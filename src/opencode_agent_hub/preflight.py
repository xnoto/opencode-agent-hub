"""Preflight checks for the agent hub daemon.

This module verifies that the agent-hub MCP is properly configured
before the daemon starts.
"""

import json
import shutil
import subprocess

from opencode_agent_hub.config import log
from opencode_agent_hub.models import PreflightError


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

    # Check that agent-hub tools are allowed in permissions
    permissions = config.get("permission", {})
    agent_hub_allowed = False

    if isinstance(permissions, dict):
        # Check for agent-hub_* permission
        agent_hub_perm = permissions.get("agent-hub_*")
        if agent_hub_perm == "allow":
            agent_hub_allowed = True

    if not agent_hub_allowed:
        raise PreflightError(
            "agent-hub tools are not allowed in OpenCode permissions.\n\n"
            f'To fix, add "agent-hub_*": "allow" to the permission section in {config_path}:\n\n'
            '  "permission": {\n'
            '    "agent-hub_*": "allow",\n'
            "    ...\n"
            "  }\n\n"
            "Then restart OpenCode."
        )

    log.info("Preflight: agent-hub tools allowed in permissions")
    return True
