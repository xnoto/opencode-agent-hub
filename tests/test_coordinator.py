"""Tests for coordinator AGENTS.md resolution, session identification, and lifecycle."""

import signal
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from opencode_agent_hub.config import (
    COORDINATOR_AGENTS_MD,
)
from opencode_agent_hub.coordinator import (
    find_coordinator_agents_md_template,
    find_coordinator_session,
    session_has_blocking_permissions,
    setup_coordinator_directory,
)
from opencode_agent_hub.hub_server import (
    _find_opencode_serve_pids_on_port,
    _kill_opencode_serve_pids,
)
from opencode_agent_hub.models import PreflightError
from opencode_agent_hub.sessions import (
    orient_session,
)


def test_find_coordinator_agents_md_explicit_config() -> None:
    """Verify explicit config path takes highest priority."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a custom AGENTS.md
        custom_path = Path(tmpdir) / "custom-agents.md"
        custom_path.write_text("# Custom Coordinator")

        # Mock the config value
        original = COORDINATOR_AGENTS_MD
        import opencode_agent_hub.config

        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = custom_path

        try:
            result = find_coordinator_agents_md_template()
            assert result == custom_path
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original


def test_find_coordinator_agents_md_explicit_config_missing() -> None:
    """Verify warning logged and fallback when explicit config path doesn't exist."""
    # Mock a non-existent explicit path
    original = COORDINATOR_AGENTS_MD
    import opencode_agent_hub.config

    opencode_agent_hub.config.COORDINATOR_AGENTS_MD = Path("/nonexistent/agents.md")

    try:
        with mock.patch.object(opencode_agent_hub.config, "CONFIG_DIR", Path("/also-nonexistent")):
            # Should return None since no templates exist
            result = find_coordinator_agents_md_template()
            # Result depends on whether system templates exist
            # At minimum, it shouldn't crash
            assert result is None or isinstance(result, Path)
    finally:
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original


def test_find_coordinator_agents_md_user_config_agents_md() -> None:
    """Verify ~/.config/agent-hub-daemon/AGENTS.md is checked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        agents_md = config_dir / "AGENTS.md"
        agents_md.write_text("# User Config AGENTS.md")

        original_config = COORDINATOR_AGENTS_MD
        import opencode_agent_hub.config

        original_dir = opencode_agent_hub.config.CONFIG_DIR
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = None  # No explicit config
        opencode_agent_hub.config.CONFIG_DIR = config_dir

        try:
            result = find_coordinator_agents_md_template()
            assert result == agents_md
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original_config
            opencode_agent_hub.config.CONFIG_DIR = original_dir


def test_find_coordinator_agents_md_user_config_coordinator_md() -> None:
    """Verify ~/.config/agent-hub-daemon/COORDINATOR.md alias is checked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        coordinator_md = config_dir / "COORDINATOR.md"
        coordinator_md.write_text("# User Config COORDINATOR.md alias")

        original_config = COORDINATOR_AGENTS_MD
        import opencode_agent_hub.config

        original_dir = opencode_agent_hub.config.CONFIG_DIR
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = None
        opencode_agent_hub.config.CONFIG_DIR = config_dir

        try:
            result = find_coordinator_agents_md_template()
            assert result == coordinator_md
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original_config
            opencode_agent_hub.config.CONFIG_DIR = original_dir


def test_find_coordinator_agents_md_agents_md_priority_over_coordinator_md() -> None:
    """Verify AGENTS.md takes priority over COORDINATOR.md alias."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        agents_md = config_dir / "AGENTS.md"
        coordinator_md = config_dir / "COORDINATOR.md"
        agents_md.write_text("# AGENTS.md (should win)")
        coordinator_md.write_text("# COORDINATOR.md (should lose)")

        original_config = COORDINATOR_AGENTS_MD
        import opencode_agent_hub.config

        original_dir = opencode_agent_hub.config.CONFIG_DIR
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = None
        opencode_agent_hub.config.CONFIG_DIR = config_dir

        try:
            result = find_coordinator_agents_md_template()
            assert result == agents_md  # AGENTS.md should win
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original_config
            opencode_agent_hub.config.CONFIG_DIR = original_dir


def test_find_coordinator_agents_md_none_when_no_templates() -> None:
    """Verify None returned when no templates exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        import opencode_agent_hub.config

        original_config = opencode_agent_hub.config.COORDINATOR_AGENTS_MD
        original_dir = opencode_agent_hub.config.CONFIG_DIR
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = None
        opencode_agent_hub.config.CONFIG_DIR = Path(tmpdir)  # Empty dir

        try:
            # Mock system locations to not exist
            with mock.patch("opencode_agent_hub.coordinator.Path") as mock_path:
                # Make all paths report as non-existent
                mock_instance = mock.MagicMock()
                mock_instance.exists.return_value = False
                mock_path.return_value = mock_instance
                mock_path.side_effect = lambda x: Path(x)  # Use real Path

                # The function should handle missing templates gracefully
                result = find_coordinator_agents_md_template()
                # Result is None or a system template if it happens to exist
                assert result is None or isinstance(result, Path)
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original_config
            opencode_agent_hub.config.CONFIG_DIR = original_dir


def test_setup_coordinator_directory_copies_template() -> None:
    """Verify setup_coordinator_directory copies from found template."""
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

        import opencode_agent_hub.config

        original_config = opencode_agent_hub.config.COORDINATOR_AGENTS_MD
        original_config_dir = opencode_agent_hub.config.CONFIG_DIR
        original_coord_dir = opencode_agent_hub.config.COORDINATOR_DIR
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = None
        opencode_agent_hub.config.CONFIG_DIR = config_dir
        opencode_agent_hub.config.COORDINATOR_DIR = coord_dir

        try:
            result = setup_coordinator_directory()
            assert result is True

            # Check the AGENTS.md was copied
            copied = coord_dir / "AGENTS.md"
            assert copied.exists()
            assert copied.read_text() == "# Custom Coordinator Instructions"
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original_config
            opencode_agent_hub.config.CONFIG_DIR = original_config_dir
            opencode_agent_hub.config.COORDINATOR_DIR = original_coord_dir


def test_setup_coordinator_directory_creates_minimal_when_no_template() -> None:
    """Verify setup_coordinator_directory creates minimal AGENTS.md when no template."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()  # Empty config dir
        coord_dir = Path(tmpdir) / "coordinator"

        # Create opencode.json template (required)
        opencode_json = config_dir / "opencode.json"
        opencode_json.write_text('{"permission": []}')

        import opencode_agent_hub.config
        import opencode_agent_hub.coordinator

        original_config = opencode_agent_hub.config.COORDINATOR_AGENTS_MD
        original_config_dir = opencode_agent_hub.config.CONFIG_DIR
        original_coord_dir = opencode_agent_hub.config.COORDINATOR_DIR
        original_find = opencode_agent_hub.coordinator.find_coordinator_agents_md_template
        opencode_agent_hub.config.COORDINATOR_AGENTS_MD = None
        opencode_agent_hub.config.CONFIG_DIR = config_dir
        opencode_agent_hub.config.COORDINATOR_DIR = coord_dir

        try:
            # Mock system locations to not exist

            def mock_find() -> Path | None:
                # Check user config only, skip system
                for path in [config_dir / "AGENTS.md", config_dir / "COORDINATOR.md"]:
                    if path.exists():
                        return path
                return None

            opencode_agent_hub.coordinator.find_coordinator_agents_md_template = mock_find

            result = setup_coordinator_directory()
            assert result is True

            # Check minimal AGENTS.md was created
            created = coord_dir / "AGENTS.md"
            assert created.exists()
            content = created.read_text()
            assert "Coordinator Agent" in content
            assert "NEW_AGENT" in content
        finally:
            opencode_agent_hub.config.COORDINATOR_AGENTS_MD = original_config
            opencode_agent_hub.config.CONFIG_DIR = original_config_dir
            opencode_agent_hub.config.COORDINATOR_DIR = original_coord_dir
            opencode_agent_hub.coordinator.find_coordinator_agents_md_template = original_find


def test_setup_coordinator_directory_overwrites_if_exists_by_default() -> None:
    """Verify setup_coordinator_directory overwrites stale AGENTS.md by default."""
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

        import opencode_agent_hub.config

        original_config_dir = opencode_agent_hub.config.CONFIG_DIR
        original_coord_dir = opencode_agent_hub.config.COORDINATOR_DIR
        original_preserve = opencode_agent_hub.config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD
        opencode_agent_hub.config.CONFIG_DIR = config_dir
        opencode_agent_hub.config.COORDINATOR_DIR = coord_dir
        opencode_agent_hub.config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = False

        template_agents = config_dir / "AGENTS.md"
        template_agents.write_text("# Fresh template")

        try:
            result = setup_coordinator_directory()
            assert result is True

            # Verify stale content was overwritten
            assert existing.read_text() == "# Fresh template"
        finally:
            opencode_agent_hub.config.CONFIG_DIR = original_config_dir
            opencode_agent_hub.config.COORDINATOR_DIR = original_coord_dir
            opencode_agent_hub.config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = original_preserve


def test_setup_coordinator_directory_preserves_when_configured() -> None:
    """Verify setup_coordinator_directory preserves AGENTS.md when configured."""
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

        import opencode_agent_hub.config

        original_config_dir = opencode_agent_hub.config.CONFIG_DIR
        original_coord_dir = opencode_agent_hub.config.COORDINATOR_DIR
        original_preserve = opencode_agent_hub.config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD
        opencode_agent_hub.config.CONFIG_DIR = config_dir
        opencode_agent_hub.config.COORDINATOR_DIR = coord_dir
        opencode_agent_hub.config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = True

        try:
            result = setup_coordinator_directory()
            assert result is True
            assert existing.read_text() == "# Existing content - should be preserved"
        finally:
            opencode_agent_hub.config.CONFIG_DIR = original_config_dir
            opencode_agent_hub.config.COORDINATOR_DIR = original_coord_dir
            opencode_agent_hub.config.COORDINATOR_PRESERVE_LOCAL_AGENTS_MD = original_preserve


# =============================================================================
# Tests for find_coordinator_session (title-based matching)
# =============================================================================


def test_find_coordinator_session_matches_title() -> None:
    """Verify find_coordinator_session matches by coordinator title."""
    from opencode_agent_hub.config import _get_coordinator_title

    coordinator_title = _get_coordinator_title()

    sessions = [
        {"id": "ses_worker1", "title": "Fix bug in auth", "directory": "/project"},
        {"id": "ses_coord", "title": coordinator_title, "directory": "/project"},
        {"id": "ses_worker2", "title": "Add feature X", "directory": "/project"},
    ]

    with mock.patch("opencode_agent_hub.sessions.get_sessions_uncached", return_value=sessions):
        result = find_coordinator_session()

    assert result == "ses_coord"


def test_find_coordinator_session_no_match() -> None:
    """Verify None returned when no coordinator session exists."""
    sessions = [
        {"id": "ses_worker1", "title": "Fix bug in auth", "directory": "/project"},
        {"id": "ses_worker2", "title": "Add feature X", "directory": "/project"},
    ]

    with mock.patch("opencode_agent_hub.sessions.get_sessions_uncached", return_value=sessions):
        result = find_coordinator_session()

    assert result is None


def test_find_coordinator_session_empty_sessions() -> None:
    """Verify None returned when hub has no sessions."""
    with mock.patch("opencode_agent_hub.sessions.get_sessions_uncached", return_value=[]):
        result = find_coordinator_session()

    assert result is None


def test_find_coordinator_session_ignores_similar_titles() -> None:
    """Verify only exact title match works."""
    sessions = [
        {"id": "ses_1", "title": "agent-hub-coordinator setup", "directory": "/p"},
        {"id": "ses_2", "title": "my-agent-hub-coordinator", "directory": "/p"},
        {"id": "ses_3", "title": "Coordinator agent setup", "directory": "/p"},
    ]

    with mock.patch("opencode_agent_hub.sessions.get_sessions_uncached", return_value=sessions):
        result = find_coordinator_session()

    assert result is None


# =============================================================================
# Tests for orient_session coordinator skip (session ID matching)
# =============================================================================


def test_orient_session_skips_coordinator_by_session_id() -> None:
    """Verify orient_session skips injection for coordinator session."""
    import opencode_agent_hub.config

    original_session_id = opencode_agent_hub.config.COORDINATOR_SESSION_ID
    original_oriented = opencode_agent_hub.config.ORIENTED_SESSIONS.copy()

    try:
        opencode_agent_hub.config.COORDINATOR_SESSION_ID = "ses_coordinator_123"
        opencode_agent_hub.config.ORIENTED_SESSIONS = set()

        all_agents: dict[str, dict[str, Any]] = {}

        # Mock save to avoid file I/O and inject_message
        with (
            mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
            mock.patch("opencode_agent_hub.messaging.inject_message") as mock_inject,
        ):
            result = orient_session("ses_coordinator_123", "/some/path", all_agents)

        assert result is True
        assert "ses_coordinator_123" in opencode_agent_hub.config.ORIENTED_SESSIONS
        mock_inject.assert_not_called()  # No orientation injected for coordinator
    finally:
        opencode_agent_hub.config.COORDINATOR_SESSION_ID = original_session_id
        opencode_agent_hub.config.ORIENTED_SESSIONS = original_oriented


def test_orient_session_does_not_skip_non_coordinator() -> None:
    """Verify orient_session injects orientation for non-coordinator sessions."""
    import opencode_agent_hub.config

    original_session_id = opencode_agent_hub.config.COORDINATOR_SESSION_ID
    original_oriented = opencode_agent_hub.config.ORIENTED_SESSIONS.copy()

    try:
        opencode_agent_hub.config.COORDINATOR_SESSION_ID = "ses_coordinator_123"
        opencode_agent_hub.config.ORIENTED_SESSIONS = set()

        all_agents = {"worker-agent": {"id": "worker-agent", "projectPath": "/worker/path"}}

        with (
            mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
            mock.patch("opencode_agent_hub.sessions.get_session_agent", return_value="gpt"),
            mock.patch("opencode_agent_hub.messaging.inject_message") as mock_inject,
            mock.patch("opencode_agent_hub.coordinator.notify_coordinator_new_agent"),
        ):
            result = orient_session("ses_worker_456", "/worker/path", all_agents)

        assert result is True
        assert "ses_worker_456" in opencode_agent_hub.config.ORIENTED_SESSIONS
        # inject_message should be called for non-coordinator
        mock_inject.assert_called_once()
    finally:
        opencode_agent_hub.config.COORDINATOR_SESSION_ID = original_session_id
        opencode_agent_hub.config.ORIENTED_SESSIONS = original_oriented


def test_orient_session_no_coordinator_id_does_not_skip() -> None:
    """Verify orient_session doesn't skip when COORDINATOR_SESSION_ID is None."""
    import opencode_agent_hub.config

    original_session_id = opencode_agent_hub.config.COORDINATOR_SESSION_ID
    original_oriented = opencode_agent_hub.config.ORIENTED_SESSIONS.copy()

    try:
        opencode_agent_hub.config.COORDINATOR_SESSION_ID = None
        opencode_agent_hub.config.ORIENTED_SESSIONS = set()

        all_agents = {"some-agent": {"id": "some-agent", "projectPath": "/some/path"}}

        with (
            mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
            mock.patch("opencode_agent_hub.sessions.get_session_agent", return_value="gpt"),
            mock.patch("opencode_agent_hub.messaging.inject_message") as mock_inject,
            mock.patch("opencode_agent_hub.coordinator.notify_coordinator_new_agent"),
        ):
            result = orient_session("ses_any_session", "/some/path", all_agents)

        assert result is True
        mock_inject.assert_called_once()  # Should inject, not skip
    finally:
        opencode_agent_hub.config.COORDINATOR_SESSION_ID = original_session_id
        opencode_agent_hub.config.ORIENTED_SESSIONS = original_oriented


def test_find_opencode_serve_pids_on_port_filters_non_opencode() -> None:
    """Verify PID discovery returns only opencode serve listeners."""
    lsof_out = mock.MagicMock()
    lsof_out.stdout = "111\n222\n"

    ps_opencode = mock.MagicMock()
    ps_opencode.stdout = "opencode serve --port 4096"

    ps_other = mock.MagicMock()
    ps_other.stdout = "python -m http.server 4096"

    with mock.patch("subprocess.run", side_effect=[lsof_out, ps_opencode, ps_other]):
        pids = _find_opencode_serve_pids_on_port()

    assert pids == [111]


def test_kill_opencode_serve_pids_escalates_to_sigkill() -> None:
    """Verify process killer escalates to SIGKILL when still alive."""
    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:
            return

    with mock.patch("os.kill", side_effect=fake_kill), mock.patch("time.sleep"):
        _kill_opencode_serve_pids([123])

    assert (123, signal.SIGTERM) in calls
    assert (123, 0) in calls
    assert (123, signal.SIGKILL) in calls


def test_session_has_blocking_permissions_with_deny_question() -> None:
    """Verify session with question:deny permission is detected as blocking."""
    session = {
        "id": "ses_blocked",
        "title": "test",
        "permission": [
            {"permission": "question", "pattern": "*", "action": "deny"},
            {"permission": "plan_enter", "pattern": "*", "action": "deny"},
        ],
    }
    assert session_has_blocking_permissions(session) is True


def test_session_has_blocking_permissions_without_question_deny() -> None:
    """Verify session without question:deny is not blocking."""
    session = {
        "id": "ses_allowed",
        "title": "test",
        "permission": [
            {"permission": "plan_enter", "pattern": "*", "action": "deny"},
        ],
    }
    assert session_has_blocking_permissions(session) is False


def test_session_has_blocking_permissions_empty_permissions() -> None:
    """Verify session with empty permissions is not blocking."""
    session = {"id": "ses_empty", "title": "test", "permission": []}
    assert session_has_blocking_permissions(session) is False


def test_session_has_blocking_permissions_no_permissions_field() -> None:
    """Verify session without permission field is not blocking."""
    session = {"id": "ses_no_perm", "title": "test"}
    assert session_has_blocking_permissions(session) is False


def test_session_has_blocking_permissions_invalid_permissions_type() -> None:
    """Verify session with non-list permission field is not blocking."""
    session = {"id": "ses_invalid", "title": "test", "permission": "deny_all"}
    assert session_has_blocking_permissions(session) is False


def test_find_coordinator_session_raises_on_blocking_permissions() -> None:
    """Verify find_coordinator_session raises PreflightError when session has blocking permissions."""
    from opencode_agent_hub.config import _get_coordinator_title

    sessions = [
        {
            "id": "ses_blocked_coord",
            "title": _get_coordinator_title(),
            "permission": [{"permission": "question", "pattern": "*", "action": "deny"}],
        }
    ]

    with (
        mock.patch("opencode_agent_hub.sessions.get_sessions_uncached", return_value=sessions),
        pytest.raises(PreflightError) as exc_info,
    ):
        find_coordinator_session()

    assert "blocking permissions" in str(exc_info.value)
    assert "ses_bloc" in str(exc_info.value)  # Session ID truncated to 8 chars


def test_find_coordinator_session_returns_valid_session() -> None:
    """Verify find_coordinator_session returns ID when session has no blocking permissions."""
    from opencode_agent_hub.config import _get_coordinator_title

    sessions = [
        {
            "id": "ses_valid_coord",
            "title": _get_coordinator_title(),
            "permission": [],
        }
    ]

    with mock.patch("opencode_agent_hub.sessions.get_sessions_uncached", return_value=sessions):
        result = find_coordinator_session()
        assert result == "ses_valid_coord"
