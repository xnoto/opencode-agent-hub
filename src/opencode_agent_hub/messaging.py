"""Message processing and injection for the agent hub daemon.

This module handles message queuing, injection into OpenCode sessions,
and file system event handling for messages and sessions.
"""

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, cast

import requests
from watchdog.events import FileSystemEvent, FileSystemEventHandler

from opencode_agent_hub.config import (
    ARCHIVE_DIR,
    COORDINATOR_SESSION_ID,
    DAEMON_START_TIME_MS,
    INJECTION_RETRIES,
    INJECTION_TIMEOUT,
    MESSAGES_DIR,
    OPENCODE_URL,
    log,
)
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.models import InjectionTask, MessageTask, SessionTask
from opencode_agent_hub.persistence import check_thread_resolution, ensure_thread_id, load_agents
from opencode_agent_hub.rate_limiting import check_rate_limit, record_message_sent
from opencode_agent_hub.sessions import find_sessions_for_agent, format_notification, get_sessions
from opencode_agent_hub.utils import validate_path_within_dir

# Work queues (module level for handler access)
_injection_queue: queue.Queue[InjectionTask] = queue.Queue()
_message_queue: queue.Queue[MessageTask] = queue.Queue()
_session_queue: queue.Queue[SessionTask] = queue.Queue()


def get_injection_queue() -> queue.Queue[InjectionTask]:
    """Get the injection queue (for daemon initialization)."""
    return _injection_queue


def get_message_queue() -> queue.Queue[MessageTask]:
    """Get the message queue (for daemon initialization)."""
    return _message_queue


def session_worker(agents: dict[str, dict], shutdown_event: threading.Event) -> None:
    """Worker thread that processes new session files for orientation."""
    from opencode_agent_hub.sessions import orient_session

    while not shutdown_event.is_set():
        try:
            task = _session_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            # Extract session ID from filename (ses_XXXX.json)
            session_id = task.path.stem

            # Get session details from API
            sessions = get_sessions()
            if not sessions:
                continue

            session = None
            for s in sessions:
                if s.get("id") == session_id:
                    session = s
                    break

            if not session:
                log.debug(f"Session {session_id[:8]} not found in API yet, skipping")
                continue

            # Check if session was created after daemon started
            created_ms = session.get("time", {}).get("created", 0)
            if isinstance(created_ms, dict):
                created_ms = created_ms.get("start", 0)

            from opencode_agent_hub.config import DAEMON_START_TIME_MS

            if created_ms < DAEMON_START_TIME_MS:
                log.debug(f"Session {session_id[:8]} created before daemon, skipping")
                continue

            # Orient the session
            directory = session.get("directory", "")
            if directory:
                log.info(f"Orienting new session from file watcher: {session_id[:8]}")
                orient_session(session_id, directory, agents, session=session)

        except Exception as e:
            log.error(f"Session worker error: {e}")
        finally:
            _session_queue.task_done()


def inject_message_sync(session_id: str, text: str) -> bool:
    """Inject message into OpenCode session (synchronous, with retries).

    Uses /prompt_async endpoint which triggers LLM invocation even when idle.
    The /message endpoint with noReply:false only adds to context without
    actually invoking the LLM when the session is idle.
    """
    payload = {
        "parts": [{"type": "text", "text": text}],
    }

    for attempt in range(INJECTION_RETRIES):
        try:
            # Use prompt_async to actually trigger LLM invocation
            # The /message endpoint only adds to context, doesn't wake idle sessions
            resp = requests.post(
                f"{OPENCODE_URL}/session/{session_id}/prompt_async",
                json=payload,
                timeout=INJECTION_TIMEOUT,
            )
            # prompt_async returns 204 No Content on success
            if resp.status_code in (200, 204):
                log.info(f"Injected message into session {session_id[:8]}... (prompt_async)")
                metrics.inc("agent_hub_injections_total")
                return True
            else:
                log.warning(f"Injection attempt {attempt + 1} failed: {resp.status_code}")
        except requests.RequestException as e:
            log.warning(f"Injection attempt {attempt + 1} failed: {e}")

        if attempt < INJECTION_RETRIES - 1:
            metrics.inc("agent_hub_injections_retried_total")
            time.sleep(0.5 * (attempt + 1))  # Backoff

    log.error(f"Injection failed after {INJECTION_RETRIES} attempts for session {session_id[:8]}")
    metrics.inc("agent_hub_injections_failed_total")
    return False


def inject_message(session_id: str, text: str) -> None:
    """Queue message for async injection (non-blocking)."""
    _injection_queue.put(InjectionTask(session_id=session_id, text=text))


def injection_worker(shutdown_event: threading.Event) -> None:
    """Worker thread that processes injection queue."""
    while not shutdown_event.is_set():
        try:
            task = _injection_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            inject_message_sync(task.session_id, task.text)
        except Exception as e:
            log.error(f"Injection worker error: {e}")
        finally:
            _injection_queue.task_done()


def message_worker(agents: dict[str, dict], shutdown_event: threading.Event) -> None:
    """Worker thread that processes message queue."""
    while not shutdown_event.is_set():
        try:
            task = _message_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            # Small delay to ensure file is fully written
            time.sleep(0.1)
            if task.path.exists():
                process_message_file(task.path, agents)
        except Exception as e:
            log.error(f"Message worker error: {e}")
        finally:
            _message_queue.task_done()


def process_message_file(path: Path, agents: dict[str, dict[str, Any]]) -> None:
    """Process a new message file and inject if applicable."""
    # Validate that the path is within the allowed messages directory
    try:
        validate_path_within_dir(path, MESSAGES_DIR)
    except ValueError as e:
        log.error(f"Path validation failed: {e}")
        metrics.inc("agent_hub_messages_failed_total")
        return

    try:
        msg = cast(dict[str, Any], json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to read message {path}: {e}")
        metrics.inc("agent_hub_messages_failed_total")
        return

    # Check rate limiting for sender
    sender = cast(str, msg.get("from", "unknown"))
    allowed, reason = check_rate_limit(sender)
    if not allowed:
        log.warning(f"Rate limited message from {sender}: {reason}")
        metrics.inc("agent_hub_messages_failed_total")
        # Archive the rate-limited message
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        msg["rateLimited"] = True
        msg["rateLimitReason"] = reason
        path.write_text(json.dumps(msg, indent=2))
        dest = ARCHIVE_DIR / path.name
        path.rename(dest)
        return

    # Record this message for rate limiting
    record_message_sent(sender)

    # Ensure message has a threadId
    ensure_thread_id(msg, path)

    # Check if this message resolves a thread
    if check_thread_resolution(msg):
        metrics.inc("agent_hub_messages_total")
        return  # Thread resolved, messages archived

    # Determine target agent(s)
    to = msg.get("to", "")
    if to == "all":
        target_agents = list(agents.values())
    elif to in agents:
        target_agents = [agents[cast(str, to)]]
    else:
        log.info(f"Unknown target agent: {to}")
        metrics.inc("agent_hub_messages_failed_total")
        return

    # Skip if already read
    if msg.get("read"):
        return

    all_sessions = get_sessions()
    if not all_sessions:
        log.info("No active sessions for message delivery")
        return

    # Only deliver to sessions created after daemon start (plus coordinator)
    sessions = [
        s
        for s in all_sessions
        if s.get("time", {}).get("created", 0) >= DAEMON_START_TIME_MS
        or (COORDINATOR_SESSION_ID and s.get("id") == COORDINATOR_SESSION_ID)
    ]

    log.info(
        f"Processing message from {msg.get('from')} to {to}, "
        f"{len(sessions)} active sessions (of {len(all_sessions)} total)"
    )

    delivered = False
    for agent in target_agents:
        # Don't notify sender
        if agent["id"] == msg.get("from"):
            log.info(f"Skipping sender {agent['id']}")
            continue

        matching_sessions = find_sessions_for_agent(agent, sessions)
        log.info(
            f"Agent {agent['id']} (path={agent.get('projectPath')}) has {len(matching_sessions)} matching sessions"
        )
        if matching_sessions:
            notification = format_notification(msg, cast(str, agent["id"]))
            for session in matching_sessions:
                log.info(f"Injecting message into session {session['id']} for agent {agent['id']}")
                inject_message(cast(str, session["id"]), notification)
                delivered = True
        else:
            log.info(f"No session found for agent {agent['id']}")

    if delivered:
        # Mark message as read to prevent re-delivery
        msg["read"] = True
        msg["deliveredAt"] = time.time()
        try:
            path.write_text(json.dumps(msg, indent=2))
            log.info(f"Marked message {path.name} as read")
        except OSError as e:
            log.warning(f"Marked message as read failed: {e}")
        metrics.inc("agent_hub_messages_total")
    else:
        metrics.inc("agent_hub_messages_failed_total")


class MessageHandler(FileSystemEventHandler):
    """Handle new message files in ~/.agent-hub/messages/."""

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(cast(str, event.src_path))
        if path.suffix != ".json":
            return
        # Ignore archive directory
        if "archive" in path.parts:
            return

        log.info(f"New message file detected: {path.name}")
        # Queue for async processing (non-blocking)
        _message_queue.put(MessageTask(path=path))


class SessionHandler(FileSystemEventHandler):
    """Handle NEW OpenCode session files for orientation.

    Only orients on file creation, not modification.
    This prevents re-orienting existing sessions on every file update.
    """

    def on_created(self, event: FileSystemEvent) -> None:
        """Only orient when a NEW session file is created."""
        if event.is_directory:
            return
        path = Path(cast(str, event.src_path))
        if path.suffix != ".json":
            return
        if not path.name.startswith("ses_"):
            return

        log.debug(f"New session file created: {path.name}")
        # Queue for async processing (non-blocking)
        _session_queue.put(SessionTask(path=path))


class AgentHandler(FileSystemEventHandler):
    """Handle agent registration changes to reload agents dict.

    When new agent files are created (via MCP registration), reloads
    the agents dict and notifies the coordinator of new agents.
    """

    def __init__(self, agents: dict[str, dict[str, Any]]):
        self.agents = agents

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(cast(str, event.src_path))
        if path.suffix != ".json":
            return

        log.info(f"New agent registration file: {path.name}")
        self._reload()

        # Check if this is a new agent that needs coordinator notification
        self._handle_new_agent(path)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._reload()

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._reload()

    def _reload(self) -> None:
        """Reload agents from disk."""
        new_agents = load_agents()
        self.agents.clear()
        self.agents.update(new_agents)
        log.debug(f"Reloaded agents: {list(self.agents.keys())}")

    def _handle_new_agent(self, path: Path) -> None:
        """Handle a newly registered agent file.

        Loads the agent and notifies coordinator if this is a new registration.
        Rejects duplicate registrations from the same session or directory.
        """
        from opencode_agent_hub.config import SESSION_AGENTS
        from opencode_agent_hub.coordinator import notify_coordinator_new_agent
        from opencode_agent_hub.persistence import load_agents, remove_agent, save_session_agents

        try:
            agent = json.loads(path.read_text())
            agent_id = agent.get("id")
            directory = agent.get("projectPath", "")
            session_id = agent.get("sessionId", "")

            log.debug(
                f"Processing agent registration: id={agent_id}, "
                f"session={session_id[:8] if session_id else 'none'}, "
                f"dir={directory}"
            )

            if not agent_id:
                log.debug(f"Agent file {path.name} has no id, skipping")
                return

            # Skip coordinator (handled separately)
            if agent_id == "coordinator":
                log.debug("Skipping coordinator agent file")
                return

            # Check if this session already has an agent registered
            if session_id and session_id in SESSION_AGENTS:
                existing_agent = SESSION_AGENTS[session_id]
                existing_id = existing_agent.get("id")
                if existing_id != agent_id:
                    log.warning(
                        f"Session {session_id[:8]} already has agent '{existing_id}', "
                        f"rejecting duplicate registration as '{agent_id}'"
                    )
                    remove_agent(agent_id)
                    return
                else:
                    log.debug(f"Agent {agent_id} re-registered for same session {session_id[:8]}")
            elif session_id:
                # Track this session->agent mapping
                SESSION_AGENTS[session_id] = agent
                save_session_agents()
                log.debug(f"Tracked session {session_id[:8]} -> agent {agent_id}")

            # Check if this directory already has an agent registered
            all_agents = load_agents()
            for other_id, other_agent in all_agents.items():
                if other_id != agent_id and other_agent.get("projectPath") == directory:
                    log.warning(
                        f"Directory {directory} already has agent '{other_id}', "
                        f"rejecting duplicate registration as '{agent_id}'"
                    )
                    remove_agent(agent_id)
                    return

            # Notify coordinator of new agent
            notify_coordinator_new_agent(agent_id, directory)
            log.info(
                f"Registered new agent: {agent_id} (session: {session_id[:8] if session_id else 'none'}, dir: {directory})"
            )

        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to handle new agent file {path}: {e}")
