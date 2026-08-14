"""Hub server management for the agent hub daemon.

This module handles the lifecycle of the OpenCode hub server which provides
HTTP API access for message injection into sessions.
"""

import os
import shutil
import signal
import subprocess
import time
from typing import cast

import requests

from opencode_agent_hub.config import (
    DAEMON_LOG_DIR,
    HUB_MODEL,
    HUB_SERVER_PID_FILE,
    HUB_STDERR_LOG_FILE,
    OPENCODE_PORT,
    OPENCODE_URL,
    log,
)

_hub_server_process: subprocess.Popen | None = None


def is_hub_server_running() -> bool:
    """Check if OpenCode hub server is responding on the configured port."""
    try:
        resp = requests.get(f"{OPENCODE_URL}/session", timeout=2)
        return cast(bool, resp.status_code == 200)
    except requests.RequestException:
        return False


def _find_opencode_serve_pids_on_port() -> list[int]:
    """Find opencode serve PIDs listening on configured hub port."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{OPENCODE_PORT}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    pids: list[int] = []
    for line in out.stdout.splitlines():
        token = line.strip()
        if not token.isdigit():
            continue
        pid = int(token)
        try:
            ps = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        cmd = ps.stdout.strip()
        if "opencode" in cmd and "serve" in cmd:
            pids.append(pid)

    return pids


def _kill_opencode_serve_pids(pids: list[int]) -> None:
    """Terminate/kill opencode serve processes by pid."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue

    time.sleep(0.5)

    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def start_hub_server() -> subprocess.Popen | None:
    """Launch OpenCode hub server in headless mode.

    The hub server provides HTTP API access for message injection into sessions.
    It uses the user's normal OpenCode config (no XDG_CONFIG_HOME isolation) so
    that TUI instances discover and connect to it, enabling prompt_async injection.

    Session discovery is handled separately via direct SQLite queries, since the
    hub server's listing API only returns sessions it manages internally.
    """
    global _hub_server_process

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
        DAEMON_LOG_DIR.mkdir(parents=True, exist_ok=True)

        # Open log files with restrictive permissions (owner read/write only)
        stdout_log_path = DAEMON_LOG_DIR / "hub-stdout.log"
        stderr_log_path = HUB_STDERR_LOG_FILE

        # Create files with 0o600 permissions if they don't exist
        for log_path in (stdout_log_path, stderr_log_path):
            if not log_path.exists():
                log_path.touch(mode=0o600)

        hub_stdout = open(stdout_log_path, "a")  # noqa: SIM115
        hub_stderr = open(stderr_log_path, "a")  # noqa: SIM115

        # Ensure file descriptors have restrictive permissions even if file existed
        os.chmod(hub_stdout.fileno(), 0o600)
        os.chmod(hub_stderr.fileno(), 0o600)

        _hub_server_process = subprocess.Popen(
            [
                opencode_bin,
                "serve",
                "--port",
                str(OPENCODE_PORT),
                "--print-logs",
            ],
            stdout=hub_stdout,
            stderr=hub_stderr,
            start_new_session=True,  # Detach from terminal
        )

        # Wait for server to start
        for _ in range(30):  # 30 attempts, 0.5s each = 15s max
            time.sleep(0.5)
            if is_hub_server_running():
                log.info(f"Hub server started (PID {_hub_server_process.pid})")
                HUB_SERVER_PID_FILE.write_text(str(_hub_server_process.pid))
                _apply_hub_model()
                return _hub_server_process

        log.error("Hub server failed to start within timeout")
        _hub_server_process.terminate()
        _kill_opencode_serve_pids(_find_opencode_serve_pids_on_port())
        _hub_server_process = None
        return None

    except Exception as e:
        log.error(f"Failed to start hub server: {e}")
        return None


def _apply_hub_model() -> None:
    """Set the hub server's default model via PATCH /config.

    Without this, the hub server defaults to claude for API-created sessions.
    The model is configured via AGENT_HUB_MODEL env var or hub.model config key.
    """
    if not HUB_MODEL or "/" not in HUB_MODEL:
        return

    try:
        resp = requests.patch(
            f"{OPENCODE_URL}/config",
            json={"model": HUB_MODEL},
            timeout=5,
        )
        if resp.status_code == 200:
            log.info(f"Hub server default model set to {HUB_MODEL}")
        else:
            log.warning(f"Failed to set hub model: HTTP {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Failed to set hub model: {e}")


def stop_hub_server() -> None:
    """Stop the hub server if we started it."""
    global _hub_server_process

    if _hub_server_process is not None:
        log.info(f"Stopping hub server (PID {_hub_server_process.pid})...")
        try:
            _hub_server_process.terminate()
            _hub_server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("Hub server didn't stop gracefully, killing...")
            _hub_server_process.kill()
        _hub_server_process = None

    elif HUB_SERVER_PID_FILE.exists():
        try:
            pid = int(HUB_SERVER_PID_FILE.read_text().strip())
            cmd = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=2,
            )
            command = cmd.stdout.strip()
            if "opencode serve" in command and f"--port {OPENCODE_PORT}" in command:
                log.info(f"Stopping hub server from PID file (PID {pid})...")
                os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass

    try:
        if HUB_SERVER_PID_FILE.exists():
            HUB_SERVER_PID_FILE.unlink()
    except OSError:
        pass

    # Final safety: kill any stray opencode serve still bound to this port.
    _kill_opencode_serve_pids(_find_opencode_serve_pids_on_port())


def get_hub_server_process() -> subprocess.Popen | None:
    """Get the current hub server process (for monitoring)."""
    return _hub_server_process
