"""Session management for the agent hub daemon.

This module handles session discovery, orientation, and agent registration
for OpenCode sessions.
"""

import json
import random
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, cast

from opencode_agent_hub.config import (
    AGENTS_DIR,
    COORDINATOR_SESSION_ID,
    DAEMON_START_TIME_MS,
    OPENCODE_DB_PATH,
    ORIENTATION_RETRY_DELAY,
    ORIENTATION_RETRY_MAX,
    SESSION_CACHE_TTL,
    _sessions_cache,
    _sessions_cache_time,
    log,
)
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.persistence import (
    atomic_write_json,
    is_agent_active,
    save_oriented_sessions,
    save_session_agents,
)

# Session cache lock
_sessions_cache_lock = threading.Lock()


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
        # Use WAL mode and read-only to avoid interfering with OpenCode processes
        conn = sqlite3.connect(f"file:{OPENCODE_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, slug, project_id, directory, title, version,"
                " time_created, time_updated FROM session"
                " WHERE time_archived IS NULL"
                " ORDER BY time_updated DESC"
            ).fetchall()

            sessions: list[dict[str, Any]] = []
            for row in rows:
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


def invalidate_session_cache() -> None:
    """Force cache refresh on next get_sessions() call."""
    global _sessions_cache_time
    with _sessions_cache_lock:
        _sessions_cache_time = 0


def load_opencode_session(path: Path) -> dict[str, Any] | None:
    """Load an OpenCode session file."""
    try:
        return cast(dict[str, Any], json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load session {path}: {e}")
        return None


def find_agent_for_directory(
    directory: str, agents: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Find registered agent matching a directory/projectPath."""
    for agent in agents.values():
        if agent.get("projectPath") == directory:
            return agent
    return None


def get_or_create_agent_for_directory(
    directory: str, agents: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Find or auto-create an agent for a directory.

    If no agent is registered for this directory, creates one automatically
    based on the directory name.
    """
    # Check for existing agent
    existing = find_agent_for_directory(directory, agents)
    if existing:
        return existing

    # Auto-create agent from directory
    dir_name = Path(directory).name or "root"
    agent_id = dir_name.lower().replace(" ", "-").replace("_", "-")

    # Handle conflicts by appending parent dir
    if agent_id in agents:
        parent = Path(directory).parent.name
        agent_id = f"{parent}-{agent_id}".lower().replace(" ", "-")

    agent = {
        "id": agent_id,
        "projectPath": directory,
        "role": f"Auto-registered agent for {directory}",
        "capabilities": [],
        "collaboratesWith": [],
        "lastSeen": int(time.time() * 1000),
        "status": "active",
        "autoCreated": True,
    }

    # Save to disk (atomic write to prevent readers seeing partial files)
    agent_file = AGENTS_DIR / f"{agent_id}.json"
    try:
        atomic_write_json(agent_file, agent, indent=2)
        agents[agent_id] = agent
        metrics.inc("agent_hub_agents_auto_created_total")
        metrics.set_gauge("agent_hub_active_agents", len(agents))
        log.info(f"Auto-registered agent '{agent_id}' for {directory}")
    except OSError as e:
        log.error(f"Failed to save auto-created agent: {e}")

    return agent


def generate_agent_id_for_session(session: dict[str, Any]) -> str:
    """Generate a unique pseudorandom agent ID for a session.

    Creates a human-readable but pseudorandom identifier (e.g., "cosmic-panda")
    rather than using the session slug directly. This ensures agents get unique
    pseudorandom names even when sessions are named after models ("kimi", "gpt").
    """
    # Adjectives and nouns for human-readable IDs
    ADJECTIVES = [
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
    NOUNS = [
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

    # Generate a unique pseudorandom ID
    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    # Add a short random suffix for uniqueness (4 hex chars = 65536 combinations)
    suffix = secrets.token_hex(2)
    return f"{adj}-{noun}-{suffix}"


def get_session_mapping(
    session: dict[str, Any], agents: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Find existing agent mapping for a session.

    Returns the agent dict if an agent has registered for this session,
    otherwise returns None. Agents register themselves via MCP, so we
    just look up existing mappings here.
    """
    from opencode_agent_hub.config import SESSION_AGENTS

    session_id = cast(str, session.get("id", ""))
    directory = cast(str, session.get("directory", ""))

    # Check if we already have a mapping for this session
    if session_id in SESSION_AGENTS:
        agent_id = SESSION_AGENTS[session_id]["agentId"]
        if agent_id in agents:
            return agents[agent_id]

    # Check if any existing agent has this sessionId
    for agent in agents.values():
        if agent.get("sessionId") == session_id:
            # Update our mapping
            SESSION_AGENTS[session_id] = {
                "agentId": agent["id"],
                "directory": directory,
                "slug": session.get("slug"),
            }
            save_session_agents()
            return agent

    return None


def register_session_agent(
    session_id: str, agent_id: str, agents: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Register an agent that has self-registered via MCP.

    Called when a new agent file is detected. If the agent is associated
    with a session we've oriented, update mappings and notify coordinator.
    """
    from opencode_agent_hub.config import SESSION_AGENTS
    from opencode_agent_hub.coordinator import notify_coordinator_new_agent

    if agent_id not in agents:
        return None

    agent = agents[agent_id]
    agent["sessionId"] = session_id

    # Update session-to-agent mapping
    directory = agent.get("projectPath", "")
    SESSION_AGENTS[session_id] = {
        "agentId": agent_id,
        "directory": directory,
        "slug": None,
    }
    save_session_agents()

    # Notify coordinator of new agent
    notify_coordinator_new_agent(agent_id, directory)

    log.info(f"Registered agent '{agent_id}' for session {session_id[:12]}")
    return agent


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


def find_sessions_for_agent(agent: dict, sessions: list[dict]) -> list[dict]:
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


def format_orientation(all_agents: dict[str, dict[str, Any]]) -> str:
    """Format orientation message for a newly detected agent session.

    Provides generic orientation that works for any session. The agent
    will register itself via MCP with its own chosen pseudorandom name.
    """
    # List active agents
    active_agents = [aid for aid, a in all_agents.items() if is_agent_active(a)]

    parts = ["Agent hub connected."]

    if active_agents:
        agents_str = ", ".join(active_agents[:5])
        if len(active_agents) > 5:
            agents_str += f" (+{len(active_agents) - 5} more)"
        parts.append(f"Active agents: {agents_str}")

    parts.append("Tools: agent-hub_send_message, agent-hub_sync")

    # Add registration instruction - agent chooses its own ID
    parts.append(
        'Register with: agent-hub_register_agent(id="<choose-your-own-name>", '
        'projectPath="<your-directory>", role="<your-role>")'
    )

    return " | ".join(parts)


def orient_session(session_id: str, directory: str, all_agents: dict[str, dict[str, Any]]) -> bool:
    """Inject orientation message into a session.

    Does NOT auto-create an agent - agents register themselves via MCP.
    Just injects orientation so the session knows hub is available.
    """
    from opencode_agent_hub.config import (  # noqa: I001
        COORDINATOR_SESSION_ID,
        ORIENTED_SESSIONS,
    )
    from opencode_agent_hub.coordinator import session_has_blocking_permissions
    from opencode_agent_hub.messaging import inject_message

    if not session_id:
        log.warning("orient_session called with empty session_id")
        return False

    if session_id in ORIENTED_SESSIONS:
        log.debug(f"Session {session_id[:8]} already in ORIENTED_SESSIONS, skipping")
        return False  # Already oriented

    # Fetch session details and check for blocking permissions
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
        ORIENTED_SESSIONS.add(session_id)
        save_oriented_sessions()
        return True

    # Inject orientation message
    orientation = format_orientation(all_agents)
    log.info(f"Injecting orientation into session {session_id[:8]} at {directory}")
    log.debug(f"Orientation message: {orientation[:100]}...")

    try:
        inject_message(session_id, orientation)
        log.info(f"Successfully injected orientation into session {session_id[:8]}")
    except Exception as e:
        log.error(f"Failed to inject orientation into session {session_id[:8]}: {e}")
        return False

    # Track that this session was oriented (pending agent registration)
    ORIENTED_SESSIONS.add(session_id)
    save_oriented_sessions()
    metrics.inc("agent_hub_sessions_oriented_total")
    metrics.set_gauge("agent_hub_oriented_sessions", len(ORIENTED_SESSIONS))

    log.info(f"Oriented session {session_id[:8]} at {directory}")
    return True


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

    for session_id, pending in ORIENTATION_PENDING.items():
        agent_id = pending["agent_id"]
        oriented_at = pending["oriented_at"]

        # Check if agent has registered
        agent = agents.get(agent_id)
        if agent:
            resolved.append(session_id)
            log.debug(f"Session {session_id[:8]} agent {agent_id} registered, clearing retry")
            continue

        # Give up after max retries (agent never registered)
        retries = pending["retries"]
        elapsed = now - oriented_at
        if retries >= ORIENTATION_RETRY_MAX and elapsed >= ORIENTATION_RETRY_DELAY:
            resolved.append(session_id)
            metrics.inc("agent_hub_orientation_gave_up_total")
            log.warning(
                f"Session {session_id[:8]} never registered an agent "
                f"after {retries} retries, giving up"
            )
            continue

        # Retry orientation if enough time has passed
        if elapsed >= ORIENTATION_RETRY_DELAY and retries < ORIENTATION_RETRY_MAX:
            # Just log - we don't re-inject since agent registers itself
            pending["retries"] = retries + 1
            pending["oriented_at"] = now
            metrics.inc("agent_hub_orientation_retries_total")
            log.info(
                f"Orientation retry {pending['retries']}/{ORIENTATION_RETRY_MAX} "
                f"for session {session_id[:8]} (waiting for agent registration)"
            )

    for session_id in resolved:
        del ORIENTATION_PENDING[session_id]


def process_session_file(path: Path, agents: dict[str, dict[str, Any]]) -> None:
    """Process an OpenCode session file and orient if needed.

    Only orients sessions created AFTER the daemon started (with 60s grace period).
    Does NOT create agent files - agents register themselves via MCP.
    """
    from opencode_agent_hub.config import (  # noqa: I001
        COORDINATOR_SESSION_ID,
        ORIENTED_SESSIONS,
    )

    session = load_opencode_session(path)
    if not session:
        return

    session_id = cast(str, session.get("id", ""))
    if not session_id:
        return

    if session_id in ORIENTED_SESSIONS:
        return  # Already oriented

    # Skip coordinator session
    if COORDINATOR_SESSION_ID and session_id == COORDINATOR_SESSION_ID:
        ORIENTED_SESSIONS.add(session_id)
        return

    # Only orient sessions created AFTER daemon started (with grace period)
    created_ms = cast(int, session.get("time", {}).get("created", 0))
    updated_ms = cast(int, session.get("time", {}).get("updated", 0))
    recently_updated = updated_ms >= DAEMON_START_TIME_MS - 60000  # 60 second grace period
    created_after_start = created_ms >= DAEMON_START_TIME_MS

    if not (created_after_start or recently_updated):
        log.debug(
            f"Session {session_id[:8]} predates daemon start (created {created_ms}, updated {updated_ms}), skipping"
        )
        return

    directory = session.get("directory", "")
    if not directory:
        return

    # Just orient the session - agent will register itself via MCP
    log.info(f"File watcher: new session {session_id[:8]} at {directory}")
    orient_session(session_id, directory, agents)


def poll_active_sessions(agents: dict[str, dict[str, Any]]) -> None:
    """Poll API for active sessions and orient any new ones.

    Only considers sessions created AFTER the daemon started. This ensures:
    - Historical sessions are never spammed with orientation messages
    - Only genuinely new TUI sessions get oriented
    - Daemon restart gives a clean slate

    Sessions are oriented once and tracked in ORIENTED_SESSIONS to prevent
    repeated messaging. Agents register themselves via MCP when ready.
    """
    from opencode_agent_hub.config import (  # noqa: I001
        COORDINATOR_SESSION_ID,
        ORIENTED_SESSIONS,
    )

    sessions = get_sessions()
    if not sessions:
        log.debug("poll_active_sessions: no sessions found")
        return

    log.debug(
        f"poll_active_sessions: checking {len(sessions)} sessions (daemon started at {DAEMON_START_TIME_MS})"
    )

    for session in sessions:
        session_id = cast(str, session.get("id", ""))
        if not session_id:
            continue

        directory = session.get("directory", "")
        created_ms = cast(int, session.get("time", {}).get("created", 0))
        updated_ms = cast(int, session.get("time", {}).get("updated", 0))

        if session_id in ORIENTED_SESSIONS:
            log.debug(f"Session {session_id[:8]} already oriented, skipping")
            continue

        # Skip coordinator session
        if COORDINATOR_SESSION_ID and session_id == COORDINATOR_SESSION_ID:
            log.debug(f"Session {session_id[:8]} is coordinator, adding to oriented")
            ORIENTED_SESSIONS.add(session_id)
            continue

        # Only orient sessions created AFTER daemon started OR recently updated
        # (within 60 seconds of daemon start to handle race conditions)
        recently_updated = updated_ms >= DAEMON_START_TIME_MS - 60000  # 60 second grace period
        created_after_start = created_ms >= DAEMON_START_TIME_MS

        if not (created_after_start or recently_updated):
            log.debug(
                f"Session {session_id[:8]} predates daemon (created {created_ms}, updated {updated_ms} < {DAEMON_START_TIME_MS}), skipping"
            )
            continue

        if not directory:
            log.warning(f"Session {session_id[:8]} has no directory, skipping")
            continue

        # Just orient the session - agent will register itself via MCP
        log.info(
            f"New session {session_id[:8]} at {directory} (created {created_ms}, updated {updated_ms})"
        )
        orient_session(session_id, directory, agents)


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


# Import injection functions at the end to avoid circular imports
