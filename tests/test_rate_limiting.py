"""Tests for rate limiting functionality."""

import os
import time


def test_rate_limit_disabled_by_default():
    """Verify rate limiting is disabled when env var not set."""
    # Clear any existing env var
    os.environ.pop("AGENT_HUB_RATE_LIMIT", None)

    # Re-import to pick up env var
    import importlib

    from opencode_agent_hub import daemon

    importlib.reload(daemon)

    assert daemon.RATE_LIMIT_ENABLED is False


def test_rate_limit_check_when_disabled():
    """Verify check_rate_limit returns True when disabled."""
    os.environ.pop("AGENT_HUB_RATE_LIMIT", None)

    import importlib

    from opencode_agent_hub import daemon

    importlib.reload(daemon)

    allowed, reason = daemon.check_rate_limit("test-agent")
    assert allowed is True
    assert reason is None


def test_rate_limit_enabled():
    """Verify rate limiting can be enabled."""
    os.environ["AGENT_HUB_RATE_LIMIT"] = "true"
    os.environ["AGENT_HUB_RATE_LIMIT_MAX"] = "2"
    os.environ["AGENT_HUB_RATE_LIMIT_WINDOW"] = "60"
    os.environ["AGENT_HUB_RATE_LIMIT_COOLDOWN"] = "0"

    import importlib

    from opencode_agent_hub import daemon

    importlib.reload(daemon)

    # Clear any existing tracking
    daemon._agent_message_times.clear()

    assert daemon.RATE_LIMIT_ENABLED is True
    assert daemon.RATE_LIMIT_MAX_MESSAGES == 2

    # First message should be allowed
    allowed, reason = daemon.check_rate_limit("test-agent")
    assert allowed is True
    daemon.record_message_sent("test-agent")

    # Second message should be allowed
    allowed, reason = daemon.check_rate_limit("test-agent")
    assert allowed is True
    daemon.record_message_sent("test-agent")

    # Third message should be rate limited
    allowed, reason = daemon.check_rate_limit("test-agent")
    assert allowed is False
    assert "Rate limit" in reason

    # Cleanup
    os.environ.pop("AGENT_HUB_RATE_LIMIT", None)


def test_rate_limit_cooldown():
    """Verify cooldown period is enforced."""
    os.environ["AGENT_HUB_RATE_LIMIT"] = "true"
    os.environ["AGENT_HUB_RATE_LIMIT_MAX"] = "100"
    os.environ["AGENT_HUB_RATE_LIMIT_WINDOW"] = "60"
    os.environ["AGENT_HUB_RATE_LIMIT_COOLDOWN"] = "1"  # 1 second cooldown

    import importlib

    from opencode_agent_hub import daemon

    importlib.reload(daemon)

    # Clear any existing tracking
    daemon._agent_message_times.clear()

    # First message allowed
    allowed, _ = daemon.check_rate_limit("cooldown-agent")
    assert allowed is True
    daemon.record_message_sent("cooldown-agent")

    # Immediate second message should be blocked by cooldown
    allowed, reason = daemon.check_rate_limit("cooldown-agent")
    assert allowed is False
    assert "Cooldown" in reason

    # Wait for cooldown
    time.sleep(1.1)

    # Now should be allowed
    allowed, _ = daemon.check_rate_limit("cooldown-agent")
    assert allowed is True

    # Cleanup
    os.environ.pop("AGENT_HUB_RATE_LIMIT", None)


def test_format_orientation_includes_essentials():
    """Verify orientation message includes essential info."""
    from opencode_agent_hub import daemon

    agent = {"id": "test-agent", "projectPath": "/test/path"}
    all_agents = {
        "test-agent": agent,
        "other-agent": {
            "id": "other-agent",
            "projectPath": "/other/path",
            "lastSeen": int(time.time() * 1000),
        },
    }

    orientation = daemon.format_orientation(agent, all_agents)

    # Check key elements are present (minimalist format)
    assert "test-agent" in orientation
    assert "other-agent" in orientation  # Other agent should be listed
    assert "agent-hub" in orientation.lower()  # Tool reference


def test_format_orientation_excludes_inactive_agents():
    """Verify orientation excludes stale agents."""
    from opencode_agent_hub import daemon

    agent = {"id": "test-agent", "projectPath": "/test/path"}
    all_agents = {
        "test-agent": agent,
        "stale-agent": {
            "id": "stale-agent",
            "projectPath": "/stale/path",
            "lastSeen": 0,  # Very old timestamp
        },
    }

    orientation = daemon.format_orientation(agent, all_agents)

    # Stale agent should not be listed
    assert "stale-agent" not in orientation
