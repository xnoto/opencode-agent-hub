"""Tests for rate limiting functionality."""

from unittest import mock


def test_rate_limit_disabled_by_default() -> None:
    """Verify rate limiting is disabled by default."""
    from opencode_agent_hub import config

    assert config.RATE_LIMIT_ENABLED is False


def test_rate_limit_check_when_disabled() -> None:
    """Verify check_rate_limit returns True when disabled."""
    from opencode_agent_hub import rate_limiting

    allowed, reason = rate_limiting.check_rate_limit("test-agent")
    assert allowed is True
    assert reason is None


def test_rate_limit_enabled() -> None:
    """Verify rate limiting can be enabled via config."""
    from opencode_agent_hub import config, rate_limiting

    # Mock config values directly
    with (
        mock.patch.object(config, "RATE_LIMIT_ENABLED", True),
        mock.patch.object(config, "RATE_LIMIT_MAX_MESSAGES", 2),
        mock.patch.object(config, "RATE_LIMIT_WINDOW_SECONDS", 60),
        mock.patch.object(config, "RATE_LIMIT_COOLDOWN_SECONDS", 0),
    ):
        # Clear any existing tracking
        rate_limiting._agent_message_times.clear()

        # First message should be allowed
        allowed, reason = rate_limiting.check_rate_limit("test-agent")
        assert allowed is True
        rate_limiting.record_message_sent("test-agent")

        # Second message should be allowed
        allowed, reason = rate_limiting.check_rate_limit("test-agent")
        assert allowed is True
        rate_limiting.record_message_sent("test-agent")

        # Third message should be rate limited
        allowed, reason = rate_limiting.check_rate_limit("test-agent")
        assert allowed is False
        assert reason and "Rate limit" in reason


def test_rate_limit_cooldown() -> None:
    """Verify cooldown period is enforced."""
    import time

    from opencode_agent_hub import config, rate_limiting

    # Mock config values directly
    with (
        mock.patch.object(config, "RATE_LIMIT_ENABLED", True),
        mock.patch.object(config, "RATE_LIMIT_MAX_MESSAGES", 100),
        mock.patch.object(config, "RATE_LIMIT_WINDOW_SECONDS", 60),
        mock.patch.object(config, "RATE_LIMIT_COOLDOWN_SECONDS", 1),  # 1 second cooldown
    ):
        # Clear any existing tracking
        rate_limiting._agent_message_times.clear()

        # First message allowed
        allowed, _ = rate_limiting.check_rate_limit("cooldown-agent")
        assert allowed is True
        rate_limiting.record_message_sent("cooldown-agent")

        # Immediate second message should be blocked by cooldown
        allowed, reason = rate_limiting.check_rate_limit("cooldown-agent")
        assert allowed is False
        assert reason and "Cooldown" in reason

        # Wait for cooldown
        time.sleep(1.1)

        # Now should be allowed
        allowed, _ = rate_limiting.check_rate_limit("cooldown-agent")
        assert allowed is True
