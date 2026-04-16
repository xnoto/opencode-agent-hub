"""Systemd service installation for the agent hub daemon.

This module handles installation and removal of the systemd user service
on Linux systems.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from opencode_agent_hub.config import log

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
