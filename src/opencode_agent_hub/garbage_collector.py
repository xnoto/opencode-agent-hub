"""Garbage collection for the agent hub daemon.

This module handles cleanup of stale agents, sessions, messages, and threads.
"""

import json
import shutil
import time
from typing import Any

from opencode_agent_hub.config import (
    AGENT_STALE_SECONDS,
    AGENTS_DIR,
    ARCHIVE_DIR,
    COORDINATOR_SESSION_ID,
    MESSAGE_TTL_SECONDS,
    MESSAGES_DIR,
    ORIENTED_SESSIONS,
    SESSION_AGENTS,
    THREADS_DIR,
    log,
)
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.persistence import (
    archive_thread_messages,
    atomic_write_json,
    is_agent_active,
    save_oriented_sessions,
    save_session_agents,
)
from opencode_agent_hub.sessions import get_sessions
from opencode_agent_hub.utils import validate_path_within_dir


def gc_oriented_sessions() -> int:
    """Remove oriented session IDs for sessions inactive for >1 hour.

    This allows re-orientation when a user returns to an old session,
    and prevents the cache from growing unbounded.

    Returns number of sessions cleaned.
    """
    global ORIENTED_SESSIONS

    if not ORIENTED_SESSIONS:
        return 0

    # Get current sessions from API
    current_sessions = get_sessions()
    if current_sessions is None:
        return 0  # Don't clear on API failure

    now_ms = int(time.time() * 1000)
    stale_threshold_ms = MESSAGE_TTL_SECONDS * 1000  # 1 hour

    # Build set of recently active session IDs
    active_ids = set()
    for s in current_sessions:
        session_id = s.get("id", "")
        if not session_id:
            continue
        updated = s.get("time", {}).get("updated", 0)
        if now_ms - updated < stale_threshold_ms:
            active_ids.add(session_id)

    # Always keep coordinator session active regardless of update time
    if COORDINATOR_SESSION_ID:
        active_ids.add(COORDINATOR_SESSION_ID)

    # Keep only recently active sessions in oriented cache
    stale = ORIENTED_SESSIONS - active_ids
    if stale:
        ORIENTED_SESSIONS -= stale
        save_oriented_sessions()
        metrics.set_gauge("agent_hub_oriented_sessions", len(ORIENTED_SESSIONS))
        log.info(
            f"GC: Removed {len(stale)} inactive oriented sessions, {len(ORIENTED_SESSIONS)} remaining"
        )
        return len(stale)
    return 0


def gc_session_agents() -> int:
    """Remove session-agent mappings for sessions that no longer exist or are stale.

    Cleans up mappings for sessions that are either missing from the database
    or haven't been updated within the stale threshold (AGENT_STALE_SECONDS).

    Returns number of mappings cleaned.
    """
    global SESSION_AGENTS

    if not SESSION_AGENTS:
        return 0

    current_sessions = get_sessions()
    if current_sessions is None:
        return 0  # Don't clear on DB failure

    now_ms = int(time.time() * 1000)
    stale_threshold_ms = AGENT_STALE_SECONDS * 1000

    # Build set of active session IDs (exist AND updated recently)
    active_ids = set()
    for s in current_sessions:
        session_id = s.get("id", "")
        if not session_id:
            continue
        updated = s.get("time", {}).get("updated", 0)
        if now_ms - updated < stale_threshold_ms:
            active_ids.add(session_id)

    # Always keep coordinator session
    if COORDINATOR_SESSION_ID:
        active_ids.add(COORDINATOR_SESSION_ID)

    stale_session_ids = [sid for sid in SESSION_AGENTS if sid not in active_ids]

    if stale_session_ids:
        for sid in stale_session_ids:
            del SESSION_AGENTS[sid]
        save_session_agents()
        log.info(
            f"GC: Removed {len(stale_session_ids)} stale session-agent mappings, "
            f"{len(SESSION_AGENTS)} remaining"
        )
        return len(stale_session_ids)
    return 0


def run_gc(agents: dict[str, dict[str, Any]]) -> None:
    """Run garbage collection on messages, threads, stale agents, and oriented sessions.

    Agents are only removed if their associated OpenCode session no longer exists
    or is inactive. If the session is still active, the agent's lastSeen is updated
    to keep it alive.
    """
    now_ms = int(time.time() * 1000)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    agents_cleaned = 0
    messages_archived = 0

    # 0. Clean up oriented sessions - keep only sessions that still exist in API
    sessions_cleaned = gc_oriented_sessions()

    # 0.5. Clean up session-agent mappings for non-existent sessions
    gc_session_agents()

    # 1. Check for stale agents (>1hr since lastSeen) - but verify session is still alive
    if AGENTS_DIR.exists():
        # Get current sessions for health checking
        current_sessions = get_sessions()
        active_session_ids = set()
        if current_sessions is not None:
            for s in current_sessions:
                session_id = s.get("id", "")
                if not session_id:
                    continue
                updated = s.get("time", {}).get("updated", 0)
                # Session is active if updated within stale threshold
                if now_ms - updated < AGENT_STALE_SECONDS * 1000:
                    active_session_ids.add(session_id)

        # Always consider coordinator session active if it exists
        if COORDINATOR_SESSION_ID:
            active_session_ids.add(COORDINATOR_SESSION_ID)

        for agent_path in AGENTS_DIR.glob("*.json"):
            try:
                agent = json.loads(agent_path.read_text())
                last_seen = agent.get("lastSeen", 0)
                age_ms = now_ms - last_seen

                if age_ms > AGENT_STALE_SECONDS * 1000:
                    agent_id = agent.get("id", agent_path.stem)
                    session_id = agent.get("sessionId")

                    # Never remove the coordinator agent - refresh its lastSeen
                    if agent_id == "coordinator":
                        log.debug(
                            f"Refreshing coordinator agent lastSeen (age: {age_ms / 1000 / 60:.0f}m)"
                        )
                        agent["lastSeen"] = now_ms
                        atomic_write_json(agent_path, agent, indent=2)
                        agents[agent_id] = agent
                        continue

                    # Check if agent's session is still active
                    if session_id and session_id in active_session_ids:
                        # Session is still alive - update agent's lastSeen instead of removing
                        log.info(
                            f"Agent {agent_id} session {session_id[:8]} still active, "
                            f"updating lastSeen (age: {age_ms / 1000 / 60:.0f}m)"
                        )
                        agent["lastSeen"] = now_ms
                        atomic_write_json(agent_path, agent, indent=2)
                        # Update in-memory cache too
                        agents[agent_id] = agent
                    else:
                        # Session is dead or missing - remove the agent
                        agent_path.unlink()
                        # Remove from in-memory cache too
                        agents.pop(agent_id, None)
                        # Also remove from session-agent mapping
                        if session_id and session_id in SESSION_AGENTS:
                            del SESSION_AGENTS[session_id]
                        agents_cleaned += 1
                        log.info(
                            f"Removed stale agent {agent_id} (session inactive, age: {age_ms / 1000 / 60:.0f}m)"
                        )
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Failed to check agent {agent_path}: {e}")
                continue

    # Save session agents if any were cleaned
    if agents_cleaned > 0:
        save_session_agents()

    # 2. Archive expired messages (>1hr old)
    for msg_path in MESSAGES_DIR.glob("*.json"):
        try:
            msg = json.loads(msg_path.read_text())
            timestamp = msg.get("timestamp", 0)
            age_ms = now_ms - timestamp
            if age_ms > MESSAGE_TTL_SECONDS * 1000:
                dest = ARCHIVE_DIR / msg_path.name
                shutil.move(str(msg_path), str(dest))
                messages_archived += 1
                log.debug(f"Archived expired message {msg_path.name} (age: {age_ms / 1000:.0f}s)")
        except (json.JSONDecodeError, OSError):
            continue

    # 3. Check threads with all stale participants
    if THREADS_DIR.exists():
        for thread_path in THREADS_DIR.glob("*.json"):
            try:
                thread = json.loads(thread_path.read_text())
                if thread.get("status") == "resolved":
                    continue

                participants = thread.get("participants", [])
                all_stale = True
                for participant_id in participants:
                    participant_agent = agents.get(participant_id)
                    if participant_agent and is_agent_active(participant_agent):
                        all_stale = False
                        break

                if all_stale and participants:
                    log.debug(f"Thread {thread['id']} expired (all participants stale)")
                    thread["status"] = "expired"
                    thread["resolvedAt"] = now_ms
                    # Validate path before writing
                    try:
                        validate_path_within_dir(thread_path, THREADS_DIR)
                        thread_path.write_text(json.dumps(thread, indent=2))
                    except (ValueError, OSError) as e:
                        log.warning(f"Failed to update expired thread {thread['id']}: {e}")
                    archive_thread_messages(thread["id"])
            except (json.JSONDecodeError, OSError):
                continue

    # Update metrics
    metrics.inc("agent_hub_gc_runs_total")
    metrics.inc("agent_hub_gc_sessions_cleaned_total", sessions_cleaned)
    metrics.inc("agent_hub_gc_agents_cleaned_total", agents_cleaned)
    metrics.inc("agent_hub_gc_messages_archived_total", messages_archived)
    metrics.set_gauge("agent_hub_active_agents", len(agents))
