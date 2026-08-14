"""Coordinator agent management for the agent hub daemon.

This module handles the lifecycle of the coordinator agent which facilitates
collaboration between other agents in the system.
"""

import shutil
import time
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import requests

from opencode_agent_hub import config
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.models import PreflightError
from opencode_agent_hub.persistence import atomic_write_json


def find_coordinator_agents_md_template() -> Path | None:
    """Find the AGENTS.md template for the coordinator.

    Search order:
    1. Explicit config/env var path (COORDINATOR_AGENTS_MD)
    2. ~/.config/agent-hub-daemon/AGENTS.md (user override)
    3. ~/.config/agent-hub-daemon/COORDINATOR.md (alias)
    4. Development: repo contrib/ directory (when running from source)
    5. importlib.resources (pip/PyPI package data)
    6. /usr/share/opencode-agent-hub/coordinator/AGENTS.md (system packages: deb, rpm)
    7. /usr/local/share/opencode-agent-hub/coordinator/AGENTS.md (local install)
    8. ~/.local/share/opencode-agent-hub/coordinator/AGENTS.md (user local)

    Returns the first existing path, or None if no template found.
    """
    # 1. Explicit config path takes highest priority
    if config.COORDINATOR_AGENTS_MD is not None:
        if config.COORDINATOR_AGENTS_MD.exists():
            return cast(Path, config.COORDINATOR_AGENTS_MD)
        else:
            config.log.warning(
                f"Configured coordinator AGENTS.md not found: {config.COORDINATOR_AGENTS_MD}"
            )
            # Fall through to other locations

    # 2-3. User config directory overrides
    user_config_locations = [
        config.CONFIG_DIR / "AGENTS.md",
        config.CONFIG_DIR / "COORDINATOR.md",
    ]

    for path in user_config_locations:
        if path.exists():
            return path

    # 4. Development: repo contrib/ directory (when running from source)
    if config._is_running_from_source():
        dev_template = Path(__file__).parent.parent.parent / "contrib" / "coordinator" / "AGENTS.md"
        if dev_template.exists():
            return dev_template

    # 5. importlib.resources - works with pip/PyPI installs
    try:
        import opencode_agent_hub

        pkg_path = files(opencode_agent_hub) / "contrib" / "coordinator" / "AGENTS.md"
        if pkg_path.is_file():
            return Path(str(pkg_path))
    except (ImportError, TypeError):
        pass

    # 6-8. System locations (FHS-compliant + Homebrew)
    system_locations = [
        Path("/usr/share/opencode-agent-hub/coordinator/AGENTS.md"),  # deb, rpm
        Path(
            "/usr/local/share/opencode-agent-hub/coordinator/AGENTS.md"
        ),  # local, Homebrew (Intel)
        Path(
            "/opt/homebrew/share/opencode-agent-hub/coordinator/AGENTS.md"
        ),  # Homebrew (Apple Silicon)
        Path.home() / ".local/share/opencode-agent-hub/coordinator/AGENTS.md",  # user
    ]

    for path in system_locations:
        if path.exists():
            return path

    return None


def find_coordinator_opencode_json_template() -> Path | None:
    """Find the opencode.json template for the coordinator.

    Search order:
    1. ~/.config/agent-hub-daemon/opencode.json (user override - highest priority)
    2. Development: repo contrib/ directory (when running from source)
    3. importlib.resources (pip/PyPI package data)
    4. /usr/share/opencode-agent-hub/coordinator/opencode.json (system packages: deb, rpm)
    5. /usr/local/share/opencode-agent-hub/coordinator/opencode.json (local install)
    6. ~/.local/share/opencode-agent-hub/coordinator/opencode.json (user local)

    Returns the first existing path, or None if no template found.
    """
    # 1. User config directory override (highest priority)
    user_config_path = config.CONFIG_DIR / "opencode.json"
    if user_config_path.exists():
        return user_config_path

    # 2. Development: repo contrib/ directory (when running from source)
    if config._is_running_from_source():
        dev_template = (
            Path(__file__).parent.parent.parent / "contrib" / "coordinator" / "opencode.json"
        )
        if dev_template.exists():
            return dev_template

    # 3. importlib.resources - works with pip/PyPI installs
    try:
        import opencode_agent_hub

        pkg_path = files(opencode_agent_hub) / "contrib" / "coordinator" / "opencode.json"
        if pkg_path.is_file():
            return Path(str(pkg_path))
    except (ImportError, TypeError):
        pass

    # 4-6. System locations (FHS-compliant + Homebrew)
    system_locations = [
        Path("/usr/share/opencode-agent-hub/coordinator/opencode.json"),  # deb, rpm
        Path(
            "/usr/local/share/opencode-agent-hub/coordinator/opencode.json"
        ),  # local, Homebrew (Intel)
        Path(
            "/opt/homebrew/share/opencode-agent-hub/coordinator/opencode.json"
        ),  # Homebrew (Apple Silicon)
        Path.home() / ".local/share/opencode-agent-hub/coordinator/opencode.json",  # user
    ]

    for path in system_locations:
        if path.exists():
            return path

    return None


def session_has_blocking_permissions(session: dict) -> bool:
    """Check if a session has blocking permissions that prevent message injection.

    A session is blocking if it has a permission rule that denies the "question"
    permission with pattern "*". This prevents prompt_async injections from
    being delivered.

    Args:
        session: Session dict from the OpenCode API containing 'permission' field.

    Returns:
        True if session has blocking permissions, False otherwise.
    """
    permissions = session.get("permission")
    if not isinstance(permissions, list):
        return False

    for perm in permissions:
        if not isinstance(perm, dict):
            continue

        permission_name = perm.get("permission", "")
        pattern = perm.get("pattern", "")
        action = perm.get("action", "")

        # Check for question:deny which blocks prompt_async
        if permission_name == "question" and pattern == "*" and action == "deny":
            return True

    return False


def setup_coordinator_directory() -> bool:
    """Set up the coordinator directory with AGENTS.md and opencode.json.

    Files copied/overwritten:
    - AGENTS.md: instructions for the coordinator agent (see find_coordinator_agents_md_template())
    - opencode.json: permissions config for the coordinator (see find_coordinator_opencode_json_template())

    Returns True if setup succeeded, False otherwise.
    """
    try:
        config.COORDINATOR_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        config.log.error(f"Failed to create coordinator directory: {e}")
        return False

    # Always copy/overwrite opencode.json from template (REQUIRED)
    opencode_json = config.COORDINATOR_DIR / "opencode.json"
    json_template = find_coordinator_opencode_json_template()
    if json_template is not None:
        shutil.copy(json_template, opencode_json)
        config.log.info(f"Copied coordinator opencode.json from {json_template}")
    else:
        config.log.error("No opencode.json template found for coordinator - cannot continue")
        return False

    # Handle AGENTS.md (overwrite by default to avoid stale coordinator behavior)
    agents_md = config.COORDINATOR_DIR / "AGENTS.md"

    if agents_md.exists() and config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD:
        config.log.info(f"Preserving existing coordinator AGENTS.md at {agents_md}")
        return True

    template = find_coordinator_agents_md_template()
    if template is not None:
        shutil.copy(template, agents_md)
        config.log.info(f"Copied coordinator AGENTS.md from {template}")
    else:
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
        config.log.info(f"Created minimal coordinator AGENTS.md at {agents_md}")

    return True


def kill_all_coordinator_sessions() -> int:
    """Kill all existing coordinator sessions on the hub server.

    Returns the number of sessions killed.
    """
    from opencode_agent_hub.sessions import get_sessions_uncached

    sessions = get_sessions_uncached()
    if sessions is None:
        return 0

    coordinator_title = config._get_coordinator_title()
    killed = 0
    for session in sessions:
        session_id = session.get("id")
        title = session.get("title", "")

        # Only kill coordinator sessions (exact title match)
        if title != coordinator_title:
            continue

        if session_id:
            config.log.info(f"Killing coordinator session {session_id[:8]} (title: {title})")
            try:
                resp = requests.delete(f"{config.OPENCODE_URL}/session/{session_id}", timeout=5)
                if resp.status_code in (200, 204):
                    config.log.info(f"Killed coordinator session {session_id[:8]}")
                    killed += 1
                else:
                    config.log.warning(
                        f"Failed to kill coordinator session {session_id[:8]}: "
                        f"HTTP {resp.status_code}"
                    )
            except requests.RequestException as e:
                config.log.warning(f"Failed to kill coordinator session: {e}")

    return killed


def find_coordinator_session() -> str | None:
    """Find the coordinator's session on the hub server.

    Checks for blocking permissions and raises PreflightError if found,
    so the caller knows to kill the existing session and create a new one.

    Returns the coordinator session ID if it exists and has valid permissions.
    Returns None if no coordinator session exists.

    Raises:
        PreflightError: If coordinator session exists but has blocking permissions.
    """
    from opencode_agent_hub.sessions import get_sessions_uncached

    sessions = get_sessions_uncached()
    if sessions is None:
        return None

    coordinator_title = config._get_coordinator_title()

    for session in sessions:
        title = session.get("title", "")
        if title != coordinator_title:
            continue

        # Check for blocking permissions
        if session_has_blocking_permissions(session):
            session_id = session.get("id", "unknown")
            raise PreflightError(
                f"Coordinator session {session_id[:8]} has blocking permissions (question: deny). "
                "This prevents message injection. The session must be recreated with correct permissions."
            )

        return session.get("id")
    return None


def _coordinator_has_ready_ack(messages: list[dict[str, Any]]) -> bool:
    """Return True when coordinator replied with exact READY text."""
    for msg in messages:
        info = msg.get("info", {})
        if info.get("role") != "assistant":
            continue

        for part in msg.get("parts", []):
            if part.get("type") == "text" and str(part.get("text", "")).strip() == "READY":
                return True
    return False


def _coordinator_has_activity_after(messages: list[dict[str, Any]], after_ms: int) -> bool:
    """Return True when coordinator produced assistant output after a timestamp."""
    for msg in messages:
        info = msg.get("info", {})
        if info.get("role") != "assistant":
            continue

        created = int(info.get("time", {}).get("created", 0))
        if created <= after_ms:
            continue

        if msg.get("parts"):
            return True
    return False


def _fetch_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Fetch session messages, returning an empty list on errors."""
    try:
        resp = requests.get(
            f"{config.OPENCODE_URL}/session/{session_id}/message", timeout=config.INJECTION_TIMEOUT
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            return payload
    except requests.RequestException:
        return []
    except (ValueError, TypeError):
        return []
    return []


def _get_recent_hub_error_context(session_id: str, max_entries: int = 2) -> str:
    """Get concise recent hub error lines related to a session."""
    if not config.HUB_STDERR_LOG_FILE.exists():
        return ""

    try:
        lines = config.HUB_STDERR_LOG_FILE.read_text(errors="replace").splitlines()
    except OSError:
        return ""

    sid_token = f"sessionID={session_id}"
    related_errors = [line.strip() for line in lines if sid_token in line and "ERROR" in line]
    if not related_errors:
        return ""

    tail = related_errors[-max_entries:]
    clipped = [entry[:280] for entry in tail]
    return " | ".join(clipped)


def _wait_for_coordinator_ready(session_id: str, timeout_seconds: int, after_ms: int = 0) -> bool:
    """Wait for coordinator readiness based on READY or assistant activity."""
    deadline = time.time() + max(1, timeout_seconds)
    last_log_time = 0.0
    log_interval = 10

    while time.time() < deadline:
        messages = _fetch_session_messages(session_id)

        # Check for READY ack
        if _coordinator_has_ready_ack(messages):
            config.log.info(f"Coordinator session {session_id[:8]} acknowledged READY")
            return True

        # Check for any activity (non-strict mode)
        if not config.COORDINATOR_STRICT_READY and _coordinator_has_activity_after(
            messages, after_ms
        ):
            config.log.info(
                f"Coordinator session {session_id[:8]} showed activity (non-strict mode)"
            )
            return True

        # Progress logging
        now = time.time()
        if now - last_log_time >= log_interval:
            remaining = int(deadline - now)
            msg_count = len(messages)
            assistant_msgs = sum(
                1 for m in messages if m.get("info", {}).get("role") == "assistant"
            )
            config.log.debug(
                f"Waiting for coordinator {session_id[:8]}... "
                f"({remaining}s remaining, {msg_count} total msgs, {assistant_msgs} assistant)"
            )
            last_log_time = now

            # Log message samples for debugging
            if assistant_msgs > 0:
                recent_assistant = [
                    m for m in messages if m.get("info", {}).get("role") == "assistant"
                ][-3:]
                for msg in recent_assistant:
                    parts = msg.get("parts", [])
                    text_parts = [p.get("text", "")[:100] for p in parts if p.get("type") == "text"]
                    if text_parts:
                        config.log.debug(f"  Recent assistant msg: {text_parts[0][:80]}...")

        time.sleep(0.5)

    # Timeout - provide detailed diagnostics
    config.log.error(f"Coordinator {session_id[:8]} timeout after {timeout_seconds}s")
    config.log.error(f"  STRICT_READY mode: {config.COORDINATOR_STRICT_READY}")
    config.log.error("  Looking for: exact 'READY' text in assistant message")

    messages = _fetch_session_messages(session_id)
    msg_count = len(messages)
    assistant_msg_list = [m for m in messages if m.get("info", {}).get("role") == "assistant"]
    config.log.error(
        f"  Total messages: {msg_count}, Assistant messages: {len(assistant_msg_list)}"
    )

    if assistant_msg_list:
        config.log.error("  Recent assistant messages received:")
        for i, msg in enumerate(assistant_msg_list[-3:], 1):
            parts = msg.get("parts", [])
            text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
            for text in text_parts:
                config.log.error(f"    [{i}] {text[:200]}{'...' if len(text) > 200 else ''}")
    else:
        config.log.error("  No assistant messages received - coordinator may not be processing")
        config.log.error("  Possible causes:")
        config.log.error("    - No OpenCode UI connected to the coordinator session")
        config.log.error("    - OpenCode is not running or hub server failed")
        config.log.error("    - Session has blocking permissions (question: deny)")
        config.log.error(f"  Session URL: {config.OPENCODE_URL}/session/{session_id}")

    return False


def _wait_for_coordinator_activity(
    session_id: str, after_ms: int, timeout_seconds: int = 5
) -> bool:
    """Wait for coordinator assistant output after a specific timestamp."""
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        messages = _fetch_session_messages(session_id)
        if _coordinator_has_activity_after(messages, after_ms):
            return True
        time.sleep(0.5)
    return False


def start_coordinator() -> bool:
    """Start the coordinator OpenCode session.

    Startup remains lightweight, but requires a READY acknowledgement after
    bootstrap to avoid silent coordinator failures.

    Returns True if coordinator session is ready, False otherwise.
    """
    from opencode_agent_hub.messaging import inject_message_sync

    global config

    if not config.COORDINATOR_ENABLED:
        config.log.info("Coordinator disabled via AGENT_HUB_COORDINATOR=false")
        return False

    # Set up coordinator directory
    if not setup_coordinator_directory():
        config.log.error("Failed to set up coordinator directory")
        return False

    # Kill all existing coordinator sessions before starting a new one
    killed = kill_all_coordinator_sessions()
    if killed > 0:
        config.log.info(f"Killed {killed} existing coordinator session(s)")

    config.log.info("Starting coordinator session...")

    # Resolve the coordinator's model from opencode.json.
    # The "model" field (e.g. "opencode/minimax-m2.5-free") specifies the
    # exact model to use and takes priority. The "agent" field (e.g. "minimax")
    # is used for the prompt_async agent label; if no explicit model is given
    # we fall back to looking up the agent in AGENT_MODELS.
    coordinator_model_override: dict[str, str] | None = None
    agent_name: str | None = None
    try:
        opencode_json_path = config.COORDINATOR_DIR / "opencode.json"
        if opencode_json_path.exists():
            import json

            with open(opencode_json_path) as f:
                opencode_config = json.load(f)
            agent_name = opencode_config.get("agent")
            model_str = opencode_config.get("model", "")

            # Prefer explicit model field (allows specifying a free/specific model)
            if model_str and "/" in model_str:
                provider_id, model_id = model_str.split("/", 1)
                coordinator_model_override = {
                    "providerID": provider_id,
                    "modelID": model_id,
                }
                config.log.info(f"Coordinator agent: {agent_name or 'n/a'}, model: {model_str}")
            elif agent_name and agent_name in config.AGENT_MODELS:
                # Fall back to agent→model lookup when no explicit model
                coordinator_model_override = config.AGENT_MODELS[agent_name]
                config.log.info(
                    f"Coordinator agent: {agent_name}, "
                    f"model: {coordinator_model_override['providerID']}/{coordinator_model_override['modelID']}"
                )
            else:
                config.log.info(f"Coordinator agent: {agent_name or 'n/a'}, model: default")
    except Exception as e:
        config.log.debug(f"Could not read coordinator config from opencode.json: {e}")

    # Create session via HTTP API.
    try:
        coordinator_title = config._get_coordinator_title()
        resp = requests.post(
            f"{config.OPENCODE_URL}/session",
            json={
                "title": coordinator_title,
                "directory": str(config.COORDINATOR_DIR),
            },
            timeout=10,
        )
        if resp.status_code != 200:
            config.log.error(
                f"Failed to create coordinator session via API: HTTP {resp.status_code}"
            )
            return False

        session_data = resp.json()
        session_id = session_data.get("id")
        if not session_id:
            config.log.error("API created session but no session ID returned")
            return False

        config.log.info(f"Created coordinator session via API: {session_id[:8]}")

        config.COORDINATOR_SESSION_ID = session_id
        config.COORDINATOR_MODEL = coordinator_model_override
        config.COORDINATOR_AGENT = agent_name or config.DEFAULT_AGENT
        config.ORIENTED_SESSIONS.add(session_id)

        # Register coordinator as an agent so other agents can message it
        coordinator_agent = {
            "id": "coordinator",
            "sessionId": session_id,
            "projectPath": str(config.COORDINATOR_DIR),
            "role": "Agent hub coordinator - facilitates collaboration between agents",
            "capabilities": [
                "agent-hub_send_message",
                "agent-hub_sync",
                "agent-hub_get_hub_status",
            ],
            "collaboratesWith": [],
            "status": "active",
            "lastSeen": int(time.time() * 1000),
        }
        config.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        agent_file = config.AGENTS_DIR / "coordinator.json"
        atomic_write_json(agent_file, coordinator_agent, indent=2)
        config.log.info("Registered coordinator as agent 'coordinator'")

        # Send bootstrap prompt synchronously before waiting for READY.
        ready_after_ms = int(time.time() * 1000)

        if not inject_message_sync(
            session_id,
            config.COORDINATOR_BOOTSTRAP_PROMPT,
            model=coordinator_model_override,
            agent=agent_name or config.DEFAULT_AGENT,
        ):
            config.log.error(
                f"Failed to inject coordinator bootstrap prompt for session {session_id[:8]}"
            )
            with suppress(requests.RequestException):
                requests.delete(f"{config.OPENCODE_URL}/session/{session_id}", timeout=5)
            config.COORDINATOR_SESSION_ID = None
            config.COORDINATOR_MODEL = None
            config.COORDINATOR_AGENT = None
            config.ORIENTED_SESSIONS.discard(session_id)
            if agent_file.exists():
                agent_file.unlink()
            return False

        config.log.info(f"Injected coordinator bootstrap prompt for session {session_id[:8]}")

        if not _wait_for_coordinator_ready(
            session_id, config.COORDINATOR_READY_TIMEOUT_SECONDS, after_ms=ready_after_ms
        ):
            msg = (
                "Coordinator did not become ready after bootstrap "
                f"within {config.COORDINATOR_READY_TIMEOUT_SECONDS}s"
            )
            error_context = _get_recent_hub_error_context(session_id)
            if error_context:
                msg = f"{msg}; recent hub error context: {error_context}"
            if config.COORDINATOR_BOOTSTRAP_REQUIRED:
                config.log.error(msg)
                with suppress(requests.RequestException):
                    requests.delete(f"{config.OPENCODE_URL}/session/{session_id}", timeout=5)
                config.COORDINATOR_SESSION_ID = None
                config.COORDINATOR_MODEL = None
                config.COORDINATOR_AGENT = None
                config.ORIENTED_SESSIONS.discard(session_id)
                if agent_file.exists():
                    agent_file.unlink()
                return False
            config.log.warning(f"{msg}; continuing with coordinator in best-effort mode")

        config.log.info(f"Coordinator session ready: {session_id[:8]}")
        return True

    except Exception as e:
        config.log.error(f"Failed to start coordinator: {e}")
        return False


def stop_coordinator() -> None:
    """Stop the coordinator session.

    Kills the coordinator session on the hub server via API call.
    """
    global config

    if config.COORDINATOR_SESSION_ID is not None:
        config.log.info(f"Stopping coordinator session: {config.COORDINATOR_SESSION_ID[:8]}")
        try:
            resp = requests.delete(
                f"{config.OPENCODE_URL}/session/{config.COORDINATOR_SESSION_ID}", timeout=5
            )
            if resp.status_code in (200, 204):
                config.log.info(f"Killed coordinator session {config.COORDINATOR_SESSION_ID[:8]}")
            else:
                config.log.warning(f"Failed to kill coordinator session: HTTP {resp.status_code}")
        except Exception as e:
            config.log.warning(f"Failed to kill coordinator session: {e}")
        config.COORDINATOR_SESSION_ID = None
        config.COORDINATOR_MODEL = None
        config.COORDINATOR_AGENT = None

        try:
            agent_file = config.AGENTS_DIR / "coordinator.json"
            if agent_file.exists():
                agent_file.unlink()
                config.log.info("Removed coordinator agent registration")
        except Exception as e:
            config.log.warning(f"Failed to remove coordinator agent file: {e}")


def notify_coordinator_new_agent(agent_id: str, directory: str) -> None:
    """Notify the coordinator that a new agent has joined.

    Injects a message into the coordinator session so it can
    reach out to the new agent and facilitate collaboration.
    """
    from opencode_agent_hub.messaging import inject_message

    if not config.COORDINATOR_ENABLED or not config.COORDINATOR_SESSION_ID:
        return

    before_ms = int(time.time() * 1000)
    notification = f"NEW_AGENT: {agent_id} at {directory}"
    inject_message(config.COORDINATOR_SESSION_ID, notification)
    config.log.info(f"Notified coordinator of new agent: {agent_id}")

    if not _wait_for_coordinator_activity(
        config.COORDINATOR_SESSION_ID, before_ms, timeout_seconds=5
    ):
        config.log.warning(
            f"Coordinator showed no activity after NEW_AGENT {agent_id}; retrying notification once"
        )
        inject_message(config.COORDINATOR_SESSION_ID, notification)


def poll_coordinator_cost() -> None:
    """Poll coordinator session messages and update cost/token metrics.

    Fetches all messages from the coordinator session via the OpenCode API,
    sums token usage from assistant messages, computes estimated USD cost
    using the configured pricing table, and updates Prometheus metrics.

    Token counts are set as absolute values (not incremented) since we
    re-sum from the full message history each poll. This is idempotent
    and self-correcting.
    """
    if not config.COORDINATOR_ENABLED or not config.COORDINATOR_SESSION_ID:
        return

    try:
        resp = requests.get(
            f"{config.OPENCODE_URL}/session/{config.COORDINATOR_SESSION_ID}/message",
            timeout=config.INJECTION_TIMEOUT,
        )
        resp.raise_for_status()
        messages = resp.json()
    except requests.RequestException as e:
        config.log.debug(f"Failed to fetch coordinator messages for cost tracking: {e}")
        return
    except (ValueError, TypeError):
        return

    if not isinstance(messages, list):
        return

    # Sum token usage from all assistant messages
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    assistant_count = 0

    for msg in messages:
        info = msg.get("info", {})
        if info.get("role") != "assistant":
            continue

        tokens = info.get("tokens", {})
        total_input += tokens.get("input", 0)
        total_output += tokens.get("output", 0)
        cache = tokens.get("cache", {})
        total_cache_read += cache.get("read", 0)
        total_cache_write += cache.get("write", 0)
        assistant_count += 1

    # Compute estimated cost
    estimated_cost = (
        total_input * config.PRICING_INPUT
        + total_output * config.PRICING_OUTPUT
        + total_cache_read * config.PRICING_CACHE_READ
        + total_cache_write * config.PRICING_CACHE_WRITE
    )

    # Update metrics (set absolute values, not increments)
    metrics.set_counter("agent_hub_coordinator_tokens_input", total_input)
    metrics.set_counter("agent_hub_coordinator_tokens_output", total_output)
    metrics.set_counter("agent_hub_coordinator_tokens_cache_read", total_cache_read)
    metrics.set_counter("agent_hub_coordinator_tokens_cache_write", total_cache_write)
    metrics.set_counter("agent_hub_coordinator_messages_total", assistant_count)
    metrics.set_gauge("agent_hub_coordinator_estimated_cost_usd", round(estimated_cost, 6))

    config.log.debug(
        f"Coordinator cost: ${estimated_cost:.4f} "
        f"({assistant_count} msgs, {total_input}in/{total_output}out/"
        f"{total_cache_read}cr/{total_cache_write}cw tokens)"
    )
