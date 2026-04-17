"""Session management for the agent hub daemon.

This module handles session discovery, orientation, and agent registration
for OpenCode sessions.
"""

import hashlib
import sqlite3
import threading
import time
from typing import Any, cast

from opencode_agent_hub.config import (
    COORDINATOR_SESSION_ID,
    DAEMON_START_TIME_MS,
    OPENCODE_DB_PATH,
    SESSION_CACHE_TTL,
    _sessions_cache,
    _sessions_cache_time,
    log,
)
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.persistence import (
    is_agent_active,
    save_oriented_sessions,
)

# Session cache lock
_sessions_cache_lock = threading.Lock()

# Adjectives and nouns for human-readable agent IDs
_ADJECTIVES = [
    "happy",
    "brave",
    "clever",
    "swift",
    "bright",
    "calm",
    "eager",
    "fancy",
    "gentle",
    "jolly",
    "kind",
    "lively",
    "merry",
    "noble",
    "polite",
    "proud",
    "quick",
    "quiet",
    "silly",
    "sleepy",
    "smart",
    "strong",
    "sweet",
    "tidy",
    "warm",
    "wise",
    "witty",
    "zany",
    "azure",
    "cosmic",
    "crimson",
    "golden",
    "rustic",
    "sunny",
    "vivid",
    "wild",
    "ancient",
    "autumn",
    "blazing",
    "crystal",
    "distant",
    "electric",
    "frozen",
    "hidden",
    "infinite",
    "lucky",
    "mystic",
    "neon",
    "radiant",
    "silent",
    "stellar",
    "thunder",
    "vibrant",
]

_NOUNS = [
    "panda",
    "tiger",
    "eagle",
    "falcon",
    "wolf",
    "bear",
    "lynx",
    "hawk",
    "fox",
    "owl",
    "lion",
    "dragon",
    "phoenix",
    "raven",
    "stag",
    "orca",
    "cobra",
    "viper",
    "badger",
    "moose",
    "elk",
    "bison",
    "crane",
    "heron",
    "ibis",
    "koala",
    "lemur",
    "puma",
    "quail",
    "robin",
    "shark",
    "turtle",
    "unicorn",
    "vulture",
    "walrus",
    "yak",
    "zebra",
    "comet",
    "nebula",
    "quasar",
    "asteroid",
    "eclipse",
    "galaxy",
    "meteor",
    "orbit",
    "planet",
    "pulsar",
    "quark",
    "rocket",
    "satellite",
    "star",
    "void",
    "wave",
    "zenith",
]


def _execute_session_query(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Execute session query, handling schemas with or without 'deleted' column."""
    try:
        # Try with WHERE clause first (newer schemas)
        return conn.execute(
            "SELECT id, slug, project_id, directory, title, version,"
            "       time_created, time_updated, time_archived"
            "  FROM session"
            " WHERE COALESCE(deleted, 0) = 0"
            " ORDER BY time_updated DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        # Fallback: no WHERE clause (older schemas without deleted column)
        return conn.execute(
            "SELECT id, slug, project_id, directory, title, version,"
            "       time_created, time_updated, time_archived"
            "  FROM session"
            " ORDER BY time_updated DESC"
        ).fetchall()


def get_session_agent(session_id: str) -> str | None:
    """Detect a session's active agent from its first user message.

    OpenCode stores the agent name (e.g. "gpt", "minimax", "kimi") in the
    message data JSON.  We read the **first user message** (ordered by
    time_created ASC) because:

    1. The hub server auto-creates assistant messages with agent='claude'
       when a session is created — even if the user started the session
       with ``opencode --agent gpt``.  Querying assistant messages would
       return the wrong agent.

    2. The user's very first message always carries the correct agent set
       by the TUI (reflecting their ``--agent`` flag).  Daemon-injected
       messages arrive later during orientation polling, so the earliest
       user message is the real one.

    Returns the agent name, or None if no user messages exist yet.
    """
    if not OPENCODE_DB_PATH.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{OPENCODE_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT json_extract(m.data, '$.agent') as agent"
                "  FROM message m"
                " WHERE m.session_id = ?"
                "   AND json_extract(m.data, '$.role') = 'user'"
                "   AND json_extract(m.data, '$.agent') IS NOT NULL"
                " ORDER BY m.time_created ASC"
                " LIMIT 1",
                (session_id,),
            ).fetchone()
            if row and row[0]:
                return cast(str, row[0])
        finally:
            conn.close()
    except (sqlite3.OperationalError, Exception) as e:
        log.debug(f"Failed to detect agent for session {session_id[:8]}: {e}")

    return None


def get_sessions_from_db() -> list[dict[str, Any]] | None:
    """Fetch sessions directly from OpenCode's SQLite database.

    This is the primary session discovery mechanism. The hub server's HTTP API
    only returns sessions it created or knew about at startup — it does NOT
    see sessions created by independent TUI processes. Querying the shared
    SQLite database directly sees ALL sessions regardless of which process
    created them.
    """
    if not OPENCODE_DB_PATH.exists():
        log.warning(f"OpenCode database not found: {OPENCODE_DB_PATH}")
        return None

    try:
        conn = sqlite3.connect(f"file:{OPENCODE_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        try:
            # Query sessions ordered by most recently updated
            # This gets ALL sessions from the database, not just ones
            # created by this hub server process
            rows = _execute_session_query(conn)

            sessions: list[dict[str, Any]] = []
            for row in rows:
                # Skip archived sessions (those with time_archived set)
                if row["time_archived"] is not None and row["time_archived"] != 0:
                    continue
                sessions.append(
                    {
                        "id": row["id"],
                        "slug": row["slug"],
                        "projectID": row["project_id"],
                        "directory": row["directory"],
                        "title": row["title"],
                        "version": row["version"] or "",
                        "time": {
                            "created": row["time_created"],
                            "updated": row["time_updated"],
                        },
                    }
                )

            log.debug(f"Discovered {len(sessions)} sessions from SQLite DB")
            return sessions
        finally:
            conn.close()

    except sqlite3.OperationalError as e:
        log.warning(f"SQLite session query failed (DB may be locked): {e}")
    except Exception as e:
        log.warning(f"SQLite session discovery failed: {e}")

    return None


def get_sessions_uncached() -> list[dict[str, Any]] | None:
    """Fetch active OpenCode sessions across all projects.

    Queries the shared SQLite database directly for reliable session discovery.
    The hub server HTTP API only sees sessions it manages — independent TUI
    sessions are invisible to it. SQLite sees everything.
    """
    return get_sessions_from_db()


def get_sessions() -> list[dict[str, Any]] | None:
    """Fetch sessions with caching to avoid repeated API calls."""
    global _sessions_cache, _sessions_cache_time

    now = time.time()
    with _sessions_cache_lock:
        if now - _sessions_cache_time < SESSION_CACHE_TTL and _sessions_cache is not None:
            metrics.inc("agent_hub_cache_hits_total")
            return cast(list[dict[str, Any]], _sessions_cache)

        # Cache miss or expired
        metrics.inc("agent_hub_cache_misses_total")
        sessions = get_sessions_uncached()
        if sessions is not None:  # Only update cache on success
            _sessions_cache = sessions
            _sessions_cache_time = now
        return sessions


def generate_agent_id_for_session(session: dict[str, Any]) -> str:
    """Generate a deterministic agent ID for a session.

    Uses hash of session ID to ensure the same session always gets
    the same agent ID, while different sessions get different IDs.
    Falls back to random selection if no session ID available.
    """
    import random  # nosec B311
    import secrets

    session_id = session.get("id", "")
    if not session_id:
        # Fallback: random selection with suffix (not for security purposes)
        adj = random.choice(_ADJECTIVES)  # nosec B311
        noun = random.choice(_NOUNS)  # nosec B311
        suffix = secrets.token_hex(2)
        return f"{adj}-{noun}-{suffix}"

    # Deterministic selection based on session ID hash
    hash_bytes = hashlib.md5(session_id.encode()).digest()  # nosec B324
    adj_idx = hash_bytes[0] % len(_ADJECTIVES)
    noun_idx = hash_bytes[1] % len(_NOUNS)
    return f"{_ADJECTIVES[adj_idx]}-{_NOUNS[noun_idx]}"


def find_session_for_agent(
    agent: dict[str, Any], sessions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find the session associated with an agent by session ID.

    This replaces directory-based matching with direct session ID lookup,
    enabling precise routing to specific sessions.
    """
    from opencode_agent_hub.config import SESSION_AGENTS

    agent_session_id = agent.get("sessionId")
    if not agent_session_id:
        # Fallback for legacy agents without sessionId - check SESSION_AGENTS mapping
        agent_id = agent.get("id", "")
        for sid, mapping in SESSION_AGENTS.items():
            if mapping.get("agentId") == agent_id:
                agent_session_id = sid
                break

    if not agent_session_id:
        return None

    for session in sessions:
        if session.get("id") == agent_session_id:
            return session

    return None


def find_sessions_for_agent(
    agent: dict[str, Any], sessions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find the session for an agent by session ID.

    Uses session ID-based lookup for precise routing. Each agent is now
    associated with exactly one session, enabling multiple agents to
    operate in the same working directory.

    Falls back to directory-based matching for legacy agents without sessionId.
    """
    # Primary: session ID-based lookup (new behavior)
    session = find_session_for_agent(agent, sessions)
    if session:
        return [session]

    # Fallback: directory-based matching for legacy agents
    agent_path = agent.get("projectPath", "")
    if not agent_path:
        return []

    matching = [s for s in sessions if s.get("directory") == agent_path]
    if not matching:
        return []

    # Return only the most recently updated session
    matching.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
    return [matching[0]]


def format_notification(msg: dict[str, Any], to_agent_id: str) -> str:
    """Format minimal agent-hub message notification."""
    from_agent = msg.get("from", "unknown")
    msg_type = msg.get("type", "message")
    content = msg.get("content", "")
    priority = msg.get("priority", "normal")
    thread_id = msg.get("threadId", "")

    # Build concise notification
    prefix = "URGENT: " if priority == "urgent" else ""
    header = f"[{msg_type}] from {from_agent}"
    if thread_id:
        header += f" (thread: {thread_id})"

    lines = [
        f"{prefix}{header}",
        cast(str, content),
        "",
        f'Reply: agent-hub_send_message(from="{to_agent_id}", to="{from_agent}", type="completion", content="...")',
    ]

    return "\n".join(lines)


def poll_active_sessions(
    agents: dict[str, dict[str, Any]],
    shutdown_event: threading.Event,
    poll_interval: float | None = None,
) -> None:
    """Background thread: Poll OpenCode sessions and orient new ones.

    This function runs in a background thread and periodically polls
    OpenCode sessions via the HTTP API. When a new session is discovered
    that hasn't been oriented yet, it triggers orientation.

    Agents do NOT need to proactively sync - messages are pushed to them.
    """
    from opencode_agent_hub.config import (  # noqa: I001
        ORIENTED_SESSIONS,
        SESSION_POLL_SECONDS,
    )

    if poll_interval is None:
        poll_interval = SESSION_POLL_SECONDS

    log.info(f"Session poller started ({poll_interval}s interval)")

    while not shutdown_event.is_set():
        try:
            sessions = get_sessions()
            if sessions is None:
                log.debug("Session poller: get_sessions returned None, retrying...")
                shutdown_event.wait(min(poll_interval, 5))
                continue

            for session in sessions:
                if shutdown_event.is_set():
                    break

                session_id = session.get("id", "")
                directory = session.get("directory", "")

                if not session_id or not directory:
                    continue

                # Skip coordinator session itself - it's handled specially
                if COORDINATOR_SESSION_ID and session_id == COORDINATOR_SESSION_ID:
                    continue

                # Skip sessions created before daemon started (unless marked active)
                session_time = session.get("time", {})
                created_ms = session_time.get("created", 0)
                if isinstance(created_ms, dict):
                    created_ms = created_ms.get("start", 0)

                if created_ms < DAEMON_START_TIME_MS:
                    # Skip sessions created before daemon started
                    # (Unless already oriented, e.g., if daemon was restarted)
                    continue

                # Skip already-oriented sessions
                if session_id in ORIENTED_SESSIONS:
                    continue

                # Orient this session (orient_session will add to ORIENTED_SESSIONS after success)
                orient_session(session_id, directory, agents, session=session)

        except Exception as e:
            log.error(f"Session poller error: {e}")
            import traceback

            log.debug(f"Session poller traceback: {traceback.format_exc()}")

        shutdown_event.wait(poll_interval)


def format_orientation(
    all_agents: dict[str, dict[str, Any]],
    agent_id: str | None = None,
    project_path: str | None = None,
) -> str:
    """Format orientation message for a newly detected agent session.

    Generates imperative orientation with actual values for the agent
    to execute immediately. The agent must register itself via MCP.
    """
    # List active agents
    active_agents = [aid for aid, a in all_agents.items() if is_agent_active(a)]

    parts = ["AGENT HUB: You must register now to participate in multi-agent collaboration."]

    if agent_id:
        parts.append(f"Your assigned ID: {agent_id}")
    if project_path:
        parts.append(f"Project path: {project_path}")

    parts.append("Registration required to receive messages from other agents.")

    if active_agents:
        agents_str = ", ".join(active_agents[:5])
        if len(active_agents) > 5:
            agents_str += f" (+{len(active_agents) - 5} more)"
        parts.append(f"Active agents: {agents_str}")

    # Generate imperative registration command with actual values
    if agent_id and project_path:
        parts.append(
            f'EXECUTE NOW: agent-hub_register_agent("{agent_id}", '
            f'"{project_path}", "AI agent collaborating on this project")'
        )
    else:
        parts.append(
            'EXECUTE NOW: agent-hub_register_agent("<choose-your-own-name>", '
            '"<your-project-path>", "AI agent collaborating on this project")'
        )

    parts.append("After registering, use: agent-hub_send_message, agent-hub_sync")

    return " | ".join(parts)


def orient_session(
    session_id: str,
    directory: str,
    all_agents: dict[str, dict[str, Any]],
    session: dict[str, Any] | None = None,
) -> bool:
    """Inject orientation message into a session.

    Does NOT auto-create an agent - agents register themselves via MCP.
    Just injects orientation so the session knows hub is available.
    """
    from opencode_agent_hub.config import (  # noqa: I001
        COORDINATOR_SESSION_ID,
        ORIENTED_SESSIONS,
        ORIENTED_SESSIONS_LOCK,
    )
    from opencode_agent_hub.coordinator import session_has_blocking_permissions
    from opencode_agent_hub.messaging import inject_message

    if not session_id:
        log.warning("orient_session called with empty session_id")
        return False

    # Use lock to prevent concurrent modifications
    with ORIENTED_SESSIONS_LOCK:
        # Check if already oriented (race condition protection)
        if session_id in ORIENTED_SESSIONS:
            log.debug(f"Session {session_id[:8]} already oriented, skipping")
            return False

        # Fetch session details and check for blocking permissions
        if session is None:
            sessions = get_sessions()
            session = None
            if sessions is not None:
                for s in sessions:
                    if s.get("id") == session_id:
                        session = s
                        break

        if session is None:
            log.warning(
                f"Session {session_id[:8]} not found in API, proceeding with orientation anyway"
            )
        elif session_has_blocking_permissions(session):
            log.error(
                f"Session {session_id[:8]} at {directory} has blocking permissions (question:deny) "
                "which prevents message injection. Skipping orientation."
            )
            return False

        # Skip coordinator session itself
        if COORDINATOR_SESSION_ID and session_id == COORDINATOR_SESSION_ID:
            log.debug(f"Session {session_id[:8]} is coordinator, skipping orientation")
            ORIENTED_SESSIONS.add(session_id)  # Track coordinator as "oriented"
            save_oriented_sessions()
            return True

        # Wait until the session has at least one real user message.
        # get_session_agent reads the FIRST user message's agent field,
        # which reflects the TUI user's --agent flag (e.g. "gpt").
        # Without this gate the daemon would inject before the user types
        # anything, defaulting to claude and hijacking the session's agent.
        session_agent = get_session_agent(session_id)
        if not session_agent:
            log.debug(f"Session {session_id[:8]} has no agent yet, deferring orientation")
            return False

        # Generate deterministic agent ID for this session
        session_for_id = session if session else {"id": session_id}
        agent_id = generate_agent_id_for_session(session_for_id)

        # Inject orientation message with actual values
        orientation = format_orientation(all_agents, agent_id=agent_id, project_path=directory)
        log.info(f"Injecting orientation into session {session_id[:8]} at {directory}")
        log.debug(f"Orientation message: {orientation[:100]}...")

        try:
            inject_message(session_id, orientation)
            # Add to ORIENTED_SESSIONS only after successful injection
            ORIENTED_SESSIONS.add(session_id)
            save_oriented_sessions()
        except Exception as e:
            log.error(f"Failed to orient session {session_id[:8]}: {e}")
            return False

    # Add to pending tracking for retry logic (outside lock to avoid holding it)
    from opencode_agent_hub.config import ORIENTATION_PENDING

    ORIENTATION_PENDING[session_id] = {
        "agent_id": agent_id,
        "oriented_at": time.time(),
        "retries": 0,
        "project_path": directory,
    }

    # Schedule verification callback
    import threading

    def verify_callback() -> None:
        _verify_session_processing(session_id)

    threading.Timer(5.0, verify_callback).start()

    return True


def _verify_session_processing(session_id: str, _orientation_text: str | None = None) -> None:
    """Verify that a session is processing injected messages.

    This diagnostic function checks if the session has received and responded
    to the orientation message. It logs warnings if the session appears stuck.

    Args:
        session_id: The session ID to check
    """
    import requests

    from opencode_agent_hub.config import INJECTION_TIMEOUT, OPENCODE_URL

    # Wait 2 seconds for message to be processed
    time.sleep(2)

    # Fetch session messages from the API
    try:
        resp = requests.get(
            f"{OPENCODE_URL}/session/{session_id}/message",
            timeout=INJECTION_TIMEOUT,
        )
        resp.raise_for_status()
        messages = resp.json()
        if not isinstance(messages, list):
            log.debug(f"Session {session_id[:8]}: messages API returned non-list")
            return
    except Exception as e:
        log.debug(f"Session {session_id[:8]}: failed to fetch messages for diagnostics: {e}")
        return

    # Check if the orientation message appears in the session's message history
    # Look for key text from the orientation message
    orientation_found = False
    orientation_timestamp = 0

    for msg in messages:
        parts = msg.get("parts", [])
        for part in parts:
            if part.get("type") == "text" and "AGENT HUB:" in part.get("text", ""):
                orientation_found = True
                # Handle time as either int or dict with 'start' key
                time_val = msg.get("info", {}).get("time", 0)
                if isinstance(time_val, dict):
                    orientation_timestamp = time_val.get("start", 0)
                else:
                    orientation_timestamp = time_val
                break
        if orientation_found:
            break

    if not orientation_found:
        log.warning(
            f"Session {session_id[:8]} did not receive orientation message - "
            "may have blocking permissions or disconnected UI"
        )
        return

    # Message was found - now check for assistant response
    # Wait additional 3 seconds (total 5 seconds from injection) for assistant response
    time.sleep(3)

    # Fetch messages again to check for assistant response
    try:
        resp = requests.get(
            f"{OPENCODE_URL}/session/{session_id}/message",
            timeout=INJECTION_TIMEOUT,
        )
        resp.raise_for_status()
        messages = resp.json()
        if not isinstance(messages, list):
            return
    except Exception:
        return

    # Check if there's an assistant response after the orientation message
    has_assistant_response = False
    for msg in messages:
        # Handle time as either int or dict with 'start' key
        time_val = msg.get("info", {}).get("time", 0)
        msg_time = time_val.get("start", 0) if isinstance(time_val, dict) else time_val
        if msg_time > orientation_timestamp:
            role = msg.get("info", {}).get("role", "")
            if role == "assistant":
                has_assistant_response = True
                break

    if not has_assistant_response:
        log.warning(
            f"Session {session_id[:8]} received message but is not responding - "
            "check that OpenCode UI is active"
        )
    else:
        log.debug(f"Session {session_id[:8]} is processing messages correctly")


def check_orientation_retries(agents: dict[str, dict[str, Any]]) -> None:
    """Check for agents that have registered after being oriented.

    Previously this re-injected orientation for unresponsive sessions.
    Now it just cleans up the tracking when agents register.
    """
    from opencode_agent_hub.config import (  # noqa: I001
        ORIENTATION_PENDING,
        ORIENTATION_RETRY_DELAY,
        ORIENTATION_RETRY_MAX,
    )

    if not ORIENTATION_PENDING:
        return

    now = time.time()
    resolved: list[str] = []
    retries: list[str] = []

    for session_id, pending in ORIENTATION_PENDING.items():
        agent_id = pending["agent_id"]
        oriented_at = pending["oriented_at"]
        retry_count = pending.get("retries", 0)

        # Check if agent has registered
        agent = agents.get(agent_id)
        if agent:
            resolved.append(session_id)
            log.info(
                f"Agent '{agent_id}' registered for session {session_id[:8]} "
                f"({(now - oriented_at):.1f}s after orientation)"
            )
            continue

        # Check if we should retry orientation
        if retry_count < ORIENTATION_RETRY_MAX:
            time_since = now - oriented_at
            if time_since >= ORIENTATION_RETRY_DELAY * (retry_count + 1):
                retries.append(session_id)
        else:
            # Max retries exceeded - give up
            resolved.append(session_id)
            log.warning(f"Giving up on session {session_id[:8]} after {retry_count} retries")
            metrics.inc("agent_hub_orientation_gave_up_total")

    # Clean up resolved tracking
    for session_id in resolved:
        del ORIENTATION_PENDING[session_id]

    # Retry orientation for sessions that haven't registered yet
    for session_id in retries:
        pending = ORIENTATION_PENDING[session_id]
        pending["retries"] = pending.get("retries", 0) + 1
        pending["oriented_at"] = now  # Reset timer

        agent_id = pending["agent_id"]
        project_path = pending.get("project_path", "")

        log.info(
            f"Retrying orientation for {session_id[:8]} "
            f"(attempt {pending['retries']}/{ORIENTATION_RETRY_MAX})"
        )

        metrics.inc("agent_hub_orientation_retries_total")

        # Re-inject orientation message
        try:
            from opencode_agent_hub.messaging import inject_message

            orientation = format_orientation(agents, agent_id=agent_id, project_path=project_path)
            inject_message(session_id, orientation)
        except Exception as e:
            log.error(f"Failed to retry orientation for {session_id[:8]}: {e}")
