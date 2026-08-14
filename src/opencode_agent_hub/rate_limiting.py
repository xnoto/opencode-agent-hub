"""Rate limiting for the agent hub daemon.

This module provides optional rate limiting to prevent runaway costs from
autonomous agent communication.
"""

import time

from opencode_agent_hub import config

_agent_message_times: dict[str, list[float]] = {}


def check_rate_limit(agent_id: str) -> tuple[bool, str | None]:
    """Check if agent is within rate limits.

    Returns (allowed, rejection_reason).
    If allowed is False, rejection_reason explains why.
    """
    if not config.RATE_LIMIT_ENABLED:
        return True, None

    now = time.time()

    if agent_id not in _agent_message_times:
        _agent_message_times[agent_id] = []

    times = _agent_message_times[agent_id]

    if config.RATE_LIMIT_COOLDOWN_SECONDS > 0 and times:
        last_msg = times[-1]
        elapsed = now - last_msg
        if elapsed < config.RATE_LIMIT_COOLDOWN_SECONDS:
            remaining = int(config.RATE_LIMIT_COOLDOWN_SECONDS - elapsed)
            return False, f"Cooldown: wait {remaining}s before sending again"

    window_start = now - config.RATE_LIMIT_WINDOW_SECONDS
    times[:] = [t for t in times if t > window_start]

    if len(times) >= config.RATE_LIMIT_MAX_MESSAGES:
        return (
            False,
            f"Rate limit: max {config.RATE_LIMIT_MAX_MESSAGES} messages per {config.RATE_LIMIT_WINDOW_SECONDS}s",
        )

    return True, None


def record_message_sent(agent_id: str) -> None:
    """Record that an agent sent a message (for rate limiting)."""
    if not config.RATE_LIMIT_ENABLED:
        return

    now = time.time()
    if agent_id not in _agent_message_times:
        _agent_message_times[agent_id] = []
    _agent_message_times[agent_id].append(now)
    config.log.debug(f"Recorded message from {agent_id} at {now}")
