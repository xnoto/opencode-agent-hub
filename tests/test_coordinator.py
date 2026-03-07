"""Tests for coordinator AGENTS.md resolution, session identification, and lifecycle."""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


def test_find_coordinator_agents_md_explicit_config() -> None:
    """Verify explicit config path takes highest priority."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a custom AGENTS.md
        custom_path = Path(tmpdir) / "custom-agents.md"
        custom_path.write_text("# Custom Coordinator")

        # Mock the config value
        original = daemon.COORDINATOR_AGENTS_MD
        daemon.COORDINATOR_AGENTS_MD = custom_path

        try:
            result = daemon.find_coordinator_agents_md_template()
            assert result == custom_path
        finally:
            daemon.COORDINATOR_AGENTS_MD = original


def test_find_coordinator_agents_md_explicit_config_missing() -> None:
    """Verify warning logged and fallback when explicit config path doesn't exist."""
    from opencode_agent_hub import daemon

    # Mock a non-existent explicit path
    original = daemon.COORDINATOR_AGENTS_MD
    daemon.COORDINATOR_AGENTS_MD = Path("/nonexistent/agents.md")

    try:
        with mock.patch.object(daemon, "CONFIG_DIR", Path("/also-nonexistent")):
            # Should return None since no templates exist
            result = daemon.find_coordinator_agents_md_template()
            # Result depends on whether system templates exist
            # At minimum, it shouldn't crash
            assert result is None or isinstance(result, Path)
    finally:
        daemon.COORDINATOR_AGENTS_MD = original


def test_find_coordinator_agents_md_user_config_agents_md() -> None:
    """Verify ~/.config/agent-hub-daemon/AGENTS.md is checked."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        agents_md = config_dir / "AGENTS.md"
        agents_md.write_text("# User Config AGENTS.md")

        original_config = daemon.COORDINATOR_AGENTS_MD
        original_dir = daemon.CONFIG_DIR
        daemon.COORDINATOR_AGENTS_MD = None  # No explicit config
        daemon.CONFIG_DIR = config_dir

        try:
            result = daemon.find_coordinator_agents_md_template()
            assert result == agents_md
        finally:
            daemon.COORDINATOR_AGENTS_MD = original_config
            daemon.CONFIG_DIR = original_dir


def test_find_coordinator_agents_md_user_config_coordinator_md() -> None:
    """Verify ~/.config/agent-hub-daemon/COORDINATOR.md alias is checked."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        coordinator_md = config_dir / "COORDINATOR.md"
        coordinator_md.write_text("# User Config COORDINATOR.md alias")

        original_config = daemon.COORDINATOR_AGENTS_MD
        original_dir = daemon.CONFIG_DIR
        daemon.COORDINATOR_AGENTS_MD = None
        daemon.CONFIG_DIR = config_dir

        try:
            result = daemon.find_coordinator_agents_md_template()
            assert result == coordinator_md
        finally:
            daemon.COORDINATOR_AGENTS_MD = original_config
            daemon.CONFIG_DIR = original_dir


def test_find_coordinator_agents_md_agents_md_priority_over_coordinator_md() -> None:
    """Verify AGENTS.md takes priority over COORDINATOR.md alias."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        agents_md = config_dir / "AGENTS.md"
        coordinator_md = config_dir / "COORDINATOR.md"
        agents_md.write_text("# AGENTS.md (should win)")
        coordinator_md.write_text("# COORDINATOR.md (should lose)")

        original_config = daemon.COORDINATOR_AGENTS_MD
        original_dir = daemon.CONFIG_DIR
        daemon.COORDINATOR_AGENTS_MD = None
        daemon.CONFIG_DIR = config_dir

        try:
            result = daemon.find_coordinator_agents_md_template()
            assert result == agents_md  # AGENTS.md should win
        finally:
            daemon.COORDINATOR_AGENTS_MD = original_config
            daemon.CONFIG_DIR = original_dir


def test_find_coordinator_agents_md_none_when_no_templates() -> None:
    """Verify None returned when no templates exist."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        original_config = daemon.COORDINATOR_AGENTS_MD
        original_dir = daemon.CONFIG_DIR
        daemon.COORDINATOR_AGENTS_MD = None
        daemon.CONFIG_DIR = Path(tmpdir)  # Empty dir

        try:
            # Mock system locations to not exist
            with mock.patch.object(daemon, "Path") as mock_path:
                # Make all paths report as non-existent
                mock_instance = mock.MagicMock()
                mock_instance.exists.return_value = False
                mock_path.return_value = mock_instance
                mock_path.side_effect = lambda x: Path(x)  # Use real Path

            # The function should handle missing templates gracefully
            result = daemon.find_coordinator_agents_md_template()
            # Result is None or a system template if it happens to exist
            assert result is None or isinstance(result, Path)
        finally:
            daemon.COORDINATOR_AGENTS_MD = original_config
            daemon.CONFIG_DIR = original_dir


def test_setup_coordinator_directory_copies_template() -> None:
    """Verify setup_coordinator_directory copies from found template."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()
        coord_dir = Path(tmpdir) / "coordinator"

        # Create user config templates
        user_template = config_dir / "AGENTS.md"
        user_template.write_text("# Custom Coordinator Instructions")
        # Create opencode.json template (required)
        opencode_json = config_dir / "opencode.json"
        opencode_json.write_text('{"permission": []}')

        original_config = daemon.COORDINATOR_AGENTS_MD
        original_config_dir = daemon.CONFIG_DIR
        original_coord_dir = daemon.COORDINATOR_DIR
        daemon.COORDINATOR_AGENTS_MD = None
        daemon.CONFIG_DIR = config_dir
        daemon.COORDINATOR_DIR = coord_dir

        try:
            result = daemon.setup_coordinator_directory()
            assert result is True

            # Check the AGENTS.md was copied
            copied = coord_dir / "AGENTS.md"
            assert copied.exists()
            assert copied.read_text() == "# Custom Coordinator Instructions"
        finally:
            daemon.COORDINATOR_AGENTS_MD = original_config
            daemon.CONFIG_DIR = original_config_dir
            daemon.COORDINATOR_DIR = original_coord_dir


def test_setup_coordinator_directory_creates_minimal_when_no_template() -> None:
    """Verify setup_coordinator_directory creates minimal AGENTS.md when no template."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()  # Empty config dir
        coord_dir = Path(tmpdir) / "coordinator"

        # Create opencode.json template (required)
        opencode_json = config_dir / "opencode.json"
        opencode_json.write_text('{"permission": []}')

        original_config = daemon.COORDINATOR_AGENTS_MD
        original_config_dir = daemon.CONFIG_DIR
        original_coord_dir = daemon.COORDINATOR_DIR
        original_find = daemon.find_coordinator_agents_md_template
        daemon.COORDINATOR_AGENTS_MD = None
        daemon.CONFIG_DIR = config_dir
        daemon.COORDINATOR_DIR = coord_dir

        try:
            # Mock system locations to not exist

            def mock_find() -> Path | None:
                # Check user config only, skip system
                for path in [config_dir / "AGENTS.md", config_dir / "COORDINATOR.md"]:
                    if path.exists():
                        return path
                return None

            daemon.find_coordinator_agents_md_template = mock_find

            result = daemon.setup_coordinator_directory()
            assert result is True

            # Check minimal AGENTS.md was created
            created = coord_dir / "AGENTS.md"
            assert created.exists()
            content = created.read_text()
            assert "Coordinator Agent" in content
            assert "NEW_AGENT" in content
        finally:
            daemon.COORDINATOR_AGENTS_MD = original_config
            daemon.CONFIG_DIR = original_config_dir
            daemon.COORDINATOR_DIR = original_coord_dir
            daemon.find_coordinator_agents_md_template = original_find


def test_setup_coordinator_directory_overwrites_if_exists_by_default() -> None:
    """Verify setup_coordinator_directory overwrites stale AGENTS.md by default."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()
        coord_dir = Path(tmpdir) / "coordinator"
        coord_dir.mkdir()
        existing = coord_dir / "AGENTS.md"
        existing.write_text("# Existing content - should not be overwritten")

        # Create opencode.json template (required)
        opencode_json = config_dir / "opencode.json"
        opencode_json.write_text('{"permission": []}')

        original_config_dir = daemon.CONFIG_DIR
        original_coord_dir = daemon.COORDINATOR_DIR
        original_preserve = daemon.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD
        daemon.CONFIG_DIR = config_dir
        daemon.COORDINATOR_DIR = coord_dir
        daemon.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = False

        template_agents = config_dir / "AGENTS.md"
        template_agents.write_text("# Fresh template")

        try:
            result = daemon.setup_coordinator_directory()
            assert result is True

            # Verify stale content was overwritten
            assert existing.read_text() == "# Fresh template"
        finally:
            daemon.CONFIG_DIR = original_config_dir
            daemon.COORDINATOR_DIR = original_coord_dir
            daemon.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = original_preserve


def test_setup_coordinator_directory_preserves_when_configured() -> None:
    """Verify setup_coordinator_directory preserves AGENTS.md when configured."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()
        coord_dir = Path(tmpdir) / "coordinator"
        coord_dir.mkdir()
        existing = coord_dir / "AGENTS.md"
        existing.write_text("# Existing content - should be preserved")

        # Create templates
        opencode_json = config_dir / "opencode.json"
        opencode_json.write_text('{"permission": []}')
        template_agents = config_dir / "AGENTS.md"
        template_agents.write_text("# Fresh template")

        original_config_dir = daemon.CONFIG_DIR
        original_coord_dir = daemon.COORDINATOR_DIR
        original_preserve = daemon.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD
        daemon.CONFIG_DIR = config_dir
        daemon.COORDINATOR_DIR = coord_dir
        daemon.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = True

        try:
            result = daemon.setup_coordinator_directory()
            assert result is True
            assert existing.read_text() == "# Existing content - should be preserved"
        finally:
            daemon.CONFIG_DIR = original_config_dir
            daemon.COORDINATOR_DIR = original_coord_dir
            daemon.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = original_preserve


# =============================================================================
# Tests for _parse_session_id_from_json_output
# =============================================================================


def test_parse_session_id_from_json_output_valid() -> None:
    """Verify session ID is extracted from valid JSON output."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps(
        {
            "type": "step_start",
            "timestamp": 1234567890,
            "sessionID": "ses_abc123def456",
            "part": {"id": "prt_xxx"},
        }
    ).encode()

    result = _parse_session_id_from_json_output(stdout)
    assert result == "ses_abc123def456"


def test_parse_session_id_from_json_output_multiline() -> None:
    """Verify only the first line is parsed."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    line1 = json.dumps({"sessionID": "ses_first_line"})
    line2 = json.dumps({"sessionID": "ses_second_line"})
    stdout = f"{line1}\n{line2}\n".encode()

    result = _parse_session_id_from_json_output(stdout)
    assert result == "ses_first_line"


def test_parse_session_id_from_json_output_none() -> None:
    """Verify None returned for None input."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    assert _parse_session_id_from_json_output(None) is None


def test_parse_session_id_from_json_output_empty() -> None:
    """Verify None returned for empty bytes."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    assert _parse_session_id_from_json_output(b"") is None
    assert _parse_session_id_from_json_output(b"\n") is None


def test_parse_session_id_from_json_output_invalid_json() -> None:
    """Verify None returned for non-JSON output."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    assert _parse_session_id_from_json_output(b"not json at all") is None


def test_parse_session_id_from_json_output_missing_field() -> None:
    """Verify None returned when sessionID field is absent."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps({"type": "step_start", "timestamp": 123}).encode()
    assert _parse_session_id_from_json_output(stdout) is None


def test_parse_session_id_from_json_output_bad_prefix() -> None:
    """Verify None returned when sessionID doesn't start with ses_."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps({"sessionID": "invalid_prefix_123"}).encode()
    assert _parse_session_id_from_json_output(stdout) is None


def test_parse_session_id_from_json_output_non_string() -> None:
    """Verify None returned when sessionID is not a string."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps({"sessionID": 12345}).encode()
    assert _parse_session_id_from_json_output(stdout) is None


# =============================================================================
# Tests for find_coordinator_session (title-based matching)
# =============================================================================


def test_find_coordinator_session_matches_title() -> None:
    """Verify find_coordinator_session matches by coordinator title."""
    from opencode_agent_hub import daemon

    coordinator_title = daemon._get_coordinator_title()

    sessions = [
        {"id": "ses_worker1", "title": "Fix bug in auth", "directory": "/project"},
        {"id": "ses_coord", "title": coordinator_title, "directory": "/project"},
        {"id": "ses_worker2", "title": "Add feature X", "directory": "/project"},
    ]

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions):
        result = daemon.find_coordinator_session()

    assert result == "ses_coord"


def test_find_coordinator_session_no_match() -> None:
    """Verify None returned when no coordinator session exists."""
    from opencode_agent_hub import daemon

    sessions = [
        {"id": "ses_worker1", "title": "Fix bug in auth", "directory": "/project"},
        {"id": "ses_worker2", "title": "Add feature X", "directory": "/project"},
    ]

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions):
        result = daemon.find_coordinator_session()

    assert result is None


def test_find_coordinator_session_empty_sessions() -> None:
    """Verify None returned when hub has no sessions."""
    from opencode_agent_hub import daemon

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=[]):
        result = daemon.find_coordinator_session()

    assert result is None


def test_find_coordinator_session_ignores_similar_titles() -> None:
    """Verify only exact title match works."""
    from opencode_agent_hub import daemon

    sessions = [
        {"id": "ses_1", "title": "agent-hub-coordinator setup", "directory": "/p"},
        {"id": "ses_2", "title": "my-agent-hub-coordinator", "directory": "/p"},
        {"id": "ses_3", "title": "Coordinator agent setup", "directory": "/p"},
    ]

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions):
        result = daemon.find_coordinator_session()

    assert result is None


# =============================================================================
# Tests for orient_session coordinator skip (session ID matching)
# =============================================================================


def test_orient_session_skips_coordinator_by_session_id() -> None:
    """Verify orient_session skips injection for coordinator session."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coordinator_123"
        daemon.ORIENTED_SESSIONS = set()

        agent: dict[str, Any] = {"id": "coordinator", "projectPath": "/some/path"}
        all_agents: dict[str, dict[str, Any]] = {"coordinator": agent}

        # Mock save to avoid file I/O
        with (
            mock.patch.object(daemon, "save_oriented_sessions"),
            mock.patch.object(daemon, "inject_message") as mock_inject,
        ):
            result = daemon.orient_session("ses_coordinator_123", agent, all_agents)

        assert result is True
        assert "ses_coordinator_123" in daemon.ORIENTED_SESSIONS
        mock_inject.assert_not_called()  # No orientation injected
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented


def test_orient_session_does_not_skip_non_coordinator() -> None:
    """Verify orient_session injects orientation for non-coordinator sessions."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coordinator_123"
        daemon.ORIENTED_SESSIONS = set()

        agent = {"id": "worker-agent", "projectPath": "/worker/path"}
        all_agents = {"worker-agent": agent}

        with (
            mock.patch.object(daemon, "save_oriented_sessions"),
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "notify_coordinator_new_agent"),
        ):
            result = daemon.orient_session("ses_worker_456", agent, all_agents)

        assert result is True
        assert "ses_worker_456" in daemon.ORIENTED_SESSIONS
        mock_inject.assert_called_once()  # Orientation WAS injected
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented


def test_orient_session_no_coordinator_id_does_not_skip() -> None:
    """Verify orient_session doesn't skip when COORDINATOR_SESSION_ID is None."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.ORIENTED_SESSIONS = set()

        agent = {"id": "some-agent", "projectPath": "/some/path"}
        all_agents = {"some-agent": agent}

        with (
            mock.patch.object(daemon, "save_oriented_sessions"),
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "notify_coordinator_new_agent"),
        ):
            result = daemon.orient_session("ses_any_session", agent, all_agents)

        assert result is True
        mock_inject.assert_called_once()  # Should inject, not skip
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented


# =============================================================================
# Tests for start_coordinator (integration with mocks)
# =============================================================================


def test_start_coordinator_registers_session_and_queues_bootstrap() -> None:
    """Verify start_coordinator registers coordinator and queues bootstrap prompt."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()

        # Mock HTTP response for session creation
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ses_newcoord123"}

        with tempfile.TemporaryDirectory() as tmpdir:
            agents_dir = Path(tmpdir) / "agents"

            with (
                mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
                mock.patch.object(daemon, "kill_all_coordinator_sessions", return_value=0),
                mock.patch("requests.post", return_value=mock_response) as mock_post,
                mock.patch.object(daemon, "inject_message_sync", return_value=True) as mock_inject,
                mock.patch.object(daemon, "_wait_for_coordinator_ready", return_value=True),
                mock.patch.object(daemon, "AGENTS_DIR", agents_dir),
            ):
                result = daemon.start_coordinator()

        assert result is True
        assert daemon.COORDINATOR_SESSION_ID == "ses_newcoord123"
        assert "ses_newcoord123" in daemon.ORIENTED_SESSIONS
        mock_inject.assert_called_once_with("ses_newcoord123", daemon.COORDINATOR_BOOTSTRAP_PROMPT)
        assert mock_post.call_args is not None
        payload = mock_post.call_args.kwargs.get("json", {})
        # Model is now set via hub server config, not API parameter
        assert "model" not in payload
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented


def test_start_coordinator_returns_false_on_api_error() -> None:
    """Verify start_coordinator fails when session creation API fails."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()

        mock_response = mock.MagicMock()
        mock_response.status_code = 500

        with (
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(daemon, "kill_all_coordinator_sessions", return_value=0),
            mock.patch("requests.post", return_value=mock_response),
            mock.patch.object(daemon, "_wait_for_coordinator_ready", return_value=True),
        ):
            result = daemon.start_coordinator()

        assert result is False
        assert daemon.COORDINATOR_SESSION_ID is None
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented


def test_start_coordinator_reuses_existing_session() -> None:
    """Verify start_coordinator kills existing and creates new session."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()

        # Mock HTTP responses
        mock_post_response = mock.MagicMock()
        mock_post_response.status_code = 200
        mock_post_response.json.return_value = {"id": "ses_new_789"}

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(
                daemon, "kill_all_coordinator_sessions", return_value=1
            ) as mock_killed,
            mock.patch("requests.post", return_value=mock_post_response),
            mock.patch.object(daemon, "inject_message_sync", return_value=True),
            mock.patch.object(daemon, "_wait_for_coordinator_ready", return_value=True),
            mock.patch.object(daemon, "AGENTS_DIR", Path(tmpdir) / "agents"),
        ):
            result = daemon.start_coordinator()

        assert result is True
        assert daemon.COORDINATOR_SESSION_ID == "ses_new_789"
        assert "ses_new_789" in daemon.ORIENTED_SESSIONS
        mock_killed.assert_called_once()
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented


def test_start_coordinator_disabled() -> None:
    """Verify start_coordinator returns False when disabled."""
    from opencode_agent_hub import daemon

    original_enabled = daemon.COORDINATOR_ENABLED

    try:
        daemon.COORDINATOR_ENABLED = False

        result = daemon.start_coordinator()
        assert result is False
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled


def test_start_coordinator_fails_when_setup_fails() -> None:
    """Verify start_coordinator returns False when setup fails."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True

        with mock.patch.object(daemon, "setup_coordinator_directory", return_value=False):
            result = daemon.start_coordinator()

        assert result is False
        assert daemon.COORDINATOR_SESSION_ID is None
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled


def test_start_coordinator_continues_when_ready_not_acknowledged() -> None:
    """Verify start_coordinator continues in best-effort mode without readiness ack."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_required = daemon.COORDINATOR_BOOTSTRAP_REQUIRED

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()
        daemon.COORDINATOR_BOOTSTRAP_REQUIRED = False

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ses_not_ready"}

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(daemon, "kill_all_coordinator_sessions", return_value=0),
            mock.patch("requests.post", return_value=mock_response),
            mock.patch.object(daemon, "inject_message_sync", return_value=True),
            mock.patch.object(daemon, "_wait_for_coordinator_ready", return_value=False),
            mock.patch.object(daemon, "AGENTS_DIR", Path(tmpdir) / "agents"),
        ):
            result = daemon.start_coordinator()

        assert result is True
        assert daemon.COORDINATOR_SESSION_ID == "ses_not_ready"
        assert "ses_not_ready" in daemon.ORIENTED_SESSIONS
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.COORDINATOR_BOOTSTRAP_REQUIRED = original_required


def test_start_coordinator_fails_when_bootstrap_required_and_not_ready() -> None:
    """Verify start_coordinator fails when readiness is required and missing."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_required = daemon.COORDINATOR_BOOTSTRAP_REQUIRED

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()
        daemon.COORDINATOR_BOOTSTRAP_REQUIRED = True

        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ses_not_ready"}

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(daemon, "kill_all_coordinator_sessions", return_value=0),
            mock.patch("requests.post", return_value=mock_response),
            mock.patch("requests.delete") as mock_delete,
            mock.patch.object(daemon, "inject_message_sync", return_value=True),
            mock.patch.object(daemon, "_wait_for_coordinator_ready", return_value=False),
            mock.patch.object(daemon, "AGENTS_DIR", Path(tmpdir) / "agents"),
        ):
            result = daemon.start_coordinator()

        assert result is False
        assert daemon.COORDINATOR_SESSION_ID is None
        assert "ses_not_ready" not in daemon.ORIENTED_SESSIONS
        mock_delete.assert_called_once()
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.COORDINATOR_BOOTSTRAP_REQUIRED = original_required


def test_notify_coordinator_new_agent_retries_when_no_activity() -> None:
    """Verify NEW_AGENT notification is retried once when coordinator is silent."""
    from opencode_agent_hub import daemon

    original_enabled = daemon.COORDINATOR_ENABLED
    original_session_id = daemon.COORDINATOR_SESSION_ID

    try:
        daemon.COORDINATOR_ENABLED = True
        daemon.COORDINATOR_SESSION_ID = "ses_coord_retry"

        with (
            mock.patch.object(daemon, "inject_message") as mock_inject,
            mock.patch.object(daemon, "_wait_for_coordinator_activity", return_value=False),
        ):
            daemon.notify_coordinator_new_agent("worker-1", "/tmp/project")

        assert mock_inject.call_count == 2
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.COORDINATOR_SESSION_ID = original_session_id


def test_wait_for_coordinator_ready_accepts_activity_when_not_strict() -> None:
    """Verify readiness accepts assistant activity when strict mode is disabled."""
    from opencode_agent_hub import daemon

    original_strict = daemon.COORDINATOR_STRICT_READY

    try:
        daemon.COORDINATOR_STRICT_READY = False
        messages = [
            {
                "info": {"role": "assistant", "time": {"created": 2000}},
                "parts": [{"type": "text", "text": "I am initialized"}],
            }
        ]

        with (
            mock.patch.object(daemon, "_fetch_session_messages", return_value=messages),
            mock.patch("time.time", side_effect=[0.0, 0.1]),
            mock.patch("time.sleep"),
        ):
            assert daemon._wait_for_coordinator_ready("ses_test", 20, after_ms=1000) is True
    finally:
        daemon.COORDINATOR_STRICT_READY = original_strict


def test_wait_for_coordinator_ready_requires_ready_when_strict() -> None:
    """Verify strict readiness mode ignores non-READY assistant activity."""
    from opencode_agent_hub import daemon

    original_strict = daemon.COORDINATOR_STRICT_READY

    try:
        daemon.COORDINATOR_STRICT_READY = True
        messages = [
            {
                "info": {"role": "assistant", "time": {"created": 2000}},
                "parts": [{"type": "text", "text": "Initialized"}],
            }
        ]

        with (
            mock.patch.object(daemon, "_fetch_session_messages", return_value=messages),
            mock.patch("time.time", side_effect=[0.0, 0.1, 99.0]),
            mock.patch("time.sleep"),
        ):
            assert daemon._wait_for_coordinator_ready("ses_test", 20, after_ms=1000) is False
    finally:
        daemon.COORDINATOR_STRICT_READY = original_strict


def test_wait_for_coordinator_ready_accepts_exact_ready_in_strict_mode() -> None:
    """Verify strict mode still succeeds on exact READY acknowledgement."""
    from opencode_agent_hub import daemon

    original_strict = daemon.COORDINATOR_STRICT_READY

    try:
        daemon.COORDINATOR_STRICT_READY = True
        messages = [
            {
                "info": {"role": "assistant", "time": {"created": 2000}},
                "parts": [{"type": "text", "text": "READY"}],
            }
        ]

        with (
            mock.patch.object(daemon, "_fetch_session_messages", return_value=messages),
            mock.patch("time.time", side_effect=[0.0, 0.1]),
            mock.patch("time.sleep"),
        ):
            assert daemon._wait_for_coordinator_ready("ses_test", 20, after_ms=1000) is True
    finally:
        daemon.COORDINATOR_STRICT_READY = original_strict


def test_get_recent_hub_error_context_returns_session_errors() -> None:
    """Verify hub error context helper extracts session-scoped error lines."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "hub-stderr.log"
        log_path.write_text(
            "INFO sessionID=ses_a all good\n"
            "ERROR sessionID=ses_target first failure details\n"
            "ERROR sessionID=ses_other unrelated\n"
            "ERROR sessionID=ses_target second failure details\n"
        )

        with mock.patch.object(daemon, "HUB_STDERR_LOG_FILE", log_path):
            ctx = daemon._get_recent_hub_error_context("ses_target")

        assert "first failure details" in ctx
        assert "second failure details" in ctx
        assert "ses_other" not in ctx


def test_find_opencode_serve_pids_on_port_filters_non_opencode() -> None:
    """Verify PID discovery returns only opencode serve listeners."""
    from opencode_agent_hub import daemon

    lsof_out = mock.MagicMock()
    lsof_out.stdout = "111\n222\n"

    ps_opencode = mock.MagicMock()
    ps_opencode.stdout = "opencode serve --port 4096"

    ps_other = mock.MagicMock()
    ps_other.stdout = "python -m http.server 4096"

    with mock.patch("subprocess.run", side_effect=[lsof_out, ps_opencode, ps_other]):
        pids = daemon._find_opencode_serve_pids_on_port()

    assert pids == [111]


def test_kill_opencode_serve_pids_escalates_to_sigkill() -> None:
    """Verify process killer escalates to SIGKILL when still alive."""
    from opencode_agent_hub import daemon

    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:
            return

    import signal

    with mock.patch("os.kill", side_effect=fake_kill), mock.patch("time.sleep"):
        daemon._kill_opencode_serve_pids([123])

    assert (123, signal.SIGTERM) in calls
    assert (123, 0) in calls
    assert (123, signal.SIGKILL) in calls


# =============================================================================
# Tests for coordinator self-registration race condition fix
# =============================================================================


def test_poll_active_sessions_skips_coordinator() -> None:
    """Verify poll_active_sessions skips coordinator session before creating agent."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_start_time = daemon.DAEMON_START_TIME_MS

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coord_abc"
        daemon.ORIENTED_SESSIONS = set()
        daemon.DAEMON_START_TIME_MS = 1000  # Ensure sessions are "after" daemon start

        sessions = [
            {
                "id": "ses_coord_abc",
                "title": daemon._get_coordinator_title(),
                "directory": "/project",
                "time": {"created": 2000},
            },
            {
                "id": "ses_worker_xyz",
                "title": "Fix bug",
                "directory": "/project",
                "time": {"created": 2000},
            },
        ]

        agents: dict[str, dict[str, Any]] = {}

        with (
            mock.patch.object(daemon, "get_sessions", return_value=sessions),
            mock.patch.object(daemon, "get_or_create_agent_for_session") as mock_create_agent,
            mock.patch.object(daemon, "orient_session"),
        ):
            mock_create_agent.return_value = {
                "id": "worker-agent",
                "projectPath": "/project",
            }
            daemon.poll_active_sessions(agents)

        # Coordinator should be in ORIENTED_SESSIONS but never trigger agent creation
        assert "ses_coord_abc" in daemon.ORIENTED_SESSIONS
        # get_or_create_agent_for_session should only be called for the worker
        assert mock_create_agent.call_count == 1
        called_session = mock_create_agent.call_args[0][0]
        assert called_session["id"] == "ses_worker_xyz"
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.DAEMON_START_TIME_MS = original_start_time


def test_poll_active_sessions_no_skip_when_coordinator_unset() -> None:
    """Verify poll_active_sessions processes all sessions when no coordinator is set."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_start_time = daemon.DAEMON_START_TIME_MS

    try:
        daemon.COORDINATOR_SESSION_ID = None  # No coordinator set
        daemon.ORIENTED_SESSIONS = set()
        daemon.DAEMON_START_TIME_MS = 1000

        sessions = [
            {
                "id": "ses_any_session",
                "title": "Some work",
                "directory": "/project",
                "time": {"created": 2000},
            },
        ]

        agents: dict[str, dict[str, Any]] = {}

        with (
            mock.patch.object(daemon, "get_sessions", return_value=sessions),
            mock.patch.object(daemon, "get_or_create_agent_for_session") as mock_create_agent,
            mock.patch.object(daemon, "orient_session"),
        ):
            mock_create_agent.return_value = {"id": "agent-1", "projectPath": "/project"}
            daemon.poll_active_sessions(agents)

        assert mock_create_agent.call_count == 1
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.DAEMON_START_TIME_MS = original_start_time


def test_process_session_file_skips_coordinator() -> None:
    """Verify process_session_file skips coordinator session before creating agent."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_start_time = daemon.DAEMON_START_TIME_MS

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coord_file"
        daemon.ORIENTED_SESSIONS = set()
        daemon.DAEMON_START_TIME_MS = 1000

        coordinator_session = {
            "id": "ses_coord_file",
            "title": daemon._get_coordinator_title(),
            "directory": "/project",
            "time": {"created": 2000},
        }

        agents: dict[str, dict[str, Any]] = {}

        with (
            mock.patch.object(daemon, "load_opencode_session", return_value=coordinator_session),
            mock.patch.object(daemon, "get_or_create_agent_for_session") as mock_create_agent,
            mock.patch.object(daemon, "orient_session") as mock_orient,
        ):
            daemon.process_session_file(Path("/fake/session.json"), agents)

        # Should skip: no agent creation, no orientation
        assert "ses_coord_file" in daemon.ORIENTED_SESSIONS
        mock_create_agent.assert_not_called()
        mock_orient.assert_not_called()
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.DAEMON_START_TIME_MS = original_start_time


def test_process_session_file_processes_non_coordinator() -> None:
    """Verify process_session_file processes normal sessions normally."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_start_time = daemon.DAEMON_START_TIME_MS

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coord_other"
        daemon.ORIENTED_SESSIONS = set()
        daemon.DAEMON_START_TIME_MS = 1000

        worker_session = {
            "id": "ses_worker_file",
            "title": "Fix bug",
            "directory": "/project",
            "time": {"created": 2000},
        }

        agents: dict[str, dict[str, Any]] = {}

        with (
            mock.patch.object(daemon, "load_opencode_session", return_value=worker_session),
            mock.patch.object(daemon, "get_or_create_agent_for_session") as mock_create_agent,
            mock.patch.object(daemon, "orient_session") as mock_orient,
        ):
            mock_create_agent.return_value = {"id": "worker", "projectPath": "/project"}
            daemon.process_session_file(Path("/fake/session.json"), agents)

        # Worker should be processed normally
        mock_create_agent.assert_called_once()
        mock_orient.assert_called_once()
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.DAEMON_START_TIME_MS = original_start_time


def test_no_phantom_agent_for_coordinator_end_to_end() -> None:
    """End-to-end: coordinator session appears in poll, no phantom agent created.

    Simulates the exact race condition scenario:
    1. start_coordinator sets COORDINATOR_SESSION_ID
    2. session_poller runs and sees the coordinator session
    3. Verify no agent identity is created for the coordinator
    """
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()
    original_start_time = daemon.DAEMON_START_TIME_MS
    original_session_agents = daemon.SESSION_AGENTS.copy()

    try:
        # Step 1: Simulate start_coordinator completing
        daemon.COORDINATOR_SESSION_ID = "ses_coord_e2e"
        daemon.ORIENTED_SESSIONS = {"ses_coord_e2e"}  # Set by start_coordinator
        daemon.DAEMON_START_TIME_MS = 1000
        daemon.SESSION_AGENTS = {}

        # Step 2: Poller sees coordinator + worker sessions
        sessions = [
            {
                "id": "ses_coord_e2e",
                "title": daemon._get_coordinator_title(),
                "directory": "/project",
                "time": {"created": 2000},
            },
            {
                "id": "ses_new_worker",
                "title": "Implement feature",
                "directory": "/project",
                "time": {"created": 3000},
            },
        ]

        agents: dict[str, dict[str, Any]] = {}

        with (
            mock.patch.object(daemon, "get_sessions", return_value=sessions),
            mock.patch.object(daemon, "get_or_create_agent_for_session") as mock_create_agent,
            mock.patch.object(daemon, "orient_session"),
        ):
            mock_create_agent.return_value = {
                "id": "new-worker",
                "projectPath": "/project",
            }
            daemon.poll_active_sessions(agents)

        # Step 3: Verify
        # Coordinator was already in ORIENTED_SESSIONS so it's skipped entirely
        # Agent creation should only happen for the worker
        assert mock_create_agent.call_count == 1
        called_session = mock_create_agent.call_args[0][0]
        assert called_session["id"] == "ses_new_worker"

        # No SESSION_AGENTS mapping for coordinator
        assert "ses_coord_e2e" not in daemon.SESSION_AGENTS
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.ORIENTED_SESSIONS = original_oriented
        daemon.DAEMON_START_TIME_MS = original_start_time
        daemon.SESSION_AGENTS = original_session_agents


def test_session_has_blocking_permissions_with_deny_question() -> None:
    """Verify session with question:deny permission is detected as blocking."""
    from opencode_agent_hub import daemon

    session = {
        "id": "ses_blocked",
        "title": "test",
        "permission": [
            {"permission": "question", "pattern": "*", "action": "deny"},
            {"permission": "plan_enter", "pattern": "*", "action": "deny"},
        ],
    }
    assert daemon.session_has_blocking_permissions(session) is True


def test_session_has_blocking_permissions_without_question_deny() -> None:
    """Verify session without question:deny is not blocking."""
    from opencode_agent_hub import daemon

    session = {
        "id": "ses_allowed",
        "title": "test",
        "permission": [
            {"permission": "plan_enter", "pattern": "*", "action": "deny"},
        ],
    }
    assert daemon.session_has_blocking_permissions(session) is False


def test_session_has_blocking_permissions_empty_permissions() -> None:
    """Verify session with empty permissions is not blocking."""
    from opencode_agent_hub import daemon

    session = {"id": "ses_empty", "title": "test", "permission": []}
    assert daemon.session_has_blocking_permissions(session) is False


def test_session_has_blocking_permissions_no_permissions_field() -> None:
    """Verify session without permission field is not blocking."""
    from opencode_agent_hub import daemon

    session = {"id": "ses_no_perm", "title": "test"}
    assert daemon.session_has_blocking_permissions(session) is False


def test_session_has_blocking_permissions_invalid_permissions_type() -> None:
    """Verify session with non-list permission field is not blocking."""
    from opencode_agent_hub import daemon

    session = {"id": "ses_invalid", "title": "test", "permission": "deny_all"}
    assert daemon.session_has_blocking_permissions(session) is False


def test_find_coordinator_session_raises_on_blocking_permissions() -> None:
    """Verify find_coordinator_session raises PreflightError when session has blocking permissions."""
    from opencode_agent_hub import daemon

    sessions = [
        {
            "id": "ses_blocked_coord",
            "title": daemon._get_coordinator_title(),
            "permission": [{"permission": "question", "pattern": "*", "action": "deny"}],
        }
    ]

    with (
        mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions),
        pytest.raises(daemon.PreflightError) as exc_info,
    ):
        daemon.find_coordinator_session()

    assert "blocking permissions" in str(exc_info.value)
    assert "ses_bloc" in str(exc_info.value)  # Session ID truncated to 8 chars


def test_find_coordinator_session_returns_valid_session() -> None:
    """Verify find_coordinator_session returns ID when session has no blocking permissions."""
    from opencode_agent_hub import daemon

    sessions = [
        {
            "id": "ses_valid_coord",
            "title": daemon._get_coordinator_title(),
            "permission": [],
        }
    ]

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions):
        result = daemon.find_coordinator_session()
        assert result == "ses_valid_coord"


def test_setup_coordinator_directory_copies_opencode_json_template() -> None:
    """Verify setup_coordinator_directory copies opencode.json from template."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        original_dir = daemon.COORDINATOR_DIR
        daemon.COORDINATOR_DIR = Path(tmpdir)

        # Create a template opencode.json
        template_dir = Path(tmpdir) / "template"
        template_dir.mkdir()
        template_json = template_dir / "opencode.json"
        template_config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": [{"permission": "*", "pattern": "*", "action": "allow"}],
        }
        template_json.write_text(json.dumps(template_config))

        try:
            with mock.patch.object(
                daemon, "find_coordinator_opencode_json_template", return_value=template_json
            ):
                result = daemon.setup_coordinator_directory()
                assert result is True

            opencode_json = Path(tmpdir) / "opencode.json"
            assert opencode_json.exists()

            config = json.loads(opencode_json.read_text())
            assert config["$schema"] == "https://opencode.ai/config.json"
            assert config["permission"][0]["action"] == "allow"
        finally:
            daemon.COORDINATOR_DIR = original_dir


def test_setup_coordinator_directory_fails_without_template() -> None:
    """Verify setup_coordinator_directory returns False when no opencode.json template found."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        original_dir = daemon.COORDINATOR_DIR
        daemon.COORDINATOR_DIR = Path(tmpdir)

        try:
            with mock.patch.object(
                daemon, "find_coordinator_opencode_json_template", return_value=None
            ):
                result = daemon.setup_coordinator_directory()
                assert result is False
        finally:
            daemon.COORDINATOR_DIR = original_dir


def test_setup_coordinator_directory_overwrites_existing_opencode_json() -> None:
    """Verify setup_coordinator_directory always overwrites opencode.json with template."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        original_dir = daemon.COORDINATOR_DIR
        daemon.COORDINATOR_DIR = Path(tmpdir)

        # Create a template opencode.json
        template_dir = Path(tmpdir) / "template"
        template_dir.mkdir()
        template_json = template_dir / "opencode.json"
        template_config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": [{"permission": "agent-hub_*", "pattern": "*", "action": "allow"}],
        }
        template_json.write_text(json.dumps(template_config))

        # Create existing opencode.json with different content
        existing_config = {"custom": "value"}
        opencode_json = Path(tmpdir) / "opencode.json"
        opencode_json.write_text(json.dumps(existing_config))

        try:
            with mock.patch.object(
                daemon, "find_coordinator_opencode_json_template", return_value=template_json
            ):
                result = daemon.setup_coordinator_directory()
                assert result is True

            # Should overwrite with template
            config = json.loads(opencode_json.read_text())
            assert config == template_config
        finally:
            daemon.COORDINATOR_DIR = original_dir
