"""Tests for watchdog file event routing into daemon work queues."""

from pathlib import Path

from watchdog.events import FileCreatedEvent, FileMovedEvent


def test_message_handler_queues_json_created_file() -> None:
    """MessageHandler should queue directly-created message JSON files."""
    from opencode_agent_hub.messaging import MessageHandler, get_message_queue

    queue = get_message_queue()
    queue.queue.clear()
    path = Path("/tmp/agent-hub/messages/msg-1.json")

    MessageHandler().on_created(FileCreatedEvent(str(path)))

    assert queue.qsize() == 1
    assert queue.get_nowait().path == path


def test_message_handler_queues_json_moved_into_place() -> None:
    """MessageHandler should queue files finalized by atomic rename."""
    from opencode_agent_hub.messaging import MessageHandler, get_message_queue

    queue = get_message_queue()
    queue.queue.clear()
    src = Path("/tmp/agent-hub/messages/msg-1.tmp.123")
    dest = Path("/tmp/agent-hub/messages/msg-1.json")

    MessageHandler().on_moved(FileMovedEvent(str(src), str(dest)))

    assert queue.qsize() == 1
    assert queue.get_nowait().path == dest


def test_message_handler_ignores_tmp_create_before_atomic_rename() -> None:
    """MessageHandler should not process temporary atomic-write files."""
    from opencode_agent_hub.messaging import MessageHandler, get_message_queue

    queue = get_message_queue()
    queue.queue.clear()

    MessageHandler().on_created(FileCreatedEvent("/tmp/agent-hub/messages/msg-1.tmp.123"))

    assert queue.qsize() == 0


def test_session_handler_queues_session_moved_into_place() -> None:
    """SessionHandler should orient session files finalized by atomic rename."""
    from opencode_agent_hub.messaging import SessionHandler, _session_queue

    _session_queue.queue.clear()
    src = Path("/tmp/opencode/ses_abc.tmp.123")
    dest = Path("/tmp/opencode/ses_abc.json")

    SessionHandler().on_moved(FileMovedEvent(str(src), str(dest)))

    assert _session_queue.qsize() == 1
    assert _session_queue.get_nowait().path == dest


def test_agent_handler_reloads_agent_moved_into_place() -> None:
    """AgentHandler should reload registrations finalized by atomic rename."""
    from opencode_agent_hub.messaging import AgentHandler

    calls: list[Path] = []

    class RecordingAgentHandler(AgentHandler):
        def _handle_agent_file(self, path: Path) -> None:
            calls.append(path)

    src = Path("/tmp/agent-hub/agents/agent.tmp.123")
    dest = Path("/tmp/agent-hub/agents/agent.json")

    RecordingAgentHandler({}).on_moved(FileMovedEvent(str(src), str(dest)))

    assert calls == [dest]
