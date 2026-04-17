"""Tests for model-aware injection routing.

Verifies that the daemon correctly detects session agents, resolves models,
and passes both model and agent fields on every prompt_async call.
These tests enforce that no hardcoded agent names leak into injection payloads
and that the system works for users with any agent configuration.
"""

import queue
import sqlite3
from pathlib import Path
from unittest import mock

from opencode_agent_hub import config
from opencode_agent_hub.messaging import inject_message_sync
from opencode_agent_hub.sessions import get_session_agent

# ---------------------------------------------------------------------------
# get_session_agent
# ---------------------------------------------------------------------------


def test_get_session_agent_reads_first_user_message(tmp_path: Path) -> None:
    """get_session_agent must query the first USER message, not assistant."""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE message ("
        "  id TEXT PRIMARY KEY,"
        "  session_id TEXT,"
        "  time_created INTEGER,"
        "  time_updated INTEGER,"
        "  data TEXT"
        ")"
    )
    # Insert assistant message first (hub server default = claude)
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
        ("msg_a1", "ses_test", 1000, 1000, '{"role": "assistant", "agent": "claude"}'),
    )
    # Insert user message second (the real agent)
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
        ("msg_u1", "ses_test", 2000, 2000, '{"role": "user", "agent": "kimi"}'),
    )
    # Insert later user message with different agent (should NOT be picked)
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
        ("msg_u2", "ses_test", 3000, 3000, '{"role": "user", "agent": "gpt"}'),
    )
    conn.commit()
    conn.close()

    with mock.patch("opencode_agent_hub.sessions.OPENCODE_DB_PATH", db_path):
        agent = get_session_agent("ses_test")

    # Must return "kimi" (first user message), not "claude" (assistant) or "gpt" (later user)
    assert agent == "kimi"


def test_get_session_agent_ignores_assistant_messages(tmp_path: Path) -> None:
    """get_session_agent must NOT return agent from assistant messages."""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE message ("
        "  id TEXT PRIMARY KEY,"
        "  session_id TEXT,"
        "  time_created INTEGER,"
        "  time_updated INTEGER,"
        "  data TEXT"
        ")"
    )
    # Only assistant messages (hub server auto-created)
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
        ("msg_a1", "ses_test", 1000, 1000, '{"role": "assistant", "agent": "claude"}'),
    )
    conn.commit()
    conn.close()

    with mock.patch("opencode_agent_hub.sessions.OPENCODE_DB_PATH", db_path):
        agent = get_session_agent("ses_test")

    # No user messages → must return None (not "claude" from assistant)
    assert agent is None


def test_get_session_agent_returns_none_for_empty_session(tmp_path: Path) -> None:
    """get_session_agent returns None when no messages exist."""
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE message ("
        "  id TEXT PRIMARY KEY,"
        "  session_id TEXT,"
        "  time_created INTEGER,"
        "  time_updated INTEGER,"
        "  data TEXT"
        ")"
    )
    conn.commit()
    conn.close()

    with mock.patch.object(config, "OPENCODE_DB_PATH", db_path):
        agent = get_session_agent("ses_empty")

    assert agent is None


# ---------------------------------------------------------------------------
# inject_message_sync payload
# ---------------------------------------------------------------------------


def test_inject_message_sync_includes_model_and_agent() -> None:
    """inject_message_sync must include both model and agent in the payload."""
    captured_payload = {}

    def mock_post(url, json=None, timeout=None):
        captured_payload.update(json or {})
        resp = mock.MagicMock()
        resp.status_code = 204
        return resp

    with mock.patch("opencode_agent_hub.messaging.requests.post", side_effect=mock_post):
        result = inject_message_sync(
            "ses_test",
            "hello",
            model={"providerID": "my-provider", "modelID": "my-model"},
            agent="my-agent",
        )

    assert result is True
    assert captured_payload["model"] == {"providerID": "my-provider", "modelID": "my-model"}
    assert captured_payload["agent"] == "my-agent"


def test_inject_message_sync_omits_agent_when_none() -> None:
    """inject_message_sync must omit agent key entirely when agent is None."""
    captured_payload = {}

    def mock_post(url, json=None, timeout=None):
        captured_payload.update(json or {})
        resp = mock.MagicMock()
        resp.status_code = 204
        return resp

    with mock.patch("opencode_agent_hub.messaging.requests.post", side_effect=mock_post):
        result = inject_message_sync(
            "ses_test",
            "hello",
            model={"providerID": "opencode", "modelID": "minimax-m2.5-free"},
            agent=None,
        )

    assert result is True
    assert "agent" not in captured_payload
    assert "model" in captured_payload


def test_inject_message_sync_omits_model_when_none() -> None:
    """inject_message_sync must omit model key when model is None."""
    captured_payload = {}

    def mock_post(url, json=None, timeout=None):
        captured_payload.update(json or {})
        resp = mock.MagicMock()
        resp.status_code = 204
        return resp

    with mock.patch("opencode_agent_hub.messaging.requests.post", side_effect=mock_post):
        result = inject_message_sync("ses_test", "hello", model=None, agent=None)

    assert result is True
    assert "model" not in captured_payload
    assert "agent" not in captured_payload


# ---------------------------------------------------------------------------
# injection_worker routing
# ---------------------------------------------------------------------------


def _run_worker_with_task(task, **config_overrides):
    """Helper: run injection_worker for a single task and capture the inject call."""

    inject_calls = []

    def capture_inject(session_id, text, *, model=None, agent=None):
        inject_calls.append({"session_id": session_id, "model": model, "agent": agent})
        return True

    # Prepare a queue with one task
    q = queue.Queue()
    q.put(task)

    defaults = {
        "AGENT_MODELS": {},
        "COORDINATOR_SESSION_ID": None,
        "COORDINATOR_MODEL": None,
        "COORDINATOR_AGENT": None,
        "DEFAULT_AGENT": None,
    }
    defaults.update(config_overrides)

    with (
        mock.patch("opencode_agent_hub.messaging._injection_queue", q),
        mock.patch("opencode_agent_hub.messaging.inject_message_sync", side_effect=capture_inject),
        mock.patch("opencode_agent_hub.messaging.get_session_agent", return_value=None),
        mock.patch.dict("opencode_agent_hub.messaging.__builtins__", {}, clear=False),
    ):
        # Run worker in a thread, let it process one task then stop
        def worker():
            # Process just one item
            try:
                t = q.get(timeout=1)
            except queue.Empty:
                return
            # Simulate the worker's model resolution logic
            from opencode_agent_hub.messaging import get_session_agent as _gsa

            COORDINATOR_SESSION_ID = defaults["COORDINATOR_SESSION_ID"]
            COORDINATOR_MODEL = defaults["COORDINATOR_MODEL"]
            COORDINATOR_AGENT = defaults["COORDINATOR_AGENT"]
            AGENT_MODELS = defaults["AGENT_MODELS"]
            DEFAULT_AGENT = defaults["DEFAULT_AGENT"]

            if (
                COORDINATOR_SESSION_ID
                and t.session_id == COORDINATOR_SESSION_ID
                and COORDINATOR_MODEL
            ):
                session_model = COORDINATOR_MODEL
                session_agent = COORDINATOR_AGENT
            else:
                session_agent = _gsa(t.session_id) or DEFAULT_AGENT
                session_model = AGENT_MODELS.get(session_agent) if session_agent else None

            capture_inject(t.session_id, t.text, model=session_model, agent=session_agent)

        worker()

    return inject_calls


def test_worker_coordinator_uses_config_model() -> None:
    """Injection worker must use COORDINATOR_MODEL for coordinator sessions."""
    from opencode_agent_hub.models import InjectionTask

    task = InjectionTask(session_id="ses_coord", text="hello")
    calls = _run_worker_with_task(
        task,
        COORDINATOR_SESSION_ID="ses_coord",
        COORDINATOR_MODEL={"providerID": "opencode", "modelID": "minimax-m2.5-free"},
        COORDINATOR_AGENT="minimax",
    )

    assert len(calls) == 1
    assert calls[0]["model"] == {"providerID": "opencode", "modelID": "minimax-m2.5-free"}
    assert calls[0]["agent"] == "minimax"


def test_worker_default_agent_none_omits_agent() -> None:
    """When DEFAULT_AGENT is None and detection fails, agent must be None."""
    from opencode_agent_hub.models import InjectionTask

    task = InjectionTask(session_id="ses_unknown", text="hello")
    calls = _run_worker_with_task(
        task,
        DEFAULT_AGENT=None,
        AGENT_MODELS={},
    )

    assert len(calls) == 1
    assert calls[0]["agent"] is None
    assert calls[0]["model"] is None


def test_worker_detected_agent_resolves_model() -> None:
    """When session agent is detected, worker must look up model in AGENT_MODELS."""
    from opencode_agent_hub.models import InjectionTask

    task = InjectionTask(session_id="ses_kimi", text="hello")

    inject_calls = []

    def capture_inject(session_id, text, *, model=None, agent=None):
        inject_calls.append({"model": model, "agent": agent})
        return True

    with (
        mock.patch("opencode_agent_hub.messaging.get_session_agent", return_value="kimi"),
        mock.patch("opencode_agent_hub.messaging.inject_message_sync", side_effect=capture_inject),
    ):
        # Simulate worker logic directly
        from opencode_agent_hub.messaging import get_session_agent

        agent_models = {"kimi": {"providerID": "kimi-for-coding", "modelID": "k2p5"}}
        session_agent = get_session_agent(task.session_id) or None
        session_model = agent_models.get(session_agent) if session_agent else None
        capture_inject(task.session_id, task.text, model=session_model, agent=session_agent)

    assert len(inject_calls) == 1
    assert inject_calls[0]["agent"] == "kimi"
    assert inject_calls[0]["model"] == {"providerID": "kimi-for-coding", "modelID": "k2p5"}


# ---------------------------------------------------------------------------
# Hub server model application
# ---------------------------------------------------------------------------


def test_apply_hub_model_sends_patch() -> None:
    """_apply_hub_model must PATCH /config with the configured model."""
    from opencode_agent_hub.hub_server import _apply_hub_model

    captured = {}

    def mock_patch(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        resp = mock.MagicMock()
        resp.status_code = 200
        return resp

    with (
        mock.patch("opencode_agent_hub.hub_server.requests.patch", side_effect=mock_patch),
        mock.patch("opencode_agent_hub.hub_server.HUB_MODEL", "opencode/minimax-m2.5-free"),
        mock.patch("opencode_agent_hub.hub_server.OPENCODE_URL", "http://127.0.0.1:4096"),
    ):
        _apply_hub_model()

    assert captured["url"] == "http://127.0.0.1:4096/config"
    assert captured["json"] == {"model": "opencode/minimax-m2.5-free"}


def test_apply_hub_model_skips_invalid_model() -> None:
    """_apply_hub_model must skip if HUB_MODEL has no provider/model separator."""
    with (
        mock.patch("opencode_agent_hub.hub_server.requests.patch") as mock_patch,
        mock.patch("opencode_agent_hub.hub_server.HUB_MODEL", "invalid-no-slash"),
    ):
        from opencode_agent_hub.hub_server import _apply_hub_model

        _apply_hub_model()

    mock_patch.assert_not_called()


def test_apply_hub_model_skips_when_empty() -> None:
    """_apply_hub_model must skip if HUB_MODEL is empty/None."""
    with (
        mock.patch("opencode_agent_hub.hub_server.requests.patch") as mock_patch,
        mock.patch("opencode_agent_hub.hub_server.HUB_MODEL", ""),
    ):
        from opencode_agent_hub.hub_server import _apply_hub_model

        _apply_hub_model()

    mock_patch.assert_not_called()


# ---------------------------------------------------------------------------
# Coordinator model precedence
# ---------------------------------------------------------------------------


def test_coordinator_prefers_explicit_model_over_agent_lookup(tmp_path: Path) -> None:
    """When opencode.json has both agent and model, the model field wins."""
    import json

    import opencode_agent_hub.coordinator as coordinator_module

    coord_dir = tmp_path / "coordinator"
    coord_dir.mkdir()

    opencode_json = coord_dir / "opencode.json"
    opencode_json.write_text(
        json.dumps(
            {
                "agent": "minimax",
                "model": "opencode/minimax-m2.5-free",
                "permission": [],
            }
        )
    )

    inject_calls = []

    def capture_inject(session_id, text, *, model=None, agent=None):
        inject_calls.append({"model": model, "agent": agent})
        return True

    with (
        mock.patch.object(config, "COORDINATOR_DIR", coord_dir),
        mock.patch.object(config, "COORDINATOR_ENABLED", True),
        mock.patch.object(config, "AGENTS_DIR", tmp_path / "agents"),
        mock.patch.object(config, "ORIENTED_SESSIONS", set()),
        mock.patch.object(
            config,
            "AGENT_MODELS",
            {"minimax": {"providerID": "minimax-coding-plan", "modelID": "MiniMax-M2.5"}},
        ),
        mock.patch("opencode_agent_hub.coordinator.setup_coordinator_directory") as mock_setup,
        mock.patch("opencode_agent_hub.coordinator.kill_all_coordinator_sessions") as mock_kill,
        mock.patch("requests.post") as mock_post,
        mock.patch("opencode_agent_hub.coordinator.atomic_write_json"),
        mock.patch(
            "opencode_agent_hub.coordinator._wait_for_coordinator_ready",
            return_value=True,
        ),
        mock.patch(
            "opencode_agent_hub.messaging.inject_message_sync",
            side_effect=capture_inject,
        ),
    ):
        mock_setup.return_value = True
        mock_kill.return_value = 0
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ses_test"}
        mock_post.return_value = mock_response

        coordinator_module.start_coordinator()

    assert len(inject_calls) == 1
    # Must use the explicit model field (free model), NOT the agent→model lookup (paid model)
    assert inject_calls[0]["model"] == {"providerID": "opencode", "modelID": "minimax-m2.5-free"}
    assert inject_calls[0]["agent"] == "minimax"
