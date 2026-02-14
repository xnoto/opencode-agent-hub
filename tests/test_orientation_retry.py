"""Tests for orientation retry logic.

Verifies that the daemon retries orientation injection for sessions
that haven't responded, respecting delay and max retry limits.
"""

import time
from unittest import mock


def _reset_orientation_state():
    """Clear orientation retry state between tests."""
    from opencode_agent_hub import daemon

    daemon.ORIENTATION_PENDING.clear()
    daemon.ORIENTED_SESSIONS.clear()


def test_orient_session_adds_to_pending():
    """After orienting a session, it should be tracked in ORIENTATION_PENDING."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_test_pending_001"
        agent = {"id": "test-agent-1", "projectPath": "/tmp/test"}
        all_agents = {"test-agent-1": agent}

        with (
            mock.patch.object(daemon, "inject_message"),
            mock.patch.object(daemon, "notify_coordinator_new_agent"),
            mock.patch.object(daemon, "save_oriented_sessions"),
            mock.patch.object(daemon, "COORDINATOR_SESSION_ID", None),
        ):
            result = daemon.orient_session(session_id, agent, all_agents)

        assert result is True
        assert session_id in daemon.ORIENTATION_PENDING
        pending = daemon.ORIENTATION_PENDING[session_id]
        assert pending["retries"] == 0
        assert pending["agent_id"] == "test-agent-1"
        assert pending["oriented_at"] > 0
    finally:
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_orient_session_no_pending_when_retry_disabled():
    """When ORIENTATION_RETRY_MAX=0, sessions should NOT be added to ORIENTATION_PENDING."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_MAX = 0

    try:
        session_id = "ses_test_no_retry_001"
        agent = {"id": "test-agent-2", "projectPath": "/tmp/test"}
        all_agents = {"test-agent-2": agent}

        with (
            mock.patch.object(daemon, "inject_message"),
            mock.patch.object(daemon, "notify_coordinator_new_agent"),
            mock.patch.object(daemon, "save_oriented_sessions"),
            mock.patch.object(daemon, "COORDINATOR_SESSION_ID", None),
        ):
            result = daemon.orient_session(session_id, agent, all_agents)

        assert result is True
        assert session_id not in daemon.ORIENTATION_PENDING
    finally:
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_coordinator_session_not_added_to_pending():
    """Coordinator sessions should be oriented but NOT tracked for retry."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_coordinator_001"
        agent = {"id": "coordinator", "projectPath": "/tmp/coordinator"}
        all_agents = {"coordinator": agent}

        with (
            mock.patch.object(daemon, "save_oriented_sessions"),
            mock.patch.object(daemon, "COORDINATOR_SESSION_ID", session_id),
        ):
            result = daemon.orient_session(session_id, agent, all_agents)

        assert result is True
        # Coordinator should NOT be in pending
        assert session_id not in daemon.ORIENTATION_PENDING
    finally:
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_no_retry_before_delay_elapsed():
    """check_orientation_retries should not retry before ORIENTATION_RETRY_DELAY."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_test_early_001"
        # Set oriented_at to just now
        daemon.ORIENTATION_PENDING[session_id] = {
            "oriented_at": time.time(),
            "retries": 0,
            "agent_id": "test-agent-3",
        }

        # Agent hasn't responded (lastSeen is before oriented_at)
        agents = {
            "test-agent-3": {
                "id": "test-agent-3",
                "lastSeen": 0,
                "projectPath": "/tmp/test",
            }
        }

        with mock.patch.object(daemon, "inject_message") as mock_inject:
            daemon.check_orientation_retries(agents)

        # Should NOT have retried (delay not elapsed)
        mock_inject.assert_not_called()
        # Should still be pending
        assert session_id in daemon.ORIENTATION_PENDING
        assert daemon.ORIENTATION_PENDING[session_id]["retries"] == 0
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_retry_fires_after_delay():
    """check_orientation_retries should re-inject after ORIENTATION_RETRY_DELAY."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_test_retry_001"
        # Set oriented_at to 61 seconds ago
        daemon.ORIENTATION_PENDING[session_id] = {
            "oriented_at": time.time() - 61,
            "retries": 0,
            "agent_id": "test-agent-4",
        }

        agents = {
            "test-agent-4": {
                "id": "test-agent-4",
                "lastSeen": 0,
                "projectPath": "/tmp/test",
            }
        }

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "format_orientation", return_value="orientation-text"),
        ):
            daemon.check_orientation_retries(agents)

        # Should have retried
        mock_inject.assert_called_once_with(session_id, "orientation-text")
        # Retries should be incremented
        assert daemon.ORIENTATION_PENDING[session_id]["retries"] == 1
        # oriented_at should be refreshed to ~now
        assert daemon.ORIENTATION_PENDING[session_id]["oriented_at"] > time.time() - 5
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_agent_responded_clears_pending():
    """When agent's lastSeen is newer than oriented_at, remove from pending."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_test_responded_001"
        oriented_at = time.time() - 30  # 30 seconds ago
        daemon.ORIENTATION_PENDING[session_id] = {
            "oriented_at": oriented_at,
            "retries": 0,
            "agent_id": "test-agent-5",
        }

        # Agent responded: lastSeen (ms) is newer than oriented_at (s)
        agents = {
            "test-agent-5": {
                "id": "test-agent-5",
                "lastSeen": (oriented_at + 10) * 1000,  # 10s after orientation, in ms
                "projectPath": "/tmp/test",
            }
        }

        with mock.patch.object(daemon, "inject_message") as mock_inject:
            daemon.check_orientation_retries(agents)

        # Should NOT have retried (agent responded)
        mock_inject.assert_not_called()
        # Should be removed from pending
        assert session_id not in daemon.ORIENTATION_PENDING
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_max_retries_gives_up():
    """After ORIENTATION_RETRY_MAX retries, session is removed from pending."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_test_giveup_001"
        daemon.ORIENTATION_PENDING[session_id] = {
            "oriented_at": time.time() - 61,
            "retries": 2,  # Already at max
            "agent_id": "test-agent-6",
        }

        agents = {
            "test-agent-6": {
                "id": "test-agent-6",
                "lastSeen": 0,
                "projectPath": "/tmp/test",
            }
        }

        with mock.patch.object(daemon, "inject_message") as mock_inject:
            daemon.check_orientation_retries(agents)

        # Should NOT have retried (max reached)
        mock_inject.assert_not_called()
        # Should be removed from pending (gave up)
        assert session_id not in daemon.ORIENTATION_PENDING
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_empty_pending_is_noop():
    """check_orientation_retries with empty ORIENTATION_PENDING should be a no-op."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    with mock.patch.object(daemon, "inject_message") as mock_inject:
        daemon.check_orientation_retries({})

    mock_inject.assert_not_called()


def test_unknown_agent_still_retries():
    """If the agent_id is not in the agents dict, retry should still fire."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        session_id = "ses_test_unknown_001"
        daemon.ORIENTATION_PENDING[session_id] = {
            "oriented_at": time.time() - 61,
            "retries": 0,
            "agent_id": "nonexistent-agent",
        }

        # Agent not in agents dict at all
        agents: dict[str, dict] = {}

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "format_orientation", return_value="retry-text"),
        ):
            daemon.check_orientation_retries(agents)

        # Should still retry (agent not found doesn't mean responded)
        mock_inject.assert_called_once_with(session_id, "retry-text")
        assert daemon.ORIENTATION_PENDING[session_id]["retries"] == 1
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_multiple_sessions_independent():
    """Multiple pending sessions are processed independently."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 2

    try:
        now = time.time()

        # Session A: responded (should be removed)
        daemon.ORIENTATION_PENDING["ses_a"] = {
            "oriented_at": now - 30,
            "retries": 0,
            "agent_id": "agent-a",
        }

        # Session B: delay not elapsed (should stay, no retry)
        daemon.ORIENTATION_PENDING["ses_b"] = {
            "oriented_at": now - 10,
            "retries": 0,
            "agent_id": "agent-b",
        }

        # Session C: delay elapsed, should retry
        daemon.ORIENTATION_PENDING["ses_c"] = {
            "oriented_at": now - 61,
            "retries": 0,
            "agent_id": "agent-c",
        }

        # Session D: max retries reached, should give up
        daemon.ORIENTATION_PENDING["ses_d"] = {
            "oriented_at": now - 61,
            "retries": 2,
            "agent_id": "agent-d",
        }

        agents = {
            "agent-a": {
                "id": "agent-a",
                "lastSeen": (now - 20) * 1000,  # Responded 20s ago
                "projectPath": "/tmp/a",
            },
            "agent-b": {
                "id": "agent-b",
                "lastSeen": 0,
                "projectPath": "/tmp/b",
            },
            "agent-c": {
                "id": "agent-c",
                "lastSeen": 0,
                "projectPath": "/tmp/c",
            },
            "agent-d": {
                "id": "agent-d",
                "lastSeen": 0,
                "projectPath": "/tmp/d",
            },
        }

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "format_orientation", return_value="orientation-text"),
        ):
            daemon.check_orientation_retries(agents)

        # Only session C should have been retried
        mock_inject.assert_called_once_with("ses_c", "orientation-text")

        # Session A: removed (responded)
        assert "ses_a" not in daemon.ORIENTATION_PENDING
        # Session B: still pending (delay not elapsed)
        assert "ses_b" in daemon.ORIENTATION_PENDING
        assert daemon.ORIENTATION_PENDING["ses_b"]["retries"] == 0
        # Session C: still pending, retries incremented
        assert "ses_c" in daemon.ORIENTATION_PENDING
        assert daemon.ORIENTATION_PENDING["ses_c"]["retries"] == 1
        # Session D: removed (gave up)
        assert "ses_d" not in daemon.ORIENTATION_PENDING
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()


def test_metrics_incremented_on_retry():
    """Verify metrics are incremented on retry and give-up."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()

    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_retry_max = daemon.ORIENTATION_RETRY_MAX
    daemon.ORIENTATION_RETRY_DELAY = 60
    daemon.ORIENTATION_RETRY_MAX = 1

    try:
        now = time.time()

        # Session that will be retried
        daemon.ORIENTATION_PENDING["ses_retry"] = {
            "oriented_at": now - 61,
            "retries": 0,
            "agent_id": "agent-retry",
        }

        # Session that will give up
        daemon.ORIENTATION_PENDING["ses_giveup"] = {
            "oriented_at": now - 61,
            "retries": 1,  # Already at max (ORIENTATION_RETRY_MAX=1)
            "agent_id": "agent-giveup",
        }

        agents = {
            "agent-retry": {"id": "agent-retry", "lastSeen": 0, "projectPath": "/tmp"},
            "agent-giveup": {"id": "agent-giveup", "lastSeen": 0, "projectPath": "/tmp"},
        }

        with (
            mock.patch.object(daemon, "inject_message"),
            mock.patch.object(daemon, "format_orientation", return_value="text"),
            mock.patch.object(daemon.metrics, "inc") as mock_inc,
        ):
            daemon.check_orientation_retries(agents)

        # Should have incremented both metrics
        calls = [c[0][0] for c in mock_inc.call_args_list]
        assert "agent_hub_orientation_retries_total" in calls
        assert "agent_hub_orientation_gave_up_total" in calls
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_retry_max
        _reset_orientation_state()
