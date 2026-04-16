"""Tests for orientation retry logic.

Verifies that the daemon retries orientation injection for sessions
that haven't responded, respecting delay and max retry limits.
"""

import time
from unittest import mock

from opencode_agent_hub import config
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.sessions import (
    check_orientation_retries,
    format_orientation,
    orient_session,
)


def _reset_orientation_state() -> None:
    """Clear orientation retry state between tests."""
    config.ORIENTATION_PENDING.clear()
    config.ORIENTED_SESSIONS.clear()


def test_orient_session_tracks_oriented() -> None:
    """After orienting a session, it should be tracked in ORIENTED_SESSIONS."""
    _reset_orientation_state()

    try:
        session_id = "ses_test_oriented"
        agent = {"id": "test-agent-1", "projectPath": "/tmp/test"}
        all_agents = {"test-agent-1": agent}

        with (
            mock.patch("opencode_agent_hub.messaging.inject_message"),
            mock.patch("opencode_agent_hub.sessions.get_session_agent", return_value="gpt"),
            mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
            mock.patch.object(config, "COORDINATOR_SESSION_ID", None),
        ):
            result = orient_session(session_id, "/tmp/test", all_agents)

        assert result is True
        assert session_id in config.ORIENTED_SESSIONS
    finally:
        _reset_orientation_state()


def test_orient_session_no_retry_when_already_oriented() -> None:
    """When session is already oriented, orient_session should return False."""
    _reset_orientation_state()

    try:
        session_id = "ses_already_oriented"
        config.ORIENTED_SESSIONS.add(session_id)

        agent = {"id": "test-agent-2", "projectPath": "/tmp/test2"}
        all_agents = {"test-agent-2": agent}

        with (
            mock.patch("opencode_agent_hub.messaging.inject_message") as mock_inject,
            mock.patch.object(config, "COORDINATOR_SESSION_ID", None),
        ):
            result = orient_session(session_id, "/tmp/test2", all_agents)

        # Should return False since already oriented
        assert result is False
        # Should not inject again
        mock_inject.assert_not_called()
    finally:
        _reset_orientation_state()


def test_coordinator_session_added_to_oriented() -> None:
    """Coordinator session should be added to ORIENTED_SESSIONS."""
    _reset_orientation_state()

    try:
        session_id = "ses_coordinator_123"
        agent = {"id": "coordinator", "projectPath": "/tmp/coordinator"}
        all_agents = {"coordinator": agent}

        with (
            mock.patch.object(config, "COORDINATOR_SESSION_ID", session_id),
            mock.patch("opencode_agent_hub.messaging.inject_message"),
            mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
        ):
            result = orient_session(session_id, "/tmp/coordinator", all_agents)

        assert result is True
        assert session_id in config.ORIENTED_SESSIONS
    finally:
        _reset_orientation_state()


def test_check_orientation_clears_resolved() -> None:
    """check_orientation_retries should clear resolved sessions."""
    _reset_orientation_state()

    session_id = "ses_resolved"
    agent_id = "agent-resolved"

    # Setup: pending session
    config.ORIENTATION_PENDING[session_id] = {
        "agent_id": agent_id,
        "oriented_at": time.time(),
        "retries": 0,
    }

    # Agent is now registered
    agents = {agent_id: {"id": agent_id}}

    check_orientation_retries(agents)

    # Should be cleared since agent registered
    assert session_id not in config.ORIENTATION_PENDING


def test_check_orientation_retries_give_up() -> None:
    """check_orientation_retries should give up after max retries."""
    _reset_orientation_state()

    original_retry_max = config.ORIENTATION_RETRY_MAX
    original_delay = config.ORIENTATION_RETRY_DELAY
    config.ORIENTATION_RETRY_MAX = 2
    config.ORIENTATION_RETRY_DELAY = 1

    try:
        session_id = "ses_give_up"
        agent_id = "agent-give-up"

        # Setup: max retries exceeded (retries >= max)
        config.ORIENTATION_PENDING[session_id] = {
            "agent_id": agent_id,
            "oriented_at": time.time() - 5,  # 5 seconds ago (> delay)
            "retries": 3,  # Above max of 2
        }

        agents: dict[str, dict] = {}  # Agent not registered

        with mock.patch.object(metrics, "inc") as mock_inc:
            check_orientation_retries(agents)

        # Should be removed
        assert session_id not in config.ORIENTATION_PENDING
        # Should have logged gave up
        mock_inc.assert_any_call("agent_hub_orientation_gave_up_total")
    finally:
        config.ORIENTATION_RETRY_MAX = original_retry_max
        config.ORIENTATION_RETRY_DELAY = original_delay
        _reset_orientation_state()


def test_check_orientation_retries_increment() -> None:
    """check_orientation_retries should increment retry count."""
    _reset_orientation_state()

    original_retry_max = config.ORIENTATION_RETRY_MAX
    original_delay = config.ORIENTATION_RETRY_DELAY
    config.ORIENTATION_RETRY_MAX = 3
    config.ORIENTATION_RETRY_DELAY = 1

    try:
        session_id = "ses_retry_inc"
        agent_id = "agent-retry-inc"

        # Setup: needs retry (oriented long ago, below max)
        config.ORIENTATION_PENDING[session_id] = {
            "agent_id": agent_id,
            "oriented_at": time.time() - 5,  # 5 seconds ago (> delay)
            "retries": 0,
        }

        agents: dict[str, dict] = {}

        with mock.patch.object(metrics, "inc") as mock_inc:
            check_orientation_retries(agents)

        # Should have incremented
        assert config.ORIENTATION_PENDING[session_id]["retries"] == 1
        # Should have logged retry
        mock_inc.assert_any_call("agent_hub_orientation_retries_total")
    finally:
        config.ORIENTATION_RETRY_MAX = original_retry_max
        config.ORIENTATION_RETRY_DELAY = original_delay
        _reset_orientation_state()


def test_check_orientation_no_retry_before_delay() -> None:
    """check_orientation_retries should not retry before delay elapsed."""
    _reset_orientation_state()

    original_delay = config.ORIENTATION_RETRY_DELAY
    config.ORIENTATION_RETRY_DELAY = 60  # 60 seconds

    try:
        session_id = "ses_no_retry_yet"
        agent_id = "agent-no-retry"

        # Setup: oriented recently (not enough time passed)
        config.ORIENTATION_PENDING[session_id] = {
            "agent_id": agent_id,
            "oriented_at": time.time() - 5,  # 5 seconds ago (< 60s delay)
            "retries": 0,
        }

        agents: dict[str, dict] = {}

        with mock.patch.object(metrics, "inc") as mock_inc:
            check_orientation_retries(agents)

        # Should NOT have retried yet
        assert config.ORIENTATION_PENDING[session_id]["retries"] == 0
        # Should not have logged retry
        mock_inc.assert_not_called()
    finally:
        config.ORIENTATION_RETRY_DELAY = original_delay
        _reset_orientation_state()


def test_empty_pending_is_noop() -> None:
    """When ORIENTATION_PENDING is empty, check should be a no-op."""
    _reset_orientation_state()

    agents: dict[str, dict] = {}

    # Should not raise or error
    check_orientation_retries(agents)

    # Pending should still be empty
    assert len(config.ORIENTATION_PENDING) == 0


def test_format_orientation_with_agent_id_and_path() -> None:
    """Test imperative format with actual values."""
    result = format_orientation(
        {},
        agent_id="warm-mamba",
        project_path="/home/user/project",
    )

    assert "AGENT HUB:" in result
    assert "Your assigned ID: warm-mamba" in result
    assert "Project path: /home/user/project" in result
    assert 'EXECUTE NOW: agent-hub_register_agent("warm-mamba", "/home/user/project"' in result


def test_format_orientation_without_params() -> None:
    """Test fallback format without values."""
    result = format_orientation({})

    assert "AGENT HUB:" in result
    assert "EXECUTE NOW: agent-hub_register_agent" in result
    assert "<choose-your-own-name>" in result
    assert "<your-project-path>" in result


def test_format_orientation_includes_tools() -> None:
    """format_orientation should mention available tools."""
    result = format_orientation({})

    assert "AGENT HUB:" in result
    assert "EXECUTE NOW: agent-hub_register_agent" in result
    assert "agent-hub_send_message" in result


def test_orient_session_skips_without_session_id() -> None:
    """orient_session should return False for empty session_id."""
    result = orient_session("", "/tmp/test", {})
    assert result is False


def test_verify_session_processing_detects_agent_hub_message() -> None:
    """Test verification detects new AGENT HUB: message format."""
    from opencode_agent_hub.sessions import _verify_session_processing

    session_id = "ses_test_verify"
    orientation_text = "AGENT HUB: You must register now"

    # Mock API response with AGENT HUB message
    mock_messages = [
        {
            "info": {"time": 1000, "role": "user"},
            "parts": [{"type": "text", "text": "AGENT HUB: You must register now"}],
        }
    ]

    with (
        mock.patch("requests.get") as mock_get,
        mock.patch("time.sleep"),  # Skip delays
    ):
        mock_response = mock.MagicMock()
        mock_response.json.return_value = mock_messages
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # Should not raise and should detect the message
        _verify_session_processing(session_id, orientation_text)

        mock_get.assert_called()
