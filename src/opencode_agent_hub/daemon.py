#!/usr/bin/env python3
# opencode-agent-hub - Multi-agent coordination daemon for OpenCode
# Copyright (c) 2025 xnoto
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

import argparse
import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import requests
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

# =============================================================================
# Configuration
# =============================================================================
# Precedence: environment variables > config file > defaults
# Config file: ~/.config/agent-hub-daemon/config.json

# Static paths (not configurable)
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
CONFIG_DIR = Path.home() / ".config" / "agent-hub-daemon"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config_file() -> dict:
    """Load configuration from JSON file if it exists."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        # Log warning later after logging is set up
        return {}


def _get_config_value(
    env_var: str,
    config_path: list[str],
    default: str | int | bool,
    config: dict,
    type_: type = str,
) -> str | int | bool:
    """Get config value with precedence: env var > config file > default.

    Args:
        env_var: Environment variable name
        config_path: Path in config dict (e.g., ["rate_limit", "enabled"])
        default: Default value
        config: Loaded config dict
        type_: Expected type (str, int, or bool)
    """
    # Check environment variable first
    env_value = os.environ.get(env_var)
    if env_value is not None:
        if type_ is bool:
            return env_value.lower() in ("1", "true", "yes")
        elif type_ is int:
            return int(env_value)
        return env_value

    # Check config file - traverse path to get leaf value
    value: str | int | bool | dict | None = config
    for key in config_path:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default

    # If we got a dict, the path didn't reach a leaf - return default
    if isinstance(value, dict):
        return default

    # Type coercion for config file values
    if type_ is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
    elif type_ is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            return int(value)

    # Return value if it matches expected type, otherwise default
    if value is None:
        return default
    return cast("str | int | bool", value)


# Load config file once at module load
_CONFIG = _load_config_file()

# OpenCode connection
OPENCODE_PORT = int(_get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, _CONFIG, int))
OPENCODE_URL = f"http://localhost:{OPENCODE_PORT}"
LOG_LEVEL = str(_get_config_value("AGENT_HUB_DAEMON_LOG_LEVEL", ["log_level"], "INFO", _CONFIG))

# Expiry/timing settings
MESSAGE_TTL_SECONDS = int(
    _get_config_value("AGENT_HUB_MESSAGE_TTL", ["gc", "message_ttl_seconds"], 3600, _CONFIG, int)
)
AGENT_STALE_SECONDS = int(
    _get_config_value("AGENT_HUB_AGENT_STALE", ["gc", "agent_stale_seconds"], 3600, _CONFIG, int)
)
GC_INTERVAL_SECONDS = int(
    _get_config_value("AGENT_HUB_GC_INTERVAL", ["gc", "interval_seconds"], 60, _CONFIG, int)
)
SESSION_POLL_SECONDS = int(
    _get_config_value("AGENT_HUB_SESSION_POLL", ["session", "poll_seconds"], 5, _CONFIG, int)
)
SESSION_CACHE_TTL = int(
    _get_config_value("AGENT_HUB_SESSION_CACHE_TTL", ["session", "cache_ttl"], 10, _CONFIG, int)
)
INJECTION_WORKERS = int(
    _get_config_value("AGENT_HUB_INJECTION_WORKERS", ["injection", "workers"], 4, _CONFIG, int)
)
INJECTION_RETRIES = int(
    _get_config_value("AGENT_HUB_INJECTION_RETRIES", ["injection", "retries"], 3, _CONFIG, int)
)
INJECTION_TIMEOUT = int(
    _get_config_value("AGENT_HUB_INJECTION_TIMEOUT", ["injection", "timeout"], 5, _CONFIG, int)
)
METRICS_INTERVAL = int(
    _get_config_value("AGENT_HUB_METRICS_INTERVAL", ["metrics_interval"], 30, _CONFIG, int)
)

# Rate limiting settings (disabled by default)
# RATE_LIMIT_ENABLED: Enable per-agent message rate limiting
# RATE_LIMIT_MAX_MESSAGES: Max messages per agent per window (default: 10)
# RATE_LIMIT_WINDOW_SECONDS: Time window for rate limiting (default: 300 = 5 min)
# RATE_LIMIT_COOLDOWN_SECONDS: Minimum seconds between messages from same agent (default: 0)
RATE_LIMIT_ENABLED = bool(
    _get_config_value("AGENT_HUB_RATE_LIMIT", ["rate_limit", "enabled"], False, _CONFIG, bool)
)
RATE_LIMIT_MAX_MESSAGES = int(
    _get_config_value("AGENT_HUB_RATE_LIMIT_MAX", ["rate_limit", "max_messages"], 10, _CONFIG, int)
)
RATE_LIMIT_WINDOW_SECONDS = int(
    _get_config_value(
        "AGENT_HUB_RATE_LIMIT_WINDOW", ["rate_limit", "window_seconds"], 300, _CONFIG, int
    )
)
RATE_LIMIT_COOLDOWN_SECONDS = int(
    _get_config_value(
        "AGENT_HUB_RATE_LIMIT_COOLDOWN", ["rate_limit", "cooldown_seconds"], 0, _CONFIG, int
    )
)

# Coordinator settings
# The coordinator is a dedicated OpenCode session that facilitates agent collaboration
# COORDINATOR_ENABLED: Enable the coordinator agent (default: true)
# COORDINATOR_MODEL: OpenCode model for coordinator (default: opencode/claude-opus-4-5)
# COORDINATOR_DIR: Directory for coordinator session (default: ~/.agent-hub/coordinator)
# COORDINATOR_AGENTS_MD: Custom path to coordinator AGENTS.md (default: auto-detect)
COORDINATOR_ENABLED = bool(
    _get_config_value("AGENT_HUB_COORDINATOR", ["coordinator", "enabled"], True, _CONFIG, bool)
)
COORDINATOR_MODEL = str(
    _get_config_value(
        "AGENT_HUB_COORDINATOR_MODEL",
        ["coordinator", "model"],
        "opencode/claude-opus-4-5",
        _CONFIG,
    )
)
_coordinator_dir_str = str(
    _get_config_value(
        "AGENT_HUB_COORDINATOR_DIR",
        ["coordinator", "directory"],
        str(AGENT_HUB_DIR / "coordinator"),
        _CONFIG,
    )
)
COORDINATOR_DIR = Path(os.path.expanduser(_coordinator_dir_str))
_coordinator_agents_md_str = str(
    _get_config_value(
        "AGENT_HUB_COORDINATOR_AGENTS_MD",
        ["coordinator", "agents_md"],
        "",  # Empty string means auto-detect
        _CONFIG,
    )
)
# None means auto-detect, otherwise use the specified path
COORDINATOR_AGENTS_MD: Path | None = (
    Path(os.path.expanduser(_coordinator_agents_md_str)) if _coordinator_agents_md_str else None
)

# Track message timestamps per agent for rate limiting
_agent_message_times: dict[str, list[float]] = {}

# Track sessions that have been oriented (session_id -> True)
ORIENTED_SESSIONS: set[str] = set()

# Session-to-agent mapping: maps session_id to agent info
# This enables multiple sessions in the same directory to have unique agent identities
# Structure: {session_id: {"agentId": str, "directory": str, "slug": str | None}}
SESSION_AGENTS: dict[str, dict] = {}
SESSION_AGENTS_FILE = AGENT_HUB_DIR / "session_agents.json"

# Daemon start time - only orient sessions created after this
DAEMON_START_TIME_MS: int = int(time.time() * 1000)

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

    def set_gauge(self, name: str, value: int | float) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = int(value)

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


def save_oriented_sessions() -> None:
    """Save oriented sessions to disk."""
    try:
        AGENT_HUB_DIR.mkdir(parents=True, exist_ok=True)
        ORIENTED_SESSIONS_FILE.write_text(json.dumps(list(ORIENTED_SESSIONS)))
    except OSError as e:
        log.warning(f"Failed to save oriented sessions: {e}")


def save_session_agents() -> None:
    """Save session-to-agent mapping to disk."""
    try:
        AGENT_HUB_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_AGENTS_FILE.write_text(json.dumps(SESSION_AGENTS, indent=2))
    except OSError as e:
        log.warning(f"Failed to save session agents: {e}")


def load_session_agents() -> dict[str, dict]:
    """Load session-to-agent mapping from disk."""
    if not SESSION_AGENTS_FILE.exists():
        return {}
    try:
        return json.loads(SESSION_AGENTS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to load session agents: {e}")
        return {}


# Hub server process (launched by daemon if needed)
HUB_SERVER_PROCESS: subprocess.Popen | None = None

# Coordinator process (dedicated OpenCode session for facilitating collaboration)
COORDINATOR_PROCESS: subprocess.Popen | None = None
COORDINATOR_SESSION_ID: str | None = None

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


def gc_session_agents() -> int:
    """Remove session-agent mappings for sessions that no longer exist.

    This prevents the SESSION_AGENTS mapping from growing unbounded
    and ensures stale session references are cleaned up.

    Returns number of mappings cleaned.
    """
    global SESSION_AGENTS

    if not SESSION_AGENTS:
        return 0

    # Get current sessions from API
    current_sessions = get_sessions()
    if not current_sessions:
        return 0  # Don't clear on API failure

    # Build set of current session IDs
    current_ids = {s.get("id", "") for s in current_sessions if s.get("id")}

    # Find session-agent mappings for non-existent sessions
    stale_session_ids = [sid for sid in SESSION_AGENTS if sid not in current_ids]

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


def run_gc(agents: dict[str, dict]) -> None:
    """Run garbage collection on messages, threads, stale agents, and oriented sessions."""
    now_ms = int(time.time() * 1000)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    agents_cleaned = 0
    messages_archived = 0

    # 0. Clean up oriented sessions - keep only sessions that still exist in API
    sessions_cleaned = gc_oriented_sessions()

    # 0.5. Clean up session-agent mappings for non-existent sessions
    gc_session_agents()

    # 1. Remove stale agents (>1hr since lastSeen)
    if AGENTS_DIR.exists():
        for agent_path in AGENTS_DIR.glob("*.json"):
            try:
                agent = json.loads(agent_path.read_text())
                last_seen = agent.get("lastSeen", 0)
                age_ms = now_ms - last_seen
                if age_ms > AGENT_STALE_SECONDS * 1000:
                    agent_id = agent.get("id", agent_path.stem)
                    session_id = agent.get("sessionId")
                    agent_path.unlink()
                    # Remove from in-memory cache too
                    agents.pop(agent_id, None)
                    # Also remove from session-agent mapping
                    if session_id and session_id in SESSION_AGENTS:
                        del SESSION_AGENTS[session_id]
                    agents_cleaned += 1
                    log.info(f"Removed stale agent {agent_id} (age: {age_ms / 1000 / 60:.0f}m)")
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
# Preflight Checks
# =============================================================================


class PreflightError(Exception):
    """Raised when preflight checks fail."""

    pass


def check_agent_hub_mcp_configured() -> bool:
    """Verify agent-hub MCP is configured and enabled in OpenCode.

    Uses `opencode debug config` to get the resolved configuration,
    which handles all config file locations automatically.

    Returns True if configured and enabled, raises PreflightError otherwise.
    """
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        raise PreflightError(
            "opencode binary not found in PATH.\n\n"
            "To fix:\n"
            "  1. Install OpenCode: https://github.com/sst/opencode\n"
            "  2. Ensure 'opencode' is in your PATH\n"
        )

    # Get config file location for error messages
    try:
        paths_result = subprocess.run(
            [opencode_bin, "debug", "paths"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        config_path = "your OpenCode config file (run 'opencode debug paths' to find it)"
        if paths_result.returncode == 0:
            for line in paths_result.stdout.splitlines():
                # Format is: "config     /path/to/config"
                if line.lower().startswith("config"):
                    parts = line.split(None, 1)  # Split on whitespace, max 2 parts
                    if len(parts) == 2:
                        config_path = parts[1].strip() + "/opencode.json"
                    break
    except (subprocess.TimeoutExpired, OSError):
        config_path = "your OpenCode config file (run 'opencode debug paths' to find it)"

    try:
        result = subprocess.run(
            [opencode_bin, "debug", "config"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as e:
        raise PreflightError("Timed out getting OpenCode config") from e
    except OSError as e:
        raise PreflightError(f"Failed to run opencode: {e}") from e

    if result.returncode != 0:
        raise PreflightError(
            f"opencode debug config failed (exit {result.returncode}): {result.stderr}"
        )

    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise PreflightError(f"Failed to parse OpenCode config: {e}") from e

    mcp_config = config.get("mcp", {})
    agent_hub = mcp_config.get("agent-hub")

    if agent_hub is None:
        raise PreflightError(
            "agent-hub MCP is not configured in OpenCode.\n\n"
            "The daemon requires agent-hub-mcp to enable agent communication.\n\n"
            f'To fix, set "enabled": true for mcp.agent-hub in {config_path}:\n\n'
            '  "mcp": {{\n'
            '    "agent-hub": {{\n'
            "      ...\n"
            '      "enabled": true\n'
            "    }}\n"
            "  }}\n\n"
            "Then restart OpenCode.\n\n"
            "More info: https://github.com/gilbarbara/agent-hub-mcp"
        )

    if not agent_hub.get("enabled", False):
        raise PreflightError(
            "agent-hub MCP is configured but disabled.\n\n"
            f'To fix, set "enabled": true for mcp.agent-hub in {config_path}, '
            "then restart OpenCode."
        )

    log.info("Preflight: agent-hub MCP configured and enabled")
    return True


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
# Coordinator Management
# =============================================================================


def find_coordinator_agents_md_template() -> Path | None:
    """Find the AGENTS.md template for the coordinator.

    Search order:
    1. Explicit config/env var path (COORDINATOR_AGENTS_MD)
    2. ~/.config/agent-hub-daemon/AGENTS.md (user override)
    3. ~/.config/agent-hub-daemon/COORDINATOR.md (alias)
    4. Package contrib/coordinator/AGENTS.md
    5. ~/.local/share/opencode-agent-hub/coordinator/AGENTS.md
    6. /usr/local/share/opencode-agent-hub/coordinator/AGENTS.md

    Returns the first existing path, or None if no template found.
    """
    # 1. Explicit config path takes highest priority
    if COORDINATOR_AGENTS_MD is not None:
        if COORDINATOR_AGENTS_MD.exists():
            return COORDINATOR_AGENTS_MD
        else:
            log.warning(f"Configured coordinator AGENTS.md not found: {COORDINATOR_AGENTS_MD}")
            # Fall through to other locations

    # 2-3. User config directory overrides
    user_config_locations = [
        CONFIG_DIR / "AGENTS.md",
        CONFIG_DIR / "COORDINATOR.md",
    ]

    for path in user_config_locations:
        if path.exists():
            return path

    # 4-6. Package and system locations
    system_locations = [
        Path(__file__).parent.parent.parent / "contrib" / "coordinator" / "AGENTS.md",
        Path.home() / ".local/share/opencode-agent-hub/coordinator/AGENTS.md",
        Path("/usr/local/share/opencode-agent-hub/coordinator/AGENTS.md"),
    ]

    for path in system_locations:
        if path.exists():
            return path

    return None


def setup_coordinator_directory() -> bool:
    """Set up the coordinator directory with AGENTS.md.

    Copies the AGENTS.md template from the first found location,
    otherwise creates a minimal version.

    Template search order (see find_coordinator_agents_md_template):
    1. Explicit config/env var path (COORDINATOR_AGENTS_MD)
    2. ~/.config/agent-hub-daemon/AGENTS.md (user override)
    3. ~/.config/agent-hub-daemon/COORDINATOR.md (alias)
    4. Package contrib/coordinator/AGENTS.md
    5. System share locations
    """
    COORDINATOR_DIR.mkdir(parents=True, exist_ok=True)
    agents_md = COORDINATOR_DIR / "AGENTS.md"

    if agents_md.exists():
        return True

    # Find and copy template
    template = find_coordinator_agents_md_template()
    if template is not None:
        shutil.copy(template, agents_md)
        log.info(f"Copied coordinator AGENTS.md from {template}")
        return True

    # Create minimal AGENTS.md if no template found
    minimal_agents_md = """# Coordinator Agent

You are the coordinator for a multi-agent system. Your job is to facilitate collaboration.

## When You Receive "NEW_AGENT" Notification

1. Ask the new agent: "What task are you working on?"
2. Check if other agents are working on related tasks
3. If matches found, introduce them to each other

## Tools

- `agent-hub_send_message` - Send messages to agents
- `agent-hub_sync` - Get hub state

## Behavior

- Be concise
- Just facilitate introductions, don't micromanage
- Let agents coordinate directly after introduction
"""
    agents_md.write_text(minimal_agents_md)
    log.info(f"Created minimal coordinator AGENTS.md at {agents_md}")
    return True


def find_coordinator_session() -> str | None:
    """Find the coordinator's session ID from active sessions."""
    sessions = get_sessions_uncached()
    for session in sessions:
        if session.get("directory") == str(COORDINATOR_DIR):
            return session.get("id")
    return None


def start_coordinator() -> subprocess.Popen | None:
    """Start the coordinator OpenCode session.

    The coordinator is a dedicated agent that facilitates collaboration
    between worker agents by:
    - Capturing what each agent is working on
    - Matching agents with related tasks
    - Facilitating introductions
    """
    global COORDINATOR_PROCESS, COORDINATOR_SESSION_ID

    if not COORDINATOR_ENABLED:
        log.info("Coordinator disabled via AGENT_HUB_COORDINATOR=false")
        return None

    # Set up coordinator directory
    if not setup_coordinator_directory():
        log.error("Failed to set up coordinator directory")
        return None

    # Check if coordinator session already exists
    existing_session = find_coordinator_session()
    if existing_session:
        COORDINATOR_SESSION_ID = existing_session
        log.info(f"Coordinator session already exists: {existing_session[:8]}")
        return None

    log.info(f"Starting coordinator with model {COORDINATOR_MODEL}...")

    # Find opencode binary
    opencode_bin = shutil.which("opencode")
    if not opencode_bin:
        log.error("opencode binary not found in PATH")
        return None

    # Launch coordinator session
    try:
        log_dir = Path.home() / ".local/share/agent-hub-daemon"
        log_dir.mkdir(parents=True, exist_ok=True)
        coord_stdout = open(log_dir / "coordinator-stdout.log", "a")  # noqa: SIM115
        coord_stderr = open(log_dir / "coordinator-stderr.log", "a")  # noqa: SIM115

        # Start opencode in the coordinator directory with specified model
        # Using 'run' subcommand with initial prompt to start the session
        COORDINATOR_PROCESS = subprocess.Popen(
            [
                opencode_bin,
                "--model",
                COORDINATOR_MODEL,
                "--prompt",
                "You are the coordinator agent. Wait for NEW_AGENT notifications and facilitate collaboration between agents. Use agent-hub_sync to check current state.",
                str(COORDINATOR_DIR),
            ],
            stdout=coord_stdout,
            stderr=coord_stderr,
            cwd=str(COORDINATOR_DIR),
            start_new_session=True,
        )

        # Wait for session to appear
        for _ in range(30):  # 15 seconds max
            time.sleep(0.5)
            session_id = find_coordinator_session()
            if session_id:
                COORDINATOR_SESSION_ID = session_id
                log.info(
                    f"Coordinator started (PID {COORDINATOR_PROCESS.pid}, session {session_id[:8]})"
                )
                return COORDINATOR_PROCESS

        log.error("Coordinator session failed to appear within timeout")
        COORDINATOR_PROCESS.terminate()
        COORDINATOR_PROCESS = None
        return None

    except Exception as e:
        log.error(f"Failed to start coordinator: {e}")
        return None


def stop_coordinator() -> None:
    """Stop the coordinator session."""
    global COORDINATOR_PROCESS, COORDINATOR_SESSION_ID

    if COORDINATOR_PROCESS is not None:
        log.info(f"Stopping coordinator (PID {COORDINATOR_PROCESS.pid})...")
        try:
            COORDINATOR_PROCESS.terminate()
            COORDINATOR_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("Coordinator didn't stop gracefully, killing...")
            COORDINATOR_PROCESS.kill()
        COORDINATOR_PROCESS = None
        COORDINATOR_SESSION_ID = None


def notify_coordinator_new_agent(agent_id: str, directory: str) -> None:
    """Notify the coordinator that a new agent has joined.

    Injects a message into the coordinator session so it can
    reach out to the new agent and facilitate collaboration.
    """
    if not COORDINATOR_ENABLED or not COORDINATOR_SESSION_ID:
        return

    notification = f"NEW_AGENT: {agent_id} at {directory}"
    inject_message(COORDINATOR_SESSION_ID, notification)
    log.info(f"Notified coordinator of new agent: {agent_id}")


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


def generate_agent_id_for_session(session: dict) -> str:
    """Generate a unique agent ID from session metadata.

    Uses session slug if available (human-readable), otherwise falls back
    to session ID. This ensures each session gets a unique agent identity
    even when multiple sessions share the same working directory.
    """
    slug = session.get("slug")
    session_id = session.get("id", "")

    if slug:
        # Use slug as primary identifier (e.g., "cosmic-panda")
        return slug

    # Fallback to session ID (truncated for readability)
    if session_id.startswith("ses_"):
        # Use the unique portion after "ses_" prefix, truncated
        return f"session-{session_id[4:16]}"

    return f"session-{session_id[:12]}" if session_id else "unknown-session"


def get_or_create_agent_for_session(session: dict, agents: dict[str, dict]) -> dict:
    """Find or auto-create an agent for a specific session.

    Unlike get_or_create_agent_for_directory(), this creates a unique agent
    identity per session, allowing multiple TUI sessions in the same directory
    to have separate agent identities.

    The agent ID is derived from the session's slug (if available) or session ID,
    ensuring uniqueness across all sessions.
    """
    session_id = session.get("id", "")
    directory = session.get("directory", "")

    # Check if we already have a mapping for this session
    if session_id in SESSION_AGENTS:
        agent_id = SESSION_AGENTS[session_id]["agentId"]
        if agent_id in agents:
            return agents[agent_id]

    # Generate unique agent ID from session
    agent_id = generate_agent_id_for_session(session)

    # Handle conflicts by appending session ID fragment
    if agent_id in agents and agents[agent_id].get("sessionId") != session_id:
        # Different session has this slug - append uniquifier
        agent_id = f"{agent_id}-{session_id[4:12]}" if session_id.startswith("ses_") else agent_id

    agent = {
        "id": agent_id,
        "sessionId": session_id,  # Track which session this agent represents
        "projectPath": directory,  # Keep for reference/display
        "slug": session.get("slug"),
        "role": f"Session agent for {session.get('title', directory)[:50]}",
        "capabilities": [],
        "collaboratesWith": [],
        "lastSeen": int(time.time() * 1000),
        "status": "active",
        "autoCreated": True,
    }

    # Update session-to-agent mapping
    SESSION_AGENTS[session_id] = {
        "agentId": agent_id,
        "directory": directory,
        "slug": session.get("slug"),
    }
    save_session_agents()

    # Save agent to disk
    agent_file = AGENTS_DIR / f"{agent_id}.json"
    try:
        agent_file.write_text(json.dumps(agent, indent=2))
        agents[agent_id] = agent
        metrics.inc("agent_hub_agents_auto_created_total")
        metrics.set_gauge("agent_hub_active_agents", len(agents))
        log.info(f"Auto-registered session agent '{agent_id}' for session {session_id[:12]}")
    except OSError as e:
        log.error(f"Failed to save auto-created session agent: {e}")

    return agent


def find_session_for_agent(agent: dict, sessions: list[dict]) -> dict | None:
    """Find the session associated with an agent by session ID.

    This replaces directory-based matching with direct session ID lookup,
    enabling precise routing to specific sessions.
    """
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


def format_orientation(agent: dict, all_agents: dict[str, dict]) -> str:
    """Format orientation message for a newly detected agent session.

    Includes registration instructions with the session-specific agent ID.
    This ensures the agent registers with agent-hub-mcp using the unique
    ID assigned by the daemon, enabling multiple sessions in the same
    directory to have separate identities.
    """
    agent_id = agent.get("id", "unknown")
    directory = agent.get("projectPath", "")

    # List other active agents (exclude self)
    other_agents = [aid for aid, a in all_agents.items() if aid != agent_id and is_agent_active(a)]

    parts = [f"Agent hub connected. You are: {agent_id}"]

    if other_agents:
        agents_str = ", ".join(other_agents[:5])
        if len(other_agents) > 5:
            agents_str += f" (+{len(other_agents) - 5} more)"
        parts.append(f"Other agents: {agents_str}")

    parts.append("Tools: agent-hub_send_message, agent-hub_sync")

    # Add registration instruction with session-specific ID
    # This ensures agent-hub-mcp creates a unique agent entry for this session
    parts.append(
        f'Register with: agent-hub_register_agent(id="{agent_id}", '
        f'projectPath="{directory}", role="your role")'
    )

    return " | ".join(parts)


def orient_session(session_id: str, agent: dict, all_agents: dict[str, dict]) -> bool:
    """Inject orientation message into a session and notify coordinator."""
    if not session_id:
        return False

    if session_id in ORIENTED_SESSIONS:
        return False  # Already oriented

    agent_id = agent.get("id", "unknown")
    directory = agent.get("projectPath", "")

    # Skip coordinator session itself
    if directory == str(COORDINATOR_DIR):
        ORIENTED_SESSIONS.add(session_id)
        save_oriented_sessions()
        return True

    # Inject minimal orientation to the agent
    orientation = format_orientation(agent, all_agents)
    inject_message(session_id, orientation)

    # Notify coordinator of new agent (coordinator will reach out to capture task)
    notify_coordinator_new_agent(agent_id, directory)

    ORIENTED_SESSIONS.add(session_id)
    save_oriented_sessions()
    metrics.inc("agent_hub_sessions_oriented_total")
    metrics.set_gauge("agent_hub_oriented_sessions", len(ORIENTED_SESSIONS))
    log.info(f"Oriented session {session_id[:8]} for agent {agent_id}")
    return True


def process_session_file(path: Path, agents: dict[str, dict]) -> None:
    """Process an OpenCode session file and orient if needed.

    Only orients sessions created AFTER the daemon started.
    Creates a unique agent identity per session using session ID/slug.
    """
    session = load_opencode_session(path)
    if not session:
        return

    session_id = session.get("id", "")
    if not session_id:
        return

    if session_id in ORIENTED_SESSIONS:
        return  # Already oriented

    # Only orient sessions created AFTER daemon started
    created_ms = session.get("time", {}).get("created", 0)
    if created_ms < DAEMON_START_TIME_MS:
        log.debug(f"Session {session_id[:8]} created before daemon start, skipping")
        return

    directory = session.get("directory", "")
    if not directory:
        return

    # Get or auto-create agent for this session (unique per session)
    agent = get_or_create_agent_for_session(session, agents)
    log.info(f"File watcher: new session {session_id[:8]}, orienting as {agent.get('id')}")
    orient_session(session_id, agent, agents)


def poll_active_sessions(agents: dict[str, dict]) -> None:
    """Poll API for active sessions and orient any new ones.

    Only considers sessions created AFTER the daemon started. This ensures:
    - Historical sessions are never spammed with orientation messages
    - Only genuinely new TUI sessions get oriented
    - Daemon restart gives a clean slate

    Sessions are oriented once and tracked in ORIENTED_SESSIONS to prevent
    repeated messaging. Each session gets a unique agent identity based on
    its session ID/slug, allowing multiple sessions in the same directory.
    """
    sessions = get_sessions()
    if not sessions:
        return

    for session in sessions:
        session_id = session.get("id", "")
        if not session_id or session_id in ORIENTED_SESSIONS:
            continue

        # Only orient sessions created AFTER daemon started
        created_ms = session.get("time", {}).get("created", 0)
        if created_ms < DAEMON_START_TIME_MS:
            continue

        directory = session.get("directory", "")
        if not directory:
            continue

        # Get or auto-create agent for this session (unique per session)
        agent = get_or_create_agent_for_session(session, agents)
        log.info(f"New session {session_id[:8]} orienting as {agent.get('id')}")
        orient_session(session_id, agent, agents)


def format_notification(msg: dict, to_agent_id: str) -> str:
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
        content,
        "",
        f'Reply: agent-hub_send_message(from="{to_agent_id}", to="{from_agent}", type="completion", content="...")',
    ]

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
    """Handle agent registration changes to reload agents dict."""

    def __init__(self, agents: dict[str, dict]):
        self.agents = agents

    def on_created(self, event: FileSystemEvent) -> None:
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
# Service Installation
# =============================================================================

# Systemd user service template - ExecStart path is dynamically set
SYSTEMD_SERVICE_TEMPLATE = """\
# systemd user service for agent-hub-daemon
# Installed by: agent-hub-daemon --install-service

[Unit]
Description=OpenCode Agent Hub Daemon
Documentation=https://github.com/xnoto/opencode-agent-hub
After=network.target

[Service]
Type=simple
ExecStart={exec_path}
Restart=on-failure
RestartSec=5

# Environment
Environment=AGENT_HUB_DAEMON_LOG_LEVEL=INFO
Environment=OPENCODE_PORT=4096

# Logging (stdout/stderr go to journal)
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def find_daemon_executable() -> str:
    """Find the path to the agent-hub-daemon executable."""
    daemon_bin = shutil.which("agent-hub-daemon")
    if daemon_bin:
        return daemon_bin

    common_paths = [
        Path.home() / ".local/bin/agent-hub-daemon",
        Path("/usr/bin/agent-hub-daemon"),
        Path("/usr/local/bin/agent-hub-daemon"),
    ]
    for p in common_paths:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)

    return "agent-hub-daemon"


def install_systemd_service() -> bool:
    """Install and start the systemd user service (Linux only)."""
    if sys.platform != "linux":
        print("Error: --install-service is only supported on Linux.", file=sys.stderr)
        print()
        if sys.platform == "darwin":
            print("On macOS, install via Homebrew instead:")
            print("  brew install xnoto/tap/opencode-agent-hub")
            print("  brew services start opencode-agent-hub")
        else:
            print(f"Platform '{sys.platform}' is not supported for service installation.")
            print("Run the daemon manually or create a service configuration for your platform.")
        return False

    service_dir = Path.home() / ".config/systemd/user"
    service_file = service_dir / "agent-hub-daemon.service"

    exec_path = find_daemon_executable()
    log.info(f"Using executable: {exec_path}")

    try:
        service_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.error(f"Failed to create service directory: {e}")
        return False

    service_content = SYSTEMD_SERVICE_TEMPLATE.format(exec_path=exec_path)
    try:
        service_file.write_text(service_content)
        log.info(f"Wrote service file: {service_file}")
    except OSError as e:
        log.error(f"Failed to write service file: {e}")
        return False

    result = subprocess.run(
        ["systemctl", "--user", "daemon-reload"], capture_output=True, text=True
    )
    if result.returncode != 0:
        log.error(f"Failed to reload systemd: {result.stderr}")
        return False

    result = subprocess.run(
        ["systemctl", "--user", "enable", "agent-hub-daemon"], capture_output=True, text=True
    )
    if result.returncode != 0:
        log.error(f"Failed to enable service: {result.stderr}")
        return False

    result = subprocess.run(
        ["systemctl", "--user", "start", "agent-hub-daemon"], capture_output=True, text=True
    )
    if result.returncode != 0:
        log.error(f"Failed to start service: {result.stderr}")
        return False

    print("Service installed and started successfully!")
    print("\nManagement commands:")
    print("  systemctl --user status agent-hub-daemon")
    print("  systemctl --user stop agent-hub-daemon")
    print("  systemctl --user restart agent-hub-daemon")
    print("  journalctl --user -u agent-hub-daemon -f")
    print("\nTo uninstall: agent-hub-daemon --uninstall-service")
    return True


def uninstall_systemd_service() -> bool:
    """Stop, disable, and remove the systemd user service (Linux only)."""
    if sys.platform != "linux":
        print("Error: --uninstall-service is only supported on Linux.", file=sys.stderr)
        print()
        if sys.platform == "darwin":
            print("On macOS, uninstall via Homebrew:")
            print("  brew services stop opencode-agent-hub")
            print("  brew uninstall opencode-agent-hub")
        else:
            print(f"Platform '{sys.platform}' is not supported for service uninstallation.")
        return False

    service_file = Path.home() / ".config/systemd/user/agent-hub-daemon.service"

    subprocess.run(
        ["systemctl", "--user", "stop", "agent-hub-daemon"], capture_output=True, text=True
    )
    subprocess.run(
        ["systemctl", "--user", "disable", "agent-hub-daemon"], capture_output=True, text=True
    )

    if service_file.exists():
        try:
            service_file.unlink()
            log.info(f"Removed service file: {service_file}")
        except OSError as e:
            log.error(f"Failed to remove service file: {e}")
            return False

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    print("Service uninstalled successfully!")
    return True


# =============================================================================
# Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Agent Hub Daemon - Multi-agent coordination for OpenCode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  agent-hub-daemon                    # Run daemon in foreground
  agent-hub-daemon --install-service  # Install as systemd user service
  agent-hub-daemon --uninstall-service
""",
    )
    parser.add_argument(
        "--install-service", action="store_true", help="Install and start as systemd user service"
    )
    parser.add_argument(
        "--uninstall-service", action="store_true", help="Remove systemd user service"
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")

    args = parser.parse_args()

    if args.version:
        from opencode_agent_hub import __version__

        print(f"agent-hub-daemon {__version__}")
        sys.exit(0)

    if args.install_service:
        sys.exit(0 if install_systemd_service() else 1)

    if args.uninstall_service:
        sys.exit(0 if uninstall_systemd_service() else 1)

    # Preflight: verify agent-hub MCP is configured before starting
    try:
        check_agent_hub_mcp_configured()
    except PreflightError as e:
        log.error(f"Preflight check failed: {e}")
        raise SystemExit(1) from None

    # Ensure directories exist
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load persisted state
    # Fresh start: clear oriented sessions and session-agent mappings from previous runs
    # Only sessions created AFTER daemon starts will be oriented
    global ORIENTED_SESSIONS, SESSION_AGENTS, DAEMON_START_TIME_MS
    DAEMON_START_TIME_MS = int(time.time() * 1000)
    ORIENTED_SESSIONS = set()
    save_oriented_sessions()
    SESSION_AGENTS = {}
    save_session_agents()

    log.info(f"Daemon starting at {DAEMON_START_TIME_MS} - only new sessions will be oriented")
    log.info(f"Watching messages: {MESSAGES_DIR}")
    log.info(f"Watching sessions: {OPENCODE_SESSIONS_DIR}")
    log.info(f"Watching agents: {AGENTS_DIR}")
    log.info(f"OpenCode API: {OPENCODE_URL}")
    log.info(f"Message TTL: {MESSAGE_TTL_SECONDS}s, GC interval: {GC_INTERVAL_SECONDS}s")
    if COORDINATOR_ENABLED:
        log.info(f"Coordinator: enabled, model={COORDINATOR_MODEL}, dir={COORDINATOR_DIR}")
    else:
        log.info("Coordinator: disabled")

    # Start hub server if not already running
    start_hub_server()

    # Start coordinator (after hub server is ready)
    start_coordinator()

    # Shared agents dict - updated by AgentHandler
    agents = load_agents()
    log.info(f"Loaded {len(agents)} registered agents: {list(agents.keys())}")

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

    # Shutdown event for coordinating thread termination
    shutdown_event = threading.Event()

    # Handle signals for graceful shutdown
    def shutdown_handler(signum, frame):
        log.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()
        observer.stop()
        stop_coordinator()
        stop_hub_server()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

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

    def coordinator_monitor():
        """Background thread to monitor coordinator health."""
        while not shutdown_event.is_set():
            if COORDINATOR_ENABLED:
                if COORDINATOR_PROCESS is not None and COORDINATOR_PROCESS.poll() is not None:
                    log.warning("Coordinator died, restarting...")
                    start_coordinator()
                elif COORDINATOR_PROCESS is None and COORDINATOR_SESSION_ID is None:
                    # Coordinator not started yet or failed to start
                    start_coordinator()
            shutdown_event.wait(30)  # Check every 30 seconds

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
        threading.Thread(target=coordinator_monitor, name="coordinator-monitor", daemon=True),
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
        stop_coordinator()
        stop_hub_server()
    observer.join()


if __name__ == "__main__":
    main()
