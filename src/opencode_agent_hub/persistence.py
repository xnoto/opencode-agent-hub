"""Persistence layer for the agent hub daemon.

This module handles all file-based storage operations including agents,
threads, sessions, and oriented sessions.
"""

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, cast

from opencode_agent_hub.config import (
    AGENT_HUB_DIR,
    AGENT_STALE_SECONDS,
    AGENTS_DIR,
    ARCHIVE_DIR,
    MESSAGES_DIR,
    ORIENTED_SESSIONS,
    ORIENTED_SESSIONS_FILE,
    SESSION_AGENTS,
    SESSION_AGENTS_FILE,
    THREADS_DIR,
    log,
)
from opencode_agent_hub.utils import atomic_write_json, validate_path_within_dir

# Prevents concurrent thread resolution races (two agents resolving the same thread)
_thread_resolution_lock = threading.Lock()


def load_oriented_sessions() -> set[str]:
    """Load oriented sessions from disk."""
    if not ORIENTED_SESSIONS_FILE.exists():
        return set()
    try:
        return set(json.loads(ORIENTED_SESSIONS_FILE.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load oriented sessions: {e}")
        return set()


def save_oriented_sessions() -> None:
    """Save oriented sessions to disk."""
    try:
        AGENT_HUB_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(ORIENTED_SESSIONS_FILE, list(ORIENTED_SESSIONS), indent=None)
    except OSError as e:
        log.warning(f"Failed to save oriented sessions: {e}")


def save_session_agents() -> None:
    """Save session-to-agent mapping to disk."""
    try:
        AGENT_HUB_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(SESSION_AGENTS_FILE, SESSION_AGENTS, indent=2)
    except OSError as e:
        log.warning(f"Failed to save session agents: {e}")


def load_session_agents() -> dict[str, dict[str, Any]]:
    """Load session-to-agent mapping from disk."""
    if not SESSION_AGENTS_FILE.exists():
        return {}
    try:
        return cast(dict[str, dict[str, Any]], json.loads(SESSION_AGENTS_FILE.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load session agents: {e}")
        return {}


def _load_agent_with_retry(
    path: Path, max_retries: int = 3, base_delay: float = 0.05
) -> dict[str, Any] | None:
    """Load a single agent file with retry for transient errors.

    Args:
        path: Path to agent JSON file
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (doubles each attempt)

    Returns:
        Agent dict or None if loading failed after all retries
    """
    import time

    for attempt in range(max_retries):
        try:
            content = path.read_text()
            if not content.strip():
                # File is empty - writer hasn't finished yet
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    log.debug(
                        f"Agent file {path.name} empty, retrying in {delay:.0f}ms (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                log.warning(f"Agent file {path.name} is empty after {max_retries} attempts")
                return None

            agent = cast(dict[str, Any], json.loads(content))
            if "id" not in agent:
                log.warning(f"Agent file {path.name} missing 'id' field")
                return None
            return agent

        except json.JSONDecodeError:
            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                log.debug(
                    f"Failed to parse agent {path.name}, retrying in {delay:.0f}ms (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
            else:
                log.warning(
                    f"Failed to load agent {path.name}: JSON decode error after {max_retries} attempts"
                )
                return None
        except OSError as e:
            log.warning(f"Failed to read agent {path.name}: {e}")
            return None

    return None


def load_agents() -> dict[str, dict[str, Any]]:
    """Load all registered agents, keyed by agent ID.

    Retries on JSON decode errors to handle transient file states
    when external writers are updating agent files.
    """
    agents: dict[str, dict[str, Any]] = {}
    if not AGENTS_DIR.exists():
        return agents
    for f in AGENTS_DIR.glob("*.json"):
        agent = _load_agent_with_retry(f)
        if agent:
            agents[agent["id"]] = agent
    return agents


def is_agent_active(agent: dict[str, Any]) -> bool:
    """Check if agent has been seen within the stale threshold."""
    import time

    last_seen = cast(float, agent.get("lastSeen", 0))
    age_seconds = (time.time() * 1000 - last_seen) / 1000
    return cast(bool, age_seconds < AGENT_STALE_SECONDS)


def load_thread(thread_id: str) -> dict[str, Any] | None:
    """Load a thread by ID."""
    path = THREADS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    try:
        return cast(dict[str, Any], json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load thread {thread_id}: {e}")
        return None


def save_thread(thread: dict[str, Any]) -> None:
    """Save a thread."""
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    path = THREADS_DIR / f"{thread['id']}.json"
    atomic_write_json(path, thread, indent=2)


def create_thread(msg: dict[str, Any]) -> dict[str, Any]:
    """Create a new thread from a message."""
    import time

    thread_id = msg.get("threadId") or str(uuid.uuid4())[:12]
    now = int(time.time() * 1000)

    participants = {msg.get("from", "unknown")}
    to = msg.get("to", "")
    if to and to != "all":
        participants.add(to)

    thread = {
        "id": thread_id,
        "createdBy": msg.get("from", "unknown"),
        "createdAt": now,
        "participants": list(participants),
        "status": "open",
        "resolvedBy": None,
        "resolvedAt": None,
    }
    save_thread(thread)
    return thread


def update_thread_participants(thread: dict[str, Any], msg: dict[str, Any]) -> None:
    """Add new participants to a thread."""
    participants = set(thread.get("participants", []))
    participants.add(msg.get("from", "unknown"))
    to = msg.get("to", "")
    if to and to != "all":
        participants.add(to)
    thread["participants"] = list(participants)
    save_thread(thread)


def resolve_thread(thread_id: str, resolved_by: str) -> None:
    """Mark a thread as resolved and archive its messages.

    Uses a lock to prevent concurrent resolution of the same thread
    (e.g., two agents sending completion messages simultaneously).
    """
    import time

    with _thread_resolution_lock:
        thread = load_thread(thread_id)
        if not thread:
            return

        # Skip if already resolved (concurrent resolution race)
        if thread.get("status") == "resolved":
            log.debug(f"Thread {thread_id} already resolved, skipping")
            return

        thread["status"] = "resolved"
        thread["resolvedBy"] = resolved_by
        thread["resolvedAt"] = int(time.time() * 1000)
        save_thread(thread)

        # Archive all messages in this thread
        archive_thread_messages(thread_id)
        log.info(f"Thread {thread_id} resolved by {resolved_by}")


def archive_thread_messages(thread_id: str) -> None:
    """Move all messages in a thread to archive."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for msg_path in MESSAGES_DIR.glob("*.json"):
        try:
            msg = json.loads(msg_path.read_text())
            if msg.get("threadId") == thread_id:
                dest = ARCHIVE_DIR / msg_path.name
                shutil.move(str(msg_path), str(dest))
                log.debug(f"Archived message {msg_path.name} (thread resolved)")
        except (json.JSONDecodeError, OSError):
            continue


def ensure_thread_id(msg: dict[str, Any], msg_path: Path) -> str:
    """Ensure message has a threadId, creating one if needed."""
    # Validate that msg_path is within the allowed messages directory
    try:
        validate_path_within_dir(msg_path, MESSAGES_DIR)
    except ValueError as e:
        log.error(f"Path validation failed for message file: {e}")
        return cast(str, msg.get("threadId", ""))

    if msg.get("threadId"):
        thread_id = cast(str, msg["threadId"])
        thread = load_thread(thread_id)
        if thread:
            update_thread_participants(thread, msg)
        else:
            create_thread(msg)
    else:
        # Auto-generate threadId
        thread = create_thread(msg)
        thread_id = thread["id"]
        msg["threadId"] = thread_id
        # Rewrite the message file with threadId
        msg_path.write_text(json.dumps(msg, indent=2))
        log.debug(f"Auto-assigned threadId {thread_id} to message {msg_path.name}")

    return cast(str, msg.get("threadId", ""))


def check_thread_resolution(msg: dict[str, Any]) -> bool:
    """Check if message resolves a thread. Returns True if resolved."""
    if msg.get("type") != "completion":
        return False

    content = cast(str, msg.get("content", "")).upper()
    if "RESOLVED" not in content:
        return False

    thread_id = cast(str, msg.get("threadId"))
    if not thread_id:
        return False

    thread = load_thread(thread_id)
    if not thread:
        return False

    # Check if sender is the thread owner (creator) or it's a broadcast thread
    sender = msg.get("from", "")
    is_owner = thread.get("createdBy") == sender
    is_broadcast = msg.get("to") == "all" or thread.get("createdBy") == "all"

    if is_owner or is_broadcast:
        resolve_thread(thread_id, cast(str, sender))
        return True

    return False


def remove_agent(agent_id: str) -> bool:
    """Remove an agent file from disk."""
    agent_file = AGENTS_DIR / f"{agent_id}.json"
    try:
        if agent_file.exists():
            agent_file.unlink()
            return True
    except OSError as e:
        log.warning(f"Failed to remove agent file {agent_file}: {e}")
    return False
