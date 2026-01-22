#!/usr/bin/env python3
# opencode-agent-hub - Multi-agent coordination daemon for OpenCode
# Copyright (C) 2025 xnoto
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "watchdog",
# ]
# ///
"""Agent Hub Daemon - Watch ~/.agent-hub/messages and inject into OpenCode sessions.

WARNING: USE AT YOUR OWN RISK. This daemon enables autonomous agent-to-agent
communication which triggers LLM API calls. The authors are not responsible
for any token usage, API costs, or other expenses incurred by running this
software. Enable rate limiting if you are concerned about runaway costs.

Features:
- Hub server management (auto-starts headless OpenCode server on port 4096)
- Session polling to detect new agents and inject orientation
- Push notifications to OpenCode sessions (always wake, always expect response)
- Thread management (auto-create, track, resolve)
- Garbage collection (expire stale messages/threads)
- Rate limiting (optional, via environment variables)
- Self-contained response instructions in every injection

Hub server:
- Daemon auto-starts `opencode serve --port 4096` if not already running
- Single hub server provides HTTP API access to ALL OpenCode sessions
- Any `opencode` TUI instance creates sessions visible via hub API
- Daemon injects messages via POST /session/{id}/message

Wake behavior: ALL messages wake agents (noReply: false)
- Agents don't need hub protocol in their definitions
- Daemon injects full response instructions with each message
- Running daemon = opt-in to coordination

Session polling:
- Polls OpenCode /session endpoint periodically
- Matches sessions to registered agents by projectPath
- Injects orientation message on first match (identifies agent, no action required)
- Tracks oriented sessions to avoid re-injection
- Agents do NOT need to proactively sync - messages are pushed to them

Thread lifecycle:
- Messages without threadId get one auto-assigned
- Threads tracked in ~/.agent-hub/threads/
- Thread resolved when owner sends type="completion" with "RESOLVED" in content
- Threads expire when all participants are stale (>1hr lastSeen)
- Messages expire after 1 hour regardless
- Stale agents (>1hr lastSeen) are removed automatically
"""

import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

# Configuration
AGENT_HUB_DIR = Path.home() / ".agent-hub"
MESSAGES_DIR = AGENT_HUB_DIR / "messages"
ARCHIVE_DIR = MESSAGES_DIR / "archive"
THREADS_DIR = AGENT_HUB_DIR / "threads"
AGENTS_DIR = AGENT_HUB_DIR / "agents"
ORIENTED_SESSIONS_FILE = AGENT_HUB_DIR / "oriented_sessions.json"
OPENCODE_DATA_DIR = Path.home() / ".local/share/opencode"
OPENCODE_SESSIONS_DIR = (
    OPENCODE_DATA_DIR / "storage/session"
)  # Watch all project subdirs, not just global
OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "4096"))
OPENCODE_URL = f"http://localhost:{OPENCODE_PORT}"
LOG_LEVEL = os.environ.get("AGENT_HUB_DAEMON_LOG_LEVEL", "INFO")

# Expiry settings
MESSAGE_TTL_SECONDS = 3600  # 1 hour
AGENT_STALE_SECONDS = 3600  # 1 hour
GC_INTERVAL_SECONDS = 60  # Run GC every 60 seconds
SESSION_POLL_SECONDS = 5  # Poll for new active sessions every 5 seconds
SESSION_CACHE_TTL = 10  # Cache sessions for 10 seconds
INJECTION_WORKERS = 4  # Concurrent injection workers
INJECTION_RETRIES = 3  # Retry failed injections
INJECTION_TIMEOUT = 5  # Shorter timeout for injections

# Rate limiting settings (disabled by default, enable via env vars)
# RATE_LIMIT_ENABLED: Enable per-agent message rate limiting
# RATE_LIMIT_MAX_MESSAGES: Max messages per agent per window (default: 10)
# RATE_LIMIT_WINDOW_SECONDS: Time window for rate limiting (default: 300 = 5 min)
# RATE_LIMIT_COOLDOWN_SECONDS: Minimum seconds between messages from same agent (default: 0)
RATE_LIMIT_ENABLED = os.environ.get("AGENT_HUB_RATE_LIMIT", "").lower() in ("1", "true", "yes")
RATE_LIMIT_MAX_MESSAGES = int(os.environ.get("AGENT_HUB_RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("AGENT_HUB_RATE_LIMIT_WINDOW", "300"))
RATE_LIMIT_COOLDOWN_SECONDS = int(os.environ.get("AGENT_HUB_RATE_LIMIT_COOLDOWN", "0"))

# Track message timestamps per agent for rate limiting
_agent_message_times: dict[str, list[float]] = {}

# Track sessions that have been oriented (session_id -> True)
ORIENTED_SESSIONS: set[str] = set()

# Session cache (avoids repeated API calls)
_sessions_cache: list[dict] = []
_sessions_cache_time: float = 0
_sessions_cache_lock = threading.Lock()


# Work queues (non-blocking handlers)
@dataclass
class InjectionTask:
    session_id: str
    text: str


@dataclass
class MessageTask:
    path: Path


@dataclass
class SessionTask:
    path: Path


_injection_queue: queue.Queue[InjectionTask] = queue.Queue()
_message_queue: queue.Queue[MessageTask] = queue.Queue()
_session_queue: queue.Queue[SessionTask] = queue.Queue()


# Prometheus-compatible metrics
class PrometheusMetrics:
    """Thread-safe Prometheus-compatible metrics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()

        # Counters (only increase)
        self._counters = {
            "agent_hub_messages_total": 0,
            "agent_hub_messages_failed_total": 0,
            "agent_hub_injections_total": 0,
            "agent_hub_injections_failed_total": 0,
            "agent_hub_injections_retried_total": 0,
            "agent_hub_sessions_oriented_total": 0,
            "agent_hub_agents_auto_created_total": 0,
            "agent_hub_cache_hits_total": 0,
            "agent_hub_cache_misses_total": 0,
            "agent_hub_gc_runs_total": 0,
            "agent_hub_gc_sessions_cleaned_total": 0,
            "agent_hub_gc_agents_cleaned_total": 0,
            "agent_hub_gc_messages_archived_total": 0,
        }

        # Gauges (can increase or decrease)
        self._gauges = {
            "agent_hub_active_agents": 0,
            "agent_hub_oriented_sessions": 0,
            "agent_hub_injection_queue_size": 0,
            "agent_hub_message_queue_size": 0,
        }

        # Metadata for metrics
        self._help = {
            "agent_hub_messages_total": "Total messages processed successfully",
            "agent_hub_messages_failed_total": "Total messages that failed processing",
            "agent_hub_injections_total": "Total message injections sent to sessions",
            "agent_hub_injections_failed_total": "Total injection failures after retries",
            "agent_hub_injections_retried_total": "Total injection retry attempts",
            "agent_hub_sessions_oriented_total": "Total sessions that received orientation",
            "agent_hub_agents_auto_created_total": "Total agents auto-created from sessions",
            "agent_hub_cache_hits_total": "Total session cache hits",
            "agent_hub_cache_misses_total": "Total session cache misses",
            "agent_hub_gc_runs_total": "Total garbage collection runs",
            "agent_hub_gc_sessions_cleaned_total": "Total stale sessions cleaned by GC",
            "agent_hub_gc_agents_cleaned_total": "Total stale agents cleaned by GC",
            "agent_hub_gc_messages_archived_total": "Total messages archived by GC",
            "agent_hub_active_agents": "Current number of registered agents",
            "agent_hub_oriented_sessions": "Current number of oriented sessions",
            "agent_hub_injection_queue_size": "Current injection queue depth",
            "agent_hub_message_queue_size": "Current message queue depth",
            "agent_hub_start_time_seconds": "Unix timestamp when daemon started",
        }

    def inc(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        with self._lock:
            if name in self._counters:
                self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = value

    def get(self, name: str) -> float:
        """Get current value of a metric."""
        with self._lock:
            if name in self._counters:
                return self._counters[name]
            if name in self._gauges:
                return self._gauges[name]
            return 0

    def to_prometheus(self) -> str:
        """Export all metrics in Prometheus text format."""
        lines = []
        with self._lock:
            # Add start time as a gauge
            lines.append(
                f"# HELP agent_hub_start_time_seconds {self._help['agent_hub_start_time_seconds']}"
            )
            lines.append("# TYPE agent_hub_start_time_seconds gauge")
            lines.append(f"agent_hub_start_time_seconds {self._start_time}")

            # Counters
            for name, value in self._counters.items():
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")

            # Gauges
            for name, value in self._gauges.items():
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {value}")

        return "\n".join(lines) + "\n"

    def log_summary(self) -> str:
        """Return a human-readable summary for logging."""
        with self._lock:
            uptime = time.time() - self._start_time
            hours, remainder = divmod(int(uptime), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = (
                f"{hours}h{minutes}m{seconds}s"
                if hours
                else f"{minutes}m{seconds}s"
                if minutes
                else f"{seconds}s"
            )

            return (
                f"uptime={uptime_str} "
                f"msgs={self._counters['agent_hub_messages_total']}/{self._counters['agent_hub_messages_failed_total']} "
                f"inj={self._counters['agent_hub_injections_total']}/{self._counters['agent_hub_injections_failed_total']} "
                f"orient={self._counters['agent_hub_sessions_oriented_total']} "
                f"cache={self._counters['agent_hub_cache_hits_total']}/{self._counters['agent_hub_cache_misses_total']} "
                f"gc={self._counters['agent_hub_gc_runs_total']}"
            )


metrics = PrometheusMetrics()

# Metrics file location
METRICS_FILE = AGENT_HUB_DIR / "metrics.prom"
METRICS_INTERVAL = 30  # Write metrics every 30 seconds


def load_oriented_sessions() -> set[str]:
    """Load oriented sessions from disk."""
    global ORIENTED_SESSIONS
    if ORIENTED_SESSIONS_FILE.exists():
        try:
            data = json.loads(ORIENTED_SESSIONS_FILE.read_text())
            ORIENTED_SESSIONS = set(data) if isinstance(data, list) else set()
            log.debug(f"Loaded {len(ORIENTED_SESSIONS)} oriented sessions from disk")
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load oriented sessions: {e}")
            ORIENTED_SESSIONS = set()
    return ORIENTED_SESSIONS


def save_oriented_sessions() -> None:
    """Save oriented sessions to disk."""
    try:
        AGENT_HUB_DIR.mkdir(parents=True, exist_ok=True)
        ORIENTED_SESSIONS_FILE.write_text(json.dumps(list(ORIENTED_SESSIONS)))
    except OSError as e:
        log.warning(f"Failed to save oriented sessions: {e}")


def bootstrap_oriented_sessions(agents: dict[str, dict]) -> None:
    """Mark sessions that already have matching agents as oriented.

    This prevents re-orienting sessions that were already set up.
    Sessions WITHOUT a matching agent are left for the poller to auto-register.
    """
    global ORIENTED_SESSIONS
    sessions = get_sessions()
    if not sessions:
        return

    initial_count = len(ORIENTED_SESSIONS)
    for session in sessions:
        session_id = session.get("id", "")
        directory = session.get("directory", "")
        if not session_id or not directory:
            continue
        # Only bootstrap if agent already exists for this directory
        if find_agent_for_directory(directory, agents):
            ORIENTED_SESSIONS.add(session_id)

    added = len(ORIENTED_SESSIONS) - initial_count
    if added > 0:
        save_oriented_sessions()
        log.info(f"Bootstrapped {added} existing sessions (with agents) as already oriented")


# Hub server process (launched by daemon if needed)
HUB_SERVER_PROCESS: subprocess.Popen | None = None

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# Rate Limiting
# =============================================================================


def check_rate_limit(agent_id: str) -> tuple[bool, str | None]:
    """Check if agent is within rate limits.

    Returns (allowed, rejection_reason).
    If allowed is False, rejection_reason explains why.
    """
    if not RATE_LIMIT_ENABLED:
        return True, None

    now = time.time()

    # Initialize tracking for this agent
    if agent_id not in _agent_message_times:
        _agent_message_times[agent_id] = []

    times = _agent_message_times[agent_id]

    # Check cooldown (minimum time between messages)
    if RATE_LIMIT_COOLDOWN_SECONDS > 0 and times:
        last_msg = times[-1]
        elapsed = now - last_msg
        if elapsed < RATE_LIMIT_COOLDOWN_SECONDS:
            remaining = int(RATE_LIMIT_COOLDOWN_SECONDS - elapsed)
            return False, f"Cooldown: wait {remaining}s before sending again"

    # Prune old timestamps outside the window
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    times[:] = [t for t in times if t > window_start]

    # Check rate limit
    if len(times) >= RATE_LIMIT_MAX_MESSAGES:
        return (
            False,
            f"Rate limit: max {RATE_LIMIT_MAX_MESSAGES} messages per {RATE_LIMIT_WINDOW_SECONDS}s",
        )

    return True, None


def record_message_sent(agent_id: str) -> None:
    """Record that an agent sent a message (for rate limiting)."""
    if not RATE_LIMIT_ENABLED:
        return

    now = time.time()
    if agent_id not in _agent_message_times:
        _agent_message_times[agent_id] = []
    _agent_message_times[agent_id].append(now)


# =============================================================================
# Agent Management
# =============================================================================


def load_agents() -> dict[str, dict]:
    """Load all registered agents, keyed by agent ID."""
    agents = {}
    if not AGENTS_DIR.exists():
        return agents
    for f in AGENTS_DIR.glob("*.json"):
        try:
            agent = json.loads(f.read_text())
            agents[agent["id"]] = agent
        except (json.JSONDecodeError, KeyError) as e:
            log.warning(f"Failed to load agent {f}: {e}")
    return agents


def is_agent_active(agent: dict) -> bool:
    """Check if agent has been seen within the stale threshold."""
    last_seen = agent.get("lastSeen", 0)
    age_seconds = (time.time() * 1000 - last_seen) / 1000
    return age_seconds < AGENT_STALE_SECONDS


# =============================================================================
# Thread Management
# =============================================================================


def load_thread(thread_id: str) -> dict | None:
    """Load a thread by ID."""
    path = THREADS_DIR / f"{thread_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load thread {thread_id}: {e}")
        return None


def save_thread(thread: dict) -> None:
    """Save a thread."""
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    path = THREADS_DIR / f"{thread['id']}.json"
    path.write_text(json.dumps(thread, indent=2))


def create_thread(msg: dict) -> dict:
    """Create a new thread from a message."""
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


def update_thread_participants(thread: dict, msg: dict) -> None:
    """Add new participants to a thread."""
    participants = set(thread.get("participants", []))
    participants.add(msg.get("from", "unknown"))
    to = msg.get("to", "")
    if to and to != "all":
        participants.add(to)
    thread["participants"] = list(participants)
    save_thread(thread)


def resolve_thread(thread_id: str, resolved_by: str) -> None:
    """Mark a thread as resolved and archive its messages."""
    thread = load_thread(thread_id)
    if not thread:
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


def ensure_thread_id(msg: dict, msg_path: Path) -> str:
    """Ensure message has a threadId, creating one if needed."""
    if msg.get("threadId"):
        thread_id = msg["threadId"]
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

    return msg.get("threadId", "")


def check_thread_resolution(msg: dict) -> bool:
    """Check if message resolves a thread. Returns True if resolved."""
    if msg.get("type") != "completion":
        return False

    content = msg.get("content", "").upper()
    if "RESOLVED" not in content:
        return False

    thread_id = msg.get("threadId")
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
        resolve_thread(thread_id, sender)
        return True

    return False


# =============================================================================
# Garbage Collection
# =============================================================================


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
    if not current_sessions:
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


def run_gc(agents: dict[str, dict]) -> None:
    """Run garbage collection on messages, threads, stale agents, and oriented sessions."""
    now_ms = int(time.time() * 1000)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    agents_cleaned = 0
    messages_archived = 0

    # 0. Clean up oriented sessions - keep only sessions that still exist in API
    sessions_cleaned = gc_oriented_sessions()

    # 1. Remove stale agents (>1hr since lastSeen)
    if AGENTS_DIR.exists():
        for agent_path in AGENTS_DIR.glob("*.json"):
            try:
                agent = json.loads(agent_path.read_text())
                last_seen = agent.get("lastSeen", 0)
                age_ms = now_ms - last_seen
                if age_ms > AGENT_STALE_SECONDS * 1000:
                    agent_id = agent.get("id", agent_path.stem)
                    agent_path.unlink()
                    # Remove from in-memory cache too
                    agents.pop(agent_id, None)
                    agents_cleaned += 1
                    log.info(f"Removed stale agent {agent_id} (age: {age_ms / 1000 / 60:.0f}m)")
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Failed to check agent {agent_path}: {e}")
                continue

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
                    agent = agents.get(participant_id)
                    if agent and is_agent_active(agent):
                        all_stale = False
                        break

                if all_stale and participants:
                    log.debug(f"Thread {thread['id']} expired (all participants stale)")
                    thread["status"] = "expired"
                    thread["resolvedAt"] = now_ms
                    thread_path.write_text(json.dumps(thread, indent=2))
                    archive_thread_messages(thread["id"])
            except (json.JSONDecodeError, OSError):
                continue

    # Update metrics
    metrics.inc("agent_hub_gc_runs_total")
    metrics.inc("agent_hub_gc_sessions_cleaned_total", sessions_cleaned)
    metrics.inc("agent_hub_gc_agents_cleaned_total", agents_cleaned)
    metrics.inc("agent_hub_gc_messages_archived_total", messages_archived)
    metrics.set_gauge("agent_hub_active_agents", len(agents))


# =============================================================================
# Hub Server Management
# =============================================================================


def is_hub_server_running() -> bool:
    """Check if OpenCode hub server is responding on the configured port."""
    try:
        resp = requests.get(f"{OPENCODE_URL}/session", timeout=2)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def start_hub_server() -> subprocess.Popen | None:
    """Launch OpenCode hub server in headless mode.

    The hub server provides HTTP API access to ALL OpenCode sessions,
    allowing the daemon to inject messages into any session.
    """
    global HUB_SERVER_PROCESS

    if is_hub_server_running():
        log.info(f"Hub server already running on port {OPENCODE_PORT}")
        return None

    log.info(f"Starting OpenCode hub server on port {OPENCODE_PORT}...")

    # Find opencode binary
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        log.error("opencode binary not found in PATH")
        return None

    # Launch headless server
    try:
        # Redirect stdout/stderr to log files
        # NOTE: Files intentionally not using context manager - must stay open for subprocess
        log_dir = Path.home() / ".local/share/agent-hub-daemon"
        log_dir.mkdir(parents=True, exist_ok=True)
        hub_stdout = open(log_dir / "hub-stdout.log", "a")  # noqa: SIM115
        hub_stderr = open(log_dir / "hub-stderr.log", "a")  # noqa: SIM115

        HUB_SERVER_PROCESS = subprocess.Popen(
            [opencode_bin, "serve", "--port", str(OPENCODE_PORT), "--print-logs"],
            stdout=hub_stdout,
            stderr=hub_stderr,
            start_new_session=True,  # Detach from terminal
        )

        # Wait for server to start
        for _ in range(30):  # 30 attempts, 0.5s each = 15s max
            time.sleep(0.5)
            if is_hub_server_running():
                log.info(f"Hub server started (PID {HUB_SERVER_PROCESS.pid})")
                return HUB_SERVER_PROCESS

        log.error("Hub server failed to start within timeout")
        HUB_SERVER_PROCESS.terminate()
        HUB_SERVER_PROCESS = None
        return None

    except Exception as e:
        log.error(f"Failed to start hub server: {e}")
        return None


def stop_hub_server() -> None:
    """Stop the hub server if we started it."""
    global HUB_SERVER_PROCESS

    if HUB_SERVER_PROCESS is not None:
        log.info(f"Stopping hub server (PID {HUB_SERVER_PROCESS.pid})...")
        try:
            HUB_SERVER_PROCESS.terminate()
            HUB_SERVER_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("Hub server didn't stop gracefully, killing...")
            HUB_SERVER_PROCESS.kill()
        HUB_SERVER_PROCESS = None


# =============================================================================
# OpenCode Integration
# =============================================================================


def get_sessions_uncached() -> list[dict]:
    """Fetch active OpenCode sessions (direct API call)."""
    try:
        resp = requests.get(f"{OPENCODE_URL}/session", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"Failed to fetch sessions: {e}")
        return []


def get_sessions() -> list[dict]:
    """Fetch sessions with caching to avoid repeated API calls."""
    global _sessions_cache, _sessions_cache_time

    now = time.time()
    with _sessions_cache_lock:
        if now - _sessions_cache_time < SESSION_CACHE_TTL and _sessions_cache:
            metrics.inc("agent_hub_cache_hits_total")
            return _sessions_cache

        # Cache miss or expired
        metrics.inc("agent_hub_cache_misses_total")
        sessions = get_sessions_uncached()
        if sessions:  # Only update cache on success
            _sessions_cache = sessions
            _sessions_cache_time = now
        return sessions


def invalidate_session_cache() -> None:
    """Force cache refresh on next get_sessions() call."""
    global _sessions_cache_time
    with _sessions_cache_lock:
        _sessions_cache_time = 0


def find_sessions_for_agent(agent: dict, sessions: list[dict]) -> list[dict]:
    """Find the most recent session for an agent's projectPath.

    Only returns the single most recently updated session to avoid
    spamming historical sessions with messages.
    """
    agent_path = agent.get("projectPath", "")
    matching = [s for s in sessions if s.get("directory") == agent_path]
    if not matching:
        return []

    # Return only the most recently updated session
    matching.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
    return [matching[0]]


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


def session_worker(agents: dict[str, dict], shutdown_event: threading.Event) -> None:
    """Worker thread that processes session orientation queue."""
    while not shutdown_event.is_set():
        try:
            task = _session_queue.get(timeout=1)
        except queue.Empty:
            continue

        try:
            # Small delay to ensure file is fully written
            time.sleep(0.2)
            if task.path.exists():
                process_session_file(task.path, agents)
        except Exception as e:
            log.error(f"Session worker error: {e}")
        finally:
            _session_queue.task_done()


# =============================================================================
# Session Orientation
# =============================================================================


def load_opencode_session(path: Path) -> dict | None:
    """Load an OpenCode session file."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load session {path}: {e}")
        return None


def find_agent_for_directory(directory: str, agents: dict[str, dict]) -> dict | None:
    """Find registered agent matching a directory/projectPath."""
    for agent in agents.values():
        if agent.get("projectPath") == directory:
            return agent
    return None


def get_or_create_agent_for_directory(directory: str, agents: dict[str, dict]) -> dict:
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

    # Save to disk
    agent_file = AGENTS_DIR / f"{agent_id}.json"
    try:
        agent_file.write_text(json.dumps(agent, indent=2))
        agents[agent_id] = agent
        metrics.inc("agent_hub_agents_auto_created_total")
        metrics.set_gauge("agent_hub_active_agents", len(agents))
        log.info(f"Auto-registered agent '{agent_id}' for {directory}")
    except OSError as e:
        log.error(f"Failed to save auto-created agent: {e}")

    return agent


def format_orientation(agent: dict, all_agents: dict[str, dict]) -> str:
    """Format orientation message for a newly detected agent session.

    Includes minimal communication protocol to encourage proactive yet
    concise coordination between agents.
    """
    agent_id = agent.get("id", "unknown")
    project_path = agent.get("projectPath", "unknown")

    # List other active agents (exclude self)
    other_agents = [aid for aid, a in all_agents.items() if aid != agent_id and is_agent_active(a)]
    agents_str = ", ".join(other_agents[:8])  # Cap at 8 to avoid huge lists
    if len(other_agents) > 8:
        agents_str += f" (+{len(other_agents) - 8} more)"

    lines = [
        "┌─────────────────────────────────────────────────",
        "│ 🔗 AGENT HUB - CONNECTED",
        "├─────────────────────────────────────────────────",
        f"│ You are: {agent_id}",
        f"│ Project: {project_path}",
    ]

    if other_agents:
        lines.extend(
            [
                "├─────────────────────────────────────────────────",
                f"│ Other agents: {agents_str}",
            ]
        )

    lines.extend(
        [
            "├─────────────────────────────────────────────────",
            "│ COMMUNICATION PROTOCOL (be proactive, stay minimal):",
            "│",
            "│ SEND when:",
            "│  • Starting work that affects another agent's domain",
            "│  • Blocked and need input from a specific agent",
            "│  • Completed a task requested by another agent",
            "│  • Hit a critical error others should know about",
            "│",
            "│ DO NOT send:",
            "│  • Progress updates or status checks",
            "│  • Acknowledgments ('got it', 'thanks')",
            "│  • Info already in shared files",
            "│",
            "│ Keep messages to 1-2 sentences.",
            "│ Use agent-hub tools: send_message, sync, get_hub_status",
            "└─────────────────────────────────────────────────",
        ]
    )
    return "\n".join(lines)


def get_active_session_ids() -> set[str]:
    """Get IDs of currently active OpenCode sessions from the API."""
    sessions = get_sessions()
    return {s.get("id", "") for s in sessions if s.get("id")}


def orient_session(session_id: str, agent: dict, all_agents: dict[str, dict]) -> bool:
    """Inject orientation message into a session."""
    if not session_id:
        return False

    if session_id in ORIENTED_SESSIONS:
        return False  # Already oriented

    orientation = format_orientation(agent, all_agents)
    inject_message(session_id, orientation)
    ORIENTED_SESSIONS.add(session_id)
    save_oriented_sessions()
    metrics.inc("agent_hub_sessions_oriented_total")
    metrics.set_gauge("agent_hub_oriented_sessions", len(ORIENTED_SESSIONS))
    log.info(f"Oriented session {session_id[:8]} for agent {agent.get('id')}")
    return True


def is_project_specific_session(path: Path) -> bool:
    """Check if session file is in a project-specific subdirectory (not global)."""
    # Path like: ~/.local/share/opencode/storage/session/<projectID>/ses_xxx.json
    # Project-specific if parent dir is not "global"
    return path.parent.name != "global"


def process_session_file(
    path: Path, agents: dict[str, dict], active_sessions: set[str] = None
) -> None:
    """Process an OpenCode session file and orient if needed.

    For global sessions: only orients if session is active in the API.
    For project-specific sessions: trusts the file directly (API doesn't list them).
    """
    session = load_opencode_session(path)
    if not session:
        return

    session_id = session.get("id", "")
    if not session_id:
        return

    if session_id in ORIENTED_SESSIONS:
        return  # Already oriented

    # Project-specific sessions (non-global) are trusted directly since
    # the OpenCode hub API only lists global sessions
    if not is_project_specific_session(path):
        # Global session - verify it's active in the API
        if active_sessions is None:
            active_sessions = get_active_session_ids()

        if session_id not in active_sessions:
            log.debug(f"Session {session_id[:8]} not active in API, skipping orientation")
            return
    else:
        log.debug(f"Session {session_id[:8]} is project-specific, trusting file directly")

    directory = session.get("directory", "")
    if not directory:
        return

    # Get or auto-create agent for this directory
    agent = get_or_create_agent_for_directory(directory, agents)
    orient_session(session_id, agent, agents)


def poll_active_sessions(agents: dict[str, dict]) -> None:
    """Poll API for active sessions and orient any new ones matching registered agents.

    This catches sessions that were missed by the file watcher or were already
    running when the daemon started.

    To avoid spamming historical sessions, we only consider the most recent
    session per unique directory.
    """
    sessions = get_sessions()
    if not sessions:
        return

    # Group by directory, keep only the most recent session per directory
    by_directory: dict[str, dict] = {}
    for session in sessions:
        directory = session.get("directory", "")
        if not directory:
            continue
        updated = session.get("time", {}).get("updated", 0)
        existing = by_directory.get(directory)
        if not existing or updated > existing.get("time", {}).get("updated", 0):
            by_directory[directory] = session

    # Only orient the most recent session per directory
    for session in by_directory.values():
        session_id = session.get("id", "")
        if not session_id or session_id in ORIENTED_SESSIONS:
            continue

        directory = session.get("directory", "")
        if not directory:
            continue

        # Get or auto-create agent for this directory
        agent = get_or_create_agent_for_directory(directory, agents)
        log.debug(f"Polling found new session {session_id[:8]} for {directory}")
        orient_session(session_id, agent, agents)


def format_notification(msg: dict, to_agent_id: str) -> str:
    """Format agent-hub message with full response instructions.

    For urgent tasks: Uses imperative plain-text format FIRST to trigger immediate
    action from the LLM, then provides structured response instructions.

    For other messages: Uses the standard formatted box.

    NOTE: Plain text messages at the start are more likely to trigger immediate
    LLM response than box-formatted messages, which may be interpreted as
    informational context rather than actionable requests.
    """
    from_agent = msg.get("from", "unknown")
    msg_type = msg.get("type", "message")
    content = msg.get("content", "")
    priority = msg.get("priority", "normal")
    thread_id = msg.get("threadId", "")

    # URGENT TASKS: Use plain imperative text FIRST to trigger immediate action
    # This format is more likely to wake sleeping agents than box-formatted messages
    if msg_type == "task" and priority == "urgent":
        lines = [
            f"URGENT FROM {from_agent}: {content}",
            "",
            "^^^ ACT ON THIS IMMEDIATELY. Do not wait for user input. ^^^",
            "",
            "┌─────────────────────────────────────────────────",
            "│ RESPOND using agent-hub_send_message:",
            f'│   from: "{to_agent_id}"',
            f'│   to: "{from_agent}"',
        ]
        if thread_id:
            lines.append(f'│   threadId: "{thread_id}"')
        lines.extend(
            [
                '│   type: "completion" | "context" | "question" | "error"',
                "│   content: <your response>",
                "│",
                '│ To resolve thread, include "RESOLVED:" in content.',
                "└─────────────────────────────────────────────────",
            ]
        )
        return "\n".join(lines)

    # Type icons
    type_icons = {
        "question": "❓",
        "task": "📋",
        "context": "📎",
        "completion": "✅",
        "error": "❌",
    }
    icon = type_icons.get(msg_type, "💬")

    # Priority markers
    priority_markers = {
        "urgent": "🚨 URGENT",
        "high": "⚠️ HIGH",
        "normal": "",
        "low": "💤 LOW",
    }
    priority_str = priority_markers.get(priority, "")

    # Build notification block
    lines = [
        "┌─────────────────────────────────────────────────",
        f"│ {icon} AGENT HUB MESSAGE",
        "├─────────────────────────────────────────────────",
        f"│ FROM: {from_agent}",
        f"│ TYPE: {msg_type}" + (f" {priority_str}" if priority_str else ""),
    ]
    if thread_id:
        lines.append(f"│ THREAD: {thread_id}")
    lines.extend(
        [
            "├─────────────────────────────────────────────────",
            f"│ {content}",
            "├─────────────────────────────────────────────────",
            "│ RESPOND using agent-hub_send_message:",
            f'│   from: "{to_agent_id}"',
            f'│   to: "{from_agent}"',
        ]
    )
    if thread_id:
        lines.append(f'│   threadId: "{thread_id}"')
    lines.extend(
        [
            '│   type: "completion" | "context" | "question" | "error"',
            "│   content: <your response>",
            "│",
            '│ To resolve thread, include "RESOLVED:" in content.',
            "└─────────────────────────────────────────────────",
        ]
    )

    return "\n".join(lines)


# =============================================================================
# Message Processing
# =============================================================================


def process_message_file(path: Path, agents: dict[str, dict]) -> None:
    """Process a new message file and inject if applicable."""
    try:
        msg = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to read message {path}: {e}")
        metrics.inc("agent_hub_messages_failed_total")
        return

    # Check rate limiting for sender
    sender = msg.get("from", "unknown")
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
        target_agents = [agents[to]]
    else:
        log.info(f"Unknown target agent: {to}")
        metrics.inc("agent_hub_messages_failed_total")
        return

    # Skip if already read
    if msg.get("read"):
        return

    sessions = get_sessions()
    if not sessions:
        log.info("No active sessions for message delivery")
        return

    log.info(
        f"Processing message from {msg.get('from')} to {to}, found {len(sessions)} total sessions"
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
            notification = format_notification(msg, agent["id"])
            for session in matching_sessions:
                log.info(f"Injecting message into session {session['id']} for agent {agent['id']}")
                inject_message(session["id"], notification)
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
            log.warning(f"Failed to mark message as read: {e}")
        metrics.inc("agent_hub_messages_total")
    else:
        metrics.inc("agent_hub_messages_failed_total")


# =============================================================================
# Event Handler
# =============================================================================


class MessageHandler(FileSystemEventHandler):
    """Handle new message files in ~/.agent-hub/messages/."""

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
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

    def on_created(self, event: FileCreatedEvent) -> None:
        """Only orient when a NEW session file is created."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix != ".json":
            return
        if not path.name.startswith("ses_"):
            return

        log.debug(f"New session file created: {path.name}")
        # Queue for async processing (non-blocking)
        _session_queue.put(SessionTask(path=path))


class AgentHandler(FileSystemEventHandler):
    """Handle agent registration changes to reload agents dict."""

    def __init__(self, agents: dict[str, dict]):
        self.agents = agents

    def on_created(self, event: FileCreatedEvent) -> None:
        self._reload()

    def on_modified(self, event) -> None:
        self._reload()

    def on_deleted(self, event) -> None:
        self._reload()

    def _reload(self) -> None:
        """Reload agents from disk."""
        new_agents = load_agents()
        self.agents.clear()
        self.agents.update(new_agents)
        log.debug(f"Reloaded agents: {list(self.agents.keys())}")


# =============================================================================
# Main
# =============================================================================


def main():
    # Ensure directories exist
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load persisted state
    load_oriented_sessions()

    log.info(f"Watching messages: {MESSAGES_DIR}")
    log.info(f"Watching sessions: {OPENCODE_SESSIONS_DIR}")
    log.info(f"Watching agents: {AGENTS_DIR}")
    log.info(f"OpenCode API: {OPENCODE_URL}")
    log.info("All messages wake agents with response instructions")
    log.info(f"Message TTL: {MESSAGE_TTL_SECONDS}s, GC interval: {GC_INTERVAL_SECONDS}s")

    # Start hub server if not already running
    start_hub_server()

    # Shared agents dict - updated by AgentHandler
    agents = load_agents()
    log.info(f"Loaded {len(agents)} registered agents: {list(agents.keys())}")

    # Bootstrap: mark existing sessions WITH agents as already oriented
    # Sessions without agents will be auto-registered by the poller
    bootstrap_oriented_sessions(agents)

    # Set up observers
    observer = Observer()

    # Watch messages directory
    message_handler = MessageHandler()
    observer.schedule(message_handler, str(MESSAGES_DIR), recursive=False)

    # Watch OpenCode sessions directory (if exists)
    # Watches recursively to catch both global/ and project-specific subdirectories
    # Only triggers on NEW session files - does NOT scan existing sessions on startup
    # This prevents spamming hundreds of sessions with orientation messages
    if OPENCODE_SESSIONS_DIR.exists():
        session_handler = SessionHandler()
        observer.schedule(session_handler, str(OPENCODE_SESSIONS_DIR), recursive=True)
        log.info(f"Watching for new sessions in {OPENCODE_SESSIONS_DIR} (recursive)")
    else:
        log.warning(f"Sessions directory not found: {OPENCODE_SESSIONS_DIR}")

    # Watch agents directory for registration changes
    agent_handler = AgentHandler(agents)
    observer.schedule(agent_handler, str(AGENTS_DIR), recursive=False)

    observer.start()

    # Handle signals for graceful shutdown
    def shutdown_handler(signum, frame):
        log.info(f"Received signal {signum}, shutting down...")
        observer.stop()
        stop_hub_server()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # Shutdown event for coordinating thread termination
    shutdown_event = threading.Event()

    def session_poller():
        """Background thread that polls for new active sessions."""
        while not shutdown_event.is_set():
            try:
                poll_active_sessions(agents)
            except Exception as e:
                log.error(f"Session poller error: {e}")
            shutdown_event.wait(SESSION_POLL_SECONDS)

    def gc_worker():
        """Background thread for garbage collection."""
        while not shutdown_event.is_set():
            try:
                run_gc(agents)
            except Exception as e:
                log.error(f"GC error: {e}")
            shutdown_event.wait(GC_INTERVAL_SECONDS)

    def hub_monitor():
        """Background thread to monitor hub server health."""
        while not shutdown_event.is_set():
            if HUB_SERVER_PROCESS is not None and HUB_SERVER_PROCESS.poll() is not None:
                log.warning("Hub server died, restarting...")
                start_hub_server()
            shutdown_event.wait(10)  # Check every 10 seconds

    def metrics_worker():
        """Background thread to write metrics and log summaries."""
        while not shutdown_event.is_set():
            try:
                # Update queue gauges
                metrics.set_gauge("agent_hub_injection_queue_size", _injection_queue.qsize())
                metrics.set_gauge("agent_hub_message_queue_size", _message_queue.qsize())

                # Write Prometheus metrics file
                METRICS_FILE.write_text(metrics.to_prometheus())

                # Log summary
                log.info(f"Metrics: {metrics.log_summary()}")
            except Exception as e:
                log.error(f"Metrics worker error: {e}")
            shutdown_event.wait(METRICS_INTERVAL)

    # Set initial gauge values
    metrics.set_gauge("agent_hub_active_agents", len(agents))
    metrics.set_gauge("agent_hub_oriented_sessions", len(ORIENTED_SESSIONS))

    # Start background threads
    threads = [
        threading.Thread(target=session_poller, name="session-poller", daemon=True),
        threading.Thread(target=gc_worker, name="gc-worker", daemon=True),
        threading.Thread(target=hub_monitor, name="hub-monitor", daemon=True),
        threading.Thread(target=metrics_worker, name="metrics-worker", daemon=True),
        threading.Thread(
            target=lambda: message_worker(agents, shutdown_event),
            name="message-worker",
            daemon=True,
        ),
        threading.Thread(
            target=lambda: session_worker(agents, shutdown_event),
            name="session-worker",
            daemon=True,
        ),
    ]

    # Start injection workers (pool for concurrent injections)
    for i in range(INJECTION_WORKERS):
        t = threading.Thread(
            target=lambda: injection_worker(shutdown_event),
            name=f"injection-worker-{i}",
            daemon=True,
        )
        threads.append(t)

    for t in threads:
        t.start()
    log.info(f"Started {len(threads)} background threads ({INJECTION_WORKERS} injection workers)")

    try:
        # Main thread just waits - all work happens in threads and watchdog callbacks
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        shutdown_event.set()
        observer.stop()
        # Wait for threads to finish
        for t in threads:
            t.join(timeout=2)
        stop_hub_server()
    observer.join()


if __name__ == "__main__":
    main()
