"""Tests for coordinator model logging functionality."""

import json
from pathlib import Path
from unittest import mock

from opencode_agent_hub import config


def test_start_coordinator_logs_model(tmp_path: Path) -> None:
    """Verify coordinator logs the model from opencode.json when starting."""
    import opencode_agent_hub.coordinator as coordinator_module

    # Create a coordinator directory with opencode.json
    coord_dir = tmp_path / "coordinator"
    coord_dir.mkdir()

    opencode_json = coord_dir / "opencode.json"
    opencode_config = {
        "agent": "test-agent",
        "model": "test-provider/test-model-v1",
        "permission": [],
    }
    opencode_json.write_text(json.dumps(opencode_config))

    # Track logged messages
    logged_messages = []
    original_log_info = config.log.info

    def capture_log_info(msg, *args, **kwargs):
        logged_messages.append(msg)
        original_log_info(msg, *args, **kwargs)

    # Mock necessary functions and config
    with (
        mock.patch.object(config, "COORDINATOR_DIR", coord_dir),
        mock.patch.object(config, "COORDINATOR_ENABLED", True),
        mock.patch.object(config, "AGENTS_DIR", tmp_path / "agents"),
        mock.patch.object(config, "ORIENTED_SESSIONS", set()),
        mock.patch.object(
            config,
            "AGENT_MODELS",
            {"test-agent": {"providerID": "test-provider", "modelID": "test-model-v1"}},
        ),
        mock.patch("opencode_agent_hub.coordinator.setup_coordinator_directory") as mock_setup,
        mock.patch("opencode_agent_hub.coordinator.kill_all_coordinator_sessions") as mock_kill,
        mock.patch("requests.post") as mock_post,
        mock.patch("opencode_agent_hub.coordinator.atomic_write_json"),
        mock.patch.object(config.log, "info", side_effect=capture_log_info),
    ):
        mock_setup.return_value = True
        mock_kill.return_value = 0

        # Mock session creation response
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ses_test123"}
        mock_post.return_value = mock_response

        # Mock the wait and other functions to avoid full coordinator startup
        with (
            mock.patch(
                "opencode_agent_hub.coordinator._wait_for_coordinator_ready",
                return_value=True,
            ),
            mock.patch("opencode_agent_hub.messaging.inject_message_sync", return_value=True),
        ):
            coordinator_module.start_coordinator()

    # Verify the configured agent/model was logged (values come from opencode.json)
    model_logged = any(
        "Coordinator agent: test-agent" in msg and "test-provider" in msg for msg in logged_messages
    )
    assert model_logged, f"Expected coordinator agent/model log not found in: {logged_messages}"


def test_coordinator_passes_agent_label_from_config(tmp_path: Path) -> None:
    """Verify coordinator passes the agent name from opencode.json to inject_message_sync.

    The agent label must come from config, never hardcoded. End users may have
    completely different agent names than the ones used during development.
    """
    import opencode_agent_hub.coordinator as coordinator_module

    coord_dir = tmp_path / "coordinator"
    coord_dir.mkdir()

    # Use a custom agent name that no one would hardcode
    opencode_json = coord_dir / "opencode.json"
    opencode_config = {
        "agent": "custom-llm",
        "model": "my-provider/my-model",
        "permission": [],
    }
    opencode_json.write_text(json.dumps(opencode_config))

    inject_calls: list[dict] = []

    def capture_inject(session_id, text, *, model=None, agent=None):
        inject_calls.append({"session_id": session_id, "model": model, "agent": agent})
        return True

    with (
        mock.patch.object(config, "COORDINATOR_DIR", coord_dir),
        mock.patch.object(config, "COORDINATOR_ENABLED", True),
        mock.patch.object(config, "AGENTS_DIR", tmp_path / "agents"),
        mock.patch.object(config, "ORIENTED_SESSIONS", set()),
        mock.patch.object(
            config,
            "AGENT_MODELS",
            {"custom-llm": {"providerID": "my-provider", "modelID": "my-model"}},
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
        mock_response.json.return_value = {"id": "ses_custom123"}
        mock_post.return_value = mock_response

        coordinator_module.start_coordinator()

    # The bootstrap injection must pass the agent name from opencode.json
    assert len(inject_calls) == 1, f"Expected 1 injection, got {len(inject_calls)}"
    assert inject_calls[0]["agent"] == "custom-llm", (
        f"Expected agent='custom-llm' from config, got {inject_calls[0]['agent']!r}"
    )
    assert inject_calls[0]["model"] == {"providerID": "my-provider", "modelID": "my-model"}

    # config.COORDINATOR_AGENT must be set for the injection worker
    assert config.COORDINATOR_AGENT == "custom-llm"


def test_start_coordinator_handles_missing_opencode_json(tmp_path: Path) -> None:
    """Verify coordinator handles missing opencode.json gracefully."""
    import opencode_agent_hub.coordinator as coordinator_module

    # Create a coordinator directory WITHOUT opencode.json
    coord_dir = tmp_path / "coordinator"
    coord_dir.mkdir()

    # Should not raise an exception when opencode.json is missing
    with (
        mock.patch.object(config, "COORDINATOR_DIR", coord_dir),
        mock.patch.object(config, "COORDINATOR_ENABLED", True),
        mock.patch.object(config, "COORDINATOR_SESSION_ID", None),
        mock.patch.object(config, "ORIENTED_SESSIONS", set()),
        mock.patch.object(config, "AGENTS_DIR", tmp_path / "agents"),
        mock.patch("opencode_agent_hub.coordinator.setup_coordinator_directory") as mock_setup,
        mock.patch("opencode_agent_hub.coordinator.kill_all_coordinator_sessions") as mock_kill,
        mock.patch("requests.post") as mock_post,
        mock.patch("opencode_agent_hub.coordinator.atomic_write_json"),
    ):
        mock_setup.return_value = True
        mock_kill.return_value = 0

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ses_test123"}
        mock_post.return_value = mock_response

        with (
            mock.patch(
                "opencode_agent_hub.coordinator._wait_for_coordinator_ready",
                return_value=True,
            ),
            mock.patch("opencode_agent_hub.messaging.inject_message_sync", return_value=True),
        ):
            # Should not raise
            coordinator_module.start_coordinator()

    # If we get here without exception, test passed
    assert True
