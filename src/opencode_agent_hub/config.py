"""Configuration management for the agent hub daemon.

This module handles all configuration loading with the precedence:
environment variables > config file > defaults
"""

import json
import logging
import os
import threading
import time

# For accessing package data files reliably across install methods (pip, deb, rpm, aur)
from pathlib import Path
from typing import Any, cast

# =============================================================================
# Static Paths (not configurable)
# =============================================================================

AGENT_HUB_DIR = Path.home() / ".agent-hub"
MESSAGES_DIR = AGENT_HUB_DIR / "messages"
ARCHIVE_DIR = MESSAGES_DIR / "archive"
THREADS_DIR = AGENT_HUB_DIR / "threads"
AGENTS_DIR = AGENT_HUB_DIR / "agents"
ORIENTED_SESSIONS_FILE = AGENT_HUB_DIR / "oriented_sessions.json"
SESSION_AGENTS_FILE = AGENT_HUB_DIR / "session_agents.json"
OPENCODE_DATA_DIR = Path.home() / ".local/share/opencode"
OPENCODE_DB_PATH = OPENCODE_DATA_DIR / "opencode.db"
OPENCODE_STORAGE_DIR = OPENCODE_DATA_DIR / "storage"  # Watch all project subdirs, not just global
CONFIG_DIR = Path.home() / ".config" / "agent-hub-daemon"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Coordinator directory
COORDINATOR_DIR = AGENT_HUB_DIR / "coordinator"

# Metrics and logging
METRICS_FILE = AGENT_HUB_DIR / "metrics.prom"
HUB_SERVER_PID_FILE = AGENT_HUB_DIR / "hub-server.pid"
DAEMON_LOG_DIR = Path.home() / ".local/share/agent-hub-daemon"
HUB_STDERR_LOG_FILE = DAEMON_LOG_DIR / "hub-stderr.log"


# =============================================================================
# Configuration Loading
# =============================================================================


def _load_config_file() -> dict[str, Any]:
    """Load configuration from JSON file if it exists."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return cast(dict[str, Any], json.loads(CONFIG_FILE.read_text()))
    except (json.JSONDecodeError, OSError):
        # Log warning later after logging is set up
        return {}


def _get_config_value(
    env_var: str,
    config_path: list[str],
    default: str | int | bool | float | None,
    config: dict[str, Any],
    type_: type = str,
) -> Any:
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

    # Traverse config dict path
    value = config
    for key in config_path:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default

    # Validate type
    if type_ is bool and isinstance(value, bool):
        return value
    if type_ is int and isinstance(value, int):
        return value
    if type_ is str and isinstance(value, str):
        return value

    return default


# Load config file once at module load
_CONFIG = _load_config_file()

# =============================================================================
# Hub Server Configuration
# =============================================================================

OPENCODE_PORT = _get_config_value("OPENCODE_PORT", ["hub", "port"], 4096, _CONFIG, int)
OPENCODE_URL = f"http://127.0.0.1:{OPENCODE_PORT}"

# Default model for the hub server.  Set via AGENT_HUB_MODEL env var or
# config key "hub.model".  Format: "providerID/modelID".
# Applied to the hub server via PATCH /config after startup so that
# API-created sessions use this model instead of the server's built-in
# default (claude).
HUB_MODEL = _get_config_value(
    "AGENT_HUB_MODEL", ["hub", "model"], "opencode/minimax-m2.5-free", _CONFIG, str
)

# =============================================================================
# Coordinator Configuration
# =============================================================================

COORDINATOR_ENABLED = _get_config_value(
    "AGENT_HUB_COORDINATOR", ["coordinator", "enabled"], True, _CONFIG, bool
)
COORDINATOR_AGENTS_MD = _get_config_value(
    "AGENT_HUB_COORDINATOR_AGENTS_MD",
    ["coordinator", "agents_md_path"],
    None,
    _CONFIG,
    str,
)
COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = _get_config_value(
    "AGENT_HUB_COORDINATOR_PRESERVE_LOCAL_AGENTS_MD",
    ["coordinator", "preserve_local_agents_md"],
    False,
    _CONFIG,
    bool,
)

# Convert COORDINATOR_AGENTS_MD to Path if set
if COORDINATOR_AGENTS_MD:
    COORDINATOR_AGENTS_MD = Path(COORDINATOR_AGENTS_MD)

# Coordinator settings
COORDINATOR_READY_TIMEOUT_SECONDS = _get_config_value(
    "AGENT_HUB_COORDINATOR_READY_TIMEOUT",
    ["coordinator", "ready_timeout_seconds"],
    60,
    _CONFIG,
    int,
)
COORDINATOR_BOOTSTRAP_REQUIRED = _get_config_value(
    "AGENT_HUB_COORDINATOR_BOOTSTRAP_REQUIRED",
    ["coordinator", "bootstrap_required"],
    False,  # Allow daemon to run without coordinator READY for headless operation
    _CONFIG,
    bool,
)
COORDINATOR_STRICT_READY = _get_config_value(
    "AGENT_HUB_COORDINATOR_STRICT_READY",
    ["coordinator", "strict_ready"],
    False,  # Allow any activity, not just exact "READY" text
    _CONFIG,
    bool,
)
COORDINATOR_BOOTSTRAP_PROMPT = _get_config_value(
    "AGENT_HUB_COORDINATOR_BOOTSTRAP_PROMPT",
    ["coordinator", "bootstrap_prompt"],
    "READY",
    _CONFIG,
    str,
)

# Coordinator pricing (USD per token)
PRICING_INPUT = _get_config_value(
    "AGENT_HUB_PRICING_INPUT", ["pricing", "input_per_1m"], 3.00, _CONFIG, float
)
PRICING_OUTPUT = _get_config_value(
    "AGENT_HUB_PRICING_OUTPUT", ["pricing", "output_per_1m"], 15.00, _CONFIG, float
)
PRICING_CACHE_READ = _get_config_value(
    "AGENT_HUB_PRICING_CACHE_READ", ["pricing", "cache_read_per_1m"], 0.30, _CONFIG, float
)
PRICING_CACHE_WRITE = _get_config_value(
    "AGENT_HUB_PRICING_CACHE_WRITE", ["pricing", "cache_write_per_1m"], 3.75, _CONFIG, float
)

# Convert per-1M rates to per-token
PRICING_INPUT = PRICING_INPUT / 1_000_000
PRICING_OUTPUT = PRICING_OUTPUT / 1_000_000
PRICING_CACHE_READ = PRICING_CACHE_READ / 1_000_000
PRICING_CACHE_WRITE = PRICING_CACHE_WRITE / 1_000_000


# =============================================================================
# Rate Limiting Configuration
# =============================================================================

RATE_LIMIT_ENABLED = _get_config_value(
    "AGENT_HUB_RATE_LIMIT_ENABLED", ["rate_limit", "enabled"], False, _CONFIG, bool
)
RATE_LIMIT_MAX_MESSAGES = _get_config_value(
    "AGENT_HUB_RATE_LIMIT_MAX_MESSAGES",
    ["rate_limit", "max_messages_per_window"],
    100,
    _CONFIG,
    int,
)
RATE_LIMIT_WINDOW_SECONDS = _get_config_value(
    "AGENT_HUB_RATE_LIMIT_WINDOW_SECONDS", ["rate_limit", "window_seconds"], 3600, _CONFIG, int
)
RATE_LIMIT_COOLDOWN_SECONDS = _get_config_value(
    "AGENT_HUB_RATE_LIMIT_COOLDOWN_SECONDS",
    ["rate_limit", "cooldown_seconds"],
    5,
    _CONFIG,
    int,
)


# =============================================================================
# Route-specific chatty throttle
# =============================================================================

CHATTY_THROTTLE_ENABLED = _get_config_value(
    "AGENT_HUB_CHATTY_THROTTLE_ENABLED",
    ["chatty_throttle", "enabled"],
    True,
    _CONFIG,
    bool,
)
CHATTY_THROTTLE_MAX_MESSAGES = _get_config_value(
    "AGENT_HUB_CHATTY_THROTTLE_MAX_MESSAGES",
    ["chatty_throttle", "max_messages"],
    3,
    _CONFIG,
    int,
)
CHATTY_THROTTLE_WINDOW_SECONDS = _get_config_value(
    "AGENT_HUB_CHATTY_THROTTLE_WINDOW_SECONDS",
    ["chatty_throttle", "window_seconds"],
    15,
    _CONFIG,
    int,
)
CHATTY_THROTTLE_COOLDOWN_SECONDS = _get_config_value(
    "AGENT_HUB_CHATTY_THROTTLE_COOLDOWN_SECONDS",
    ["chatty_throttle", "cooldown_seconds"],
    15,
    _CONFIG,
    int,
)

# =============================================================================
# Agent & Session Configuration
# =============================================================================

AGENT_STALE_SECONDS = _get_config_value(
    "AGENT_HUB_AGENT_STALE_SECONDS", ["agent", "stale_seconds"], 600, _CONFIG, int
)
MESSAGE_TTL_SECONDS = _get_config_value(
    "AGENT_HUB_MESSAGE_TTL_SECONDS", ["message", "ttl_seconds"], 3600, _CONFIG, int
)

# =============================================================================
# Polling Intervals
# =============================================================================

SESSION_POLL_SECONDS = _get_config_value(
    "AGENT_HUB_SESSION_POLL_SECONDS", ["poll", "session_seconds"], 5, _CONFIG, int
)
GC_INTERVAL_SECONDS = _get_config_value(
    "AGENT_HUB_GC_INTERVAL_SECONDS", ["poll", "gc_seconds"], 60, _CONFIG, int
)
METRICS_INTERVAL = _get_config_value(
    "AGENT_HUB_METRICS_INTERVAL", ["poll", "metrics_seconds"], 60, _CONFIG, int
)

# =============================================================================
# Injection Configuration
# =============================================================================

INJECTION_TIMEOUT = _get_config_value(
    "AGENT_HUB_INJECTION_TIMEOUT", ["injection", "timeout_seconds"], 30, _CONFIG, int
)
INJECTION_RETRIES = _get_config_value(
    "AGENT_HUB_INJECTION_RETRIES", ["injection", "retries"], 3, _CONFIG, int
)
INJECTION_WORKERS = _get_config_value(
    "AGENT_HUB_INJECTION_WORKERS", ["injection", "workers"], 3, _CONFIG, int
)

# =============================================================================
# Caching Configuration
# =============================================================================

SESSION_CACHE_TTL = _get_config_value(
    "AGENT_HUB_SESSION_CACHE_TTL", ["cache", "session_ttl_seconds"], 5, _CONFIG, int
)

# =============================================================================
# Agent/Model Configuration
# =============================================================================

# Default agent name for injections when the session's agent can't be detected.
# Set via env var or config file. When empty/None, injections rely on the hub
# server's default model (set via HUB_MODEL) and omit the agent label.
# Only set this if you want to force a specific agent for undetectable sessions.
DEFAULT_AGENT: str | None = _get_config_value(
    "AGENT_HUB_DEFAULT_AGENT", ["agent", "default"], None, _CONFIG, str
)

# =============================================================================
# Orientation Configuration
# =============================================================================

ORIENTATION_RETRY_DELAY = _get_config_value(
    "AGENT_HUB_ORIENTATION_RETRY_DELAY", ["orientation", "retry_delay_seconds"], 30, _CONFIG, int
)
ORIENTATION_RETRY_MAX = _get_config_value(
    "AGENT_HUB_ORIENTATION_RETRY_MAX", ["orientation", "retry_max"], 5, _CONFIG, int
)

# =============================================================================
# Logging Configuration
# =============================================================================

LOG_LEVEL = _get_config_value(
    "AGENT_HUB_DAEMON_LOG_LEVEL", ["logging", "level"], "INFO", _CONFIG, str
)

# =============================================================================
# Internal State (global mutable state)
# =============================================================================

# Track which sessions have been oriented
ORIENTED_SESSIONS: set[str] = set()

# Threading lock for ORIENTED_SESSIONS access
ORIENTED_SESSIONS_LOCK = threading.Lock()

# Track session-to-agent mappings
SESSION_AGENTS: dict[str, dict[str, Any]] = {}

# Track pending orientation retries
ORIENTATION_PENDING: dict[str, dict] = {}

# Session cache (avoids repeated API calls)
_sessions_cache: list[dict] = []
_sessions_cache_time: float = 0

# Coordinator session ID, model override, and agent name (set at coordinator startup)
COORDINATOR_SESSION_ID: str | None = None
COORDINATOR_MODEL: dict[str, str] | None = None
COORDINATOR_AGENT: str | None = None

# Agent→model lookup: maps agent name to {"providerID": ..., "modelID": ...}.
# Populated by preflight from the resolved OpenCode config.
AGENT_MODELS: dict[str, dict[str, str]] = {}

# Default model used for injections when the session's agent can't be detected
# (e.g. brand new sessions with no assistant messages). Populated by preflight
# from the first non-disabled agent in the OpenCode config, or overridden via
# AGENT_HUB_DEFAULT_AGENT env var.
DEFAULT_INJECTION_MODEL: dict[str, str] | None = None

# Daemon start time - only orient sessions created after this
DAEMON_START_TIME_MS: int = int(time.time() * 1000)


def _is_running_from_source() -> bool:
    """Check if running from source (development) or installed package."""
    this_file = Path(__file__)
    # If we're in a site-packages or dist-packages, we're installed
    if "site-packages" in str(this_file) or "dist-packages" in str(this_file):
        return False
    # If parent of parent has pyproject.toml, we're likely in dev
    return (this_file.parent.parent.parent / "pyproject.toml").exists()


def _get_coordinator_title() -> str:
    """Get the coordinator session title from environment or default."""
    return os.environ.get("AGENT_HUB_COORDINATOR_TITLE", "Agent Hub Coordinator")


# Initialize logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
