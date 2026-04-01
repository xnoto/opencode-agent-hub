"""Tests for message injection with agent parameter."""

import json
import tempfile
from pathlib import Path
from unittest import mock


def test_inject_message_passes_agent_to_payload() -> None:
    """Verify agent parameter is included in payload when provided."""
    from opencode_agent_hub import daemon

    mock_response = mock.MagicMock()
    mock_response.status_code = 204

    with mock.patch("requests.post", return_value=mock_response) as mock_post:
        daemon.inject_message_sync("ses_test", "test message", agent="kimi")

        # Verify the call was made with correct payload
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]

        assert payload["agent"] == "kimi"
        assert payload["parts"] == [{"type": "text", "text": "test message"}]


def test_inject_message_omits_agent_when_none() -> None:
    """Verify agent is not included in payload when None."""
    from opencode_agent_hub import daemon

    mock_response = mock.MagicMock()
    mock_response.status_code = 204

    with mock.patch("requests.post", return_value=mock_response) as mock_post:
        daemon.inject_message_sync("ses_test", "test message", agent=None)

        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]

        assert "agent" not in payload


def test_inject_message_omits_agent_when_not_provided() -> None:
    """Verify agent is not included when parameter not provided."""
    from opencode_agent_hub import daemon

    mock_response = mock.MagicMock()
    mock_response.status_code = 204

    with mock.patch("requests.post", return_value=mock_response) as mock_post:
        daemon.inject_message_sync("ses_test", "test message")

        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]

        assert "agent" not in payload


def test_inject_message_task_passes_agent() -> None:
    """Verify InjectionTask correctly passes agent through queue."""
    from opencode_agent_hub import daemon

    # Clear queue
    while not daemon._injection_queue.empty():
        daemon._injection_queue.get()

    # Add task with agent
    daemon.inject_message("ses_test", "test message", agent="claude")

    # Verify task has agent
    task = daemon._injection_queue.get(timeout=0.1)
    assert task.session_id == "ses_test"
    assert task.text == "test message"
    assert task.agent == "claude"

    daemon._injection_queue.task_done()


def test_process_message_file_passes_target_agent() -> None:
    """Verify process_message_file passes target agent (not sender) to inject_message."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test message
        msg_path = Path(tmpdir) / "msg_test.json"
        message = {
            "from": "sender-agent",
            "to": "receiver-agent",
            "type": "task",
            "content": "Do something",
        }
        msg_path.write_text(json.dumps(message))

        # Set up agents
        agents = {
            "sender-agent": {"id": "sender-agent", "projectPath": "/sender"},
            "receiver-agent": {
                "id": "receiver-agent",
                "projectPath": "/receiver",
                "sessionId": "ses_receiver",
            },
        }

        # Mock sessions
        mock_sessions = [
            {
                "id": "ses_receiver",
                "directory": "/receiver",
                "time": {"created": daemon.DAEMON_START_TIME_MS + 1000},
            }
        ]

        with (
            mock.patch.object(daemon, "get_sessions", return_value=mock_sessions),
            mock.patch.object(daemon, "inject_message") as mock_inject,
        ):
            daemon.process_message_file(msg_path, agents)

            # Verify inject_message was called with TARGET agent (receiver-agent)
            mock_inject.assert_called_once()
            call_args = mock_inject.call_args
            assert call_args[0][0] == "ses_receiver"  # session_id
            assert (
                call_args[1]["agent"] == "receiver-agent"
            )  # agent parameter (target, not sender!)


def test_process_message_file_broadcast_to_all() -> None:
    """Verify broadcast messages pass correct agent to each recipient."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a broadcast message
        msg_path = Path(tmpdir) / "msg_broadcast.json"
        message = {
            "from": "sender-agent",
            "to": "all",
            "type": "announcement",
            "content": "Hello everyone",
        }
        msg_path.write_text(json.dumps(message))

        # Set up multiple agents
        agents = {
            "sender-agent": {"id": "sender-agent", "projectPath": "/sender"},
            "agent-a": {"id": "agent-a", "projectPath": "/project-a", "sessionId": "ses_a"},
            "agent-b": {"id": "agent-b", "projectPath": "/project-b", "sessionId": "ses_b"},
        }

        mock_sessions = [
            {
                "id": "ses_a",
                "directory": "/project-a",
                "time": {"created": daemon.DAEMON_START_TIME_MS + 1000},
            },
            {
                "id": "ses_b",
                "directory": "/project-b",
                "time": {"created": daemon.DAEMON_START_TIME_MS + 1000},
            },
        ]

        with (
            mock.patch.object(daemon, "get_sessions", return_value=mock_sessions),
            mock.patch.object(daemon, "inject_message") as mock_inject,
        ):
            daemon.process_message_file(msg_path, agents)

            # Should inject to both agents (not sender)
            assert mock_inject.call_count == 2

            # Get all calls
            calls = mock_inject.call_args_list

            # First call should be to agent-a with agent-a as the handler
            assert calls[0][0][0] == "ses_a"
            assert calls[0][1]["agent"] == "agent-a"

            # Second call should be to agent-b with agent-b as the handler
            assert calls[1][0][0] == "ses_b"
            assert calls[1][1]["agent"] == "agent-b"


def test_orient_session_passes_agent() -> None:
    """Verify orient_session passes the target agent ID."""
    from opencode_agent_hub import daemon

    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.ORIENTED_SESSIONS = set()

        agent = {"id": "target-agent", "projectPath": "/test", "sessionId": "ses_test"}
        all_agents = {"target-agent": agent}

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "notify_coordinator_new_agent"),
        ):
            daemon.orient_session("ses_test", agent, all_agents)

            # Should inject with target agent ID
            mock_inject.assert_called_once()
            call_args = mock_inject.call_args
            assert call_args[0][0] == "ses_test"
            assert call_args[1]["agent"] == "target-agent"
    finally:
        daemon.ORIENTED_SESSIONS = original_oriented


def test_check_orientation_retries_passes_agent() -> None:
    """Verify check_orientation_retries passes agent ID for retry."""
    from opencode_agent_hub import daemon

    _reset_orientation_state()
    original_delay = daemon.ORIENTATION_RETRY_DELAY
    original_max = daemon.ORIENTATION_RETRY_MAX

    try:
        daemon.ORIENTATION_RETRY_DELAY = 60
        daemon.ORIENTATION_RETRY_MAX = 2

        session_id = "ses_retry_test"
        daemon.ORIENTATION_PENDING[session_id] = {
            "oriented_at": daemon.time.time() - 61,  # Over delay
            "retries": 0,
            "agent_id": "retry-agent",
        }

        agents = {
            "retry-agent": {
                "id": "retry-agent",
                "projectPath": "/test",
                "lastSeen": 0,
            }
        }

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "format_orientation", return_value="retry-text"),
        ):
            daemon.check_orientation_retries(agents)

            mock_inject.assert_called_once()
            call_args = mock_inject.call_args
            assert call_args[0][0] == session_id
            assert call_args[1]["agent"] == "retry-agent"
    finally:
        daemon.ORIENTATION_RETRY_DELAY = original_delay
        daemon.ORIENTATION_RETRY_MAX = original_max
        _reset_orientation_state()


def test_notify_coordinator_passes_coordinator_agent() -> None:
    """Verify coordinator notifications use coordinator agent."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coordinator"

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "_wait_for_coordinator_activity", return_value=True),
        ):
            daemon.notify_coordinator_new_agent("new-agent", "/new/path")

            # Should be called twice (initial + retry check)
            assert mock_inject.call_count == 1

            # Both calls should use "coordinator" as agent
            call = mock_inject.call_args
            assert call[0][0] == "ses_coordinator"
            assert call[1]["agent"] == "coordinator"
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id


def _reset_orientation_state():
    """Helper to reset orientation state between tests."""
    from opencode_agent_hub import daemon

    daemon.ORIENTED_SESSIONS = set()
    daemon.ORIENTATION_PENDING = {}
