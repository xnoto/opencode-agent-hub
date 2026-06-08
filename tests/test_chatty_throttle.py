"""Tests for route-specific chatty throttling and dropped-agent handling."""

import json
import queue
import threading
import time
from pathlib import Path
from unittest import mock

import opencode_agent_hub.messaging as messaging
from opencode_agent_hub.messaging import (
    _prepare_injection_task,
    _release_delayed_injection_tasks,
    _reset_route_throttle_state,
    injection_worker,
    process_message_file,
)
from opencode_agent_hub.metrics import metrics
from opencode_agent_hub.models import InjectionTask


def _make_task(
    *, sender: str = "coordinator", target: str = "agent-a", thread: str = "thr-1"
) -> InjectionTask:
    return InjectionTask(
        session_id="ses_test",
        text="hello",
        original_sender=sender,
        original_message_id="msg-1",
        thread_id=thread,
        target_agent=target,
    )


def setup_function() -> None:
    """Reset global throttle and metrics state between tests."""
    _reset_route_throttle_state()
    metrics.reset()


def test_route_throttle_delays_fourth_message_same_route() -> None:
    """Fourth message on the same route inside the window should be delayed."""
    with (
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_ENABLED", True),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_MAX_MESSAGES", 3),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_WINDOW_SECONDS", 15),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_COOLDOWN_SECONDS", 15),
    ):
        first = _prepare_injection_task(_make_task(), now=100.0)
        second = _prepare_injection_task(_make_task(), now=101.0)
        third = _prepare_injection_task(_make_task(), now=102.0)
        fourth = _prepare_injection_task(_make_task(), now=103.0)

    assert first is not None
    assert second is not None
    assert third is not None
    assert fourth is None
    assert metrics.get("agent_hub_chatty_throttle_triggered_total") == 1
    assert metrics.get("agent_hub_chatty_throttle_delayed_total") == 1


def test_route_throttle_isolated_by_thread_and_direction() -> None:
    """Different threads and reverse directions should not share a cooldown."""
    with (
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_ENABLED", True),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_MAX_MESSAGES", 3),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_WINDOW_SECONDS", 15),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_COOLDOWN_SECONDS", 15),
    ):
        for offset in range(3):
            assert (
                _prepare_injection_task(_make_task(thread="thr-1"), now=100.0 + offset) is not None
            )

        assert _prepare_injection_task(_make_task(thread="thr-1"), now=103.0) is None
        assert _prepare_injection_task(_make_task(thread="thr-2"), now=103.0) is not None
        assert (
            _prepare_injection_task(
                _make_task(sender="agent-a", target="coordinator", thread="thr-1"),
                now=103.0,
            )
            is not None
        )


def test_route_throttle_releases_delayed_messages_in_order_after_cooldown() -> None:
    """Delayed messages should be released in FIFO order after cooldown expiry."""
    with (
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_ENABLED", True),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_MAX_MESSAGES", 3),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_WINDOW_SECONDS", 15),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_COOLDOWN_SECONDS", 15),
    ):
        for offset in range(3):
            assert (
                _prepare_injection_task(
                    _make_task(thread="thr-1", sender="coordinator", target="agent-a"),
                    now=100.0 + offset,
                )
                is not None
            )

        delayed_fourth = _make_task(thread="thr-1", sender="coordinator", target="agent-a")
        delayed_fourth.original_message_id = "msg-4"
        delayed_fifth = _make_task(thread="thr-1", sender="coordinator", target="agent-a")
        delayed_fifth.original_message_id = "msg-5"

        assert _prepare_injection_task(delayed_fourth, now=103.0) is None
        assert _prepare_injection_task(delayed_fifth, now=104.0) is None

        ready = _release_delayed_injection_tasks(now=119.0)

    assert [task.original_message_id for task in ready] == ["msg-4", "msg-5"]
    assert metrics.get("agent_hub_chatty_throttle_released_total") == 2


def test_injection_worker_holds_fourth_message_until_cooldown_expires() -> None:
    """Worker should not deliver the fourth message until the route cooldown expires."""

    class FakeClock:
        def __init__(self, now: float) -> None:
            self.now = now

        def time(self) -> float:
            return self.now

    fake_clock = FakeClock(100.0)
    delivered: list[tuple[float, str]] = []
    shutdown_event = threading.Event()
    test_queue: queue.Queue[InjectionTask] = queue.Queue()

    def capture_inject(session_id: str, text: str, *, model=None, agent=None) -> bool:
        delivered.append((fake_clock.now, text))
        return True

    tasks = []
    for idx in range(1, 5):
        task = _make_task(thread="thr-worker", sender="coordinator", target="agent-a")
        task.text = f"message-{idx}"
        task.original_message_id = f"msg-{idx}"
        tasks.append(task)

    worker = threading.Thread(target=injection_worker, args=(shutdown_event,), daemon=True)

    with (
        mock.patch.object(messaging, "_injection_queue", test_queue),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_ENABLED", True),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_MAX_MESSAGES", 3),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_WINDOW_SECONDS", 15),
        mock.patch("opencode_agent_hub.messaging.CHATTY_THROTTLE_COOLDOWN_SECONDS", 15),
        mock.patch("opencode_agent_hub.messaging._ROUTE_RELEASE_POLL_SECONDS", 0.01),
        mock.patch("opencode_agent_hub.messaging.inject_message_sync", side_effect=capture_inject),
        mock.patch("opencode_agent_hub.messaging.get_session_agent", return_value=None),
        mock.patch("opencode_agent_hub.messaging.time.time", side_effect=fake_clock.time),
        mock.patch("opencode_agent_hub.config.DEFAULT_AGENT", None),
        mock.patch("opencode_agent_hub.config.AGENT_MODELS", {}),
        mock.patch("opencode_agent_hub.config.COORDINATOR_SESSION_ID", None),
        mock.patch("opencode_agent_hub.config.COORDINATOR_MODEL", None),
        mock.patch("opencode_agent_hub.config.COORDINATOR_AGENT", None),
    ):
        worker.start()
        for task in tasks:
            test_queue.put(task)

        test_queue.join()
        time.sleep(0.05)

        assert [text for _, text in delivered] == ["message-1", "message-2", "message-3"]

        fake_clock.now = 116.0
        time.sleep(0.05)

        shutdown_event.set()
        worker.join(timeout=1)

    assert [text for _, text in delivered] == ["message-1", "message-2", "message-3", "message-4"]
    assert delivered[-1][0] >= 115.0


def test_process_message_file_reports_unknown_agent_for_dropped_route(tmp_path: Path) -> None:
    """A message to an unregistered agent should emit unknown_agent feedback."""
    messages_dir = tmp_path / "messages"
    archive_dir = messages_dir / "archive"
    messages_dir.mkdir()
    archive_dir.mkdir()

    message_path = messages_dir / "msg.json"
    message_path.write_text(
        json.dumps(
            {
                "id": "msg-unknown-agent",
                "from": "coordinator",
                "to": "strong-koala",
                "type": "message",
                "content": "ping",
                "timestamp": 100.0,
            }
        )
    )

    with (
        mock.patch("opencode_agent_hub.messaging.MESSAGES_DIR", messages_dir),
        mock.patch("opencode_agent_hub.messaging.ARCHIVE_DIR", archive_dir),
        mock.patch("opencode_agent_hub.messaging.check_rate_limit", return_value=(True, "ok")),
        mock.patch("opencode_agent_hub.messaging.record_message_sent"),
        mock.patch("opencode_agent_hub.messaging.ensure_thread_id"),
        mock.patch("opencode_agent_hub.messaging.check_thread_resolution", return_value=False),
    ):
        process_message_file(message_path, agents={})

    feedback_files = sorted(messages_dir.glob("feedback-*.json"))
    assert len(feedback_files) == 1

    feedback = json.loads(feedback_files[0].read_text())
    assert feedback["to"] == "coordinator"
    assert feedback["status"] == "failed"
    assert feedback["reason"] == "unknown_agent"
    assert feedback["errors"] == ["No registered agent with id 'strong-koala'"]
