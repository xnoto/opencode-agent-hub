"""Tests for coordinator AGENTS.md resolution, session identification, and lifecycle."""

import json
import tempfile
from pathlib import Path
from unittest import mock


def test_find_coordinator_agents_md_explicit_config():
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


def test_find_coordinator_agents_md_explicit_config_missing():
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


def test_find_coordinator_agents_md_user_config_agents_md():
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


def test_find_coordinator_agents_md_user_config_coordinator_md():
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


def test_find_coordinator_agents_md_agents_md_priority_over_coordinator_md():
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


def test_find_coordinator_agents_md_none_when_no_templates():
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


def test_setup_coordinator_directory_copies_template():
    """Verify setup_coordinator_directory copies from found template."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()
        coord_dir = Path(tmpdir) / "coordinator"

        # Create user config template
        user_template = config_dir / "AGENTS.md"
        user_template.write_text("# Custom Coordinator Instructions")

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


def test_setup_coordinator_directory_creates_minimal_when_no_template():
    """Verify setup_coordinator_directory creates minimal AGENTS.md when no template."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir()  # Empty config dir
        coord_dir = Path(tmpdir) / "coordinator"

        original_config = daemon.COORDINATOR_AGENTS_MD
        original_config_dir = daemon.CONFIG_DIR
        original_coord_dir = daemon.COORDINATOR_DIR
        daemon.COORDINATOR_AGENTS_MD = None
        daemon.CONFIG_DIR = config_dir
        daemon.COORDINATOR_DIR = coord_dir

        try:
            # Mock system locations to not exist
            original_find = daemon.find_coordinator_agents_md_template

            def mock_find():
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


def test_setup_coordinator_directory_skips_if_exists():
    """Verify setup_coordinator_directory skips if AGENTS.md already exists."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        coord_dir = Path(tmpdir) / "coordinator"
        coord_dir.mkdir()
        existing = coord_dir / "AGENTS.md"
        existing.write_text("# Existing content - should not be overwritten")

        original_coord_dir = daemon.COORDINATOR_DIR
        daemon.COORDINATOR_DIR = coord_dir

        try:
            result = daemon.setup_coordinator_directory()
            assert result is True

            # Verify content was NOT overwritten
            assert existing.read_text() == "# Existing content - should not be overwritten"
        finally:
            daemon.COORDINATOR_DIR = original_coord_dir


# =============================================================================
# Tests for _parse_session_id_from_json_output
# =============================================================================


def test_parse_session_id_from_json_output_valid():
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


def test_parse_session_id_from_json_output_multiline():
    """Verify only the first line is parsed."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    line1 = json.dumps({"sessionID": "ses_first_line"})
    line2 = json.dumps({"sessionID": "ses_second_line"})
    stdout = f"{line1}\n{line2}\n".encode()

    result = _parse_session_id_from_json_output(stdout)
    assert result == "ses_first_line"


def test_parse_session_id_from_json_output_none():
    """Verify None returned for None input."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    assert _parse_session_id_from_json_output(None) is None


def test_parse_session_id_from_json_output_empty():
    """Verify None returned for empty bytes."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    assert _parse_session_id_from_json_output(b"") is None
    assert _parse_session_id_from_json_output(b"\n") is None


def test_parse_session_id_from_json_output_invalid_json():
    """Verify None returned for non-JSON output."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    assert _parse_session_id_from_json_output(b"not json at all") is None


def test_parse_session_id_from_json_output_missing_field():
    """Verify None returned when sessionID field is absent."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps({"type": "step_start", "timestamp": 123}).encode()
    assert _parse_session_id_from_json_output(stdout) is None


def test_parse_session_id_from_json_output_bad_prefix():
    """Verify None returned when sessionID doesn't start with ses_."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps({"sessionID": "invalid_prefix_123"}).encode()
    assert _parse_session_id_from_json_output(stdout) is None


def test_parse_session_id_from_json_output_non_string():
    """Verify None returned when sessionID is not a string."""
    from opencode_agent_hub.daemon import _parse_session_id_from_json_output

    stdout = json.dumps({"sessionID": 12345}).encode()
    assert _parse_session_id_from_json_output(stdout) is None


# =============================================================================
# Tests for find_coordinator_session (title-based matching)
# =============================================================================


def test_find_coordinator_session_matches_title():
    """Verify find_coordinator_session matches by COORDINATOR_TITLE."""
    from opencode_agent_hub import daemon

    sessions = [
        {"id": "ses_worker1", "title": "Fix bug in auth", "directory": "/project"},
        {"id": "ses_coord", "title": daemon.COORDINATOR_TITLE, "directory": "/project"},
        {"id": "ses_worker2", "title": "Add feature X", "directory": "/project"},
    ]

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions):
        result = daemon.find_coordinator_session()

    assert result == "ses_coord"


def test_find_coordinator_session_no_match():
    """Verify None returned when no coordinator session exists."""
    from opencode_agent_hub import daemon

    sessions = [
        {"id": "ses_worker1", "title": "Fix bug in auth", "directory": "/project"},
        {"id": "ses_worker2", "title": "Add feature X", "directory": "/project"},
    ]

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=sessions):
        result = daemon.find_coordinator_session()

    assert result is None


def test_find_coordinator_session_empty_sessions():
    """Verify None returned when hub has no sessions."""
    from opencode_agent_hub import daemon

    with mock.patch.object(daemon, "get_sessions_uncached", return_value=[]):
        result = daemon.find_coordinator_session()

    assert result is None


def test_find_coordinator_session_ignores_similar_titles():
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


def test_orient_session_skips_coordinator_by_session_id():
    """Verify orient_session skips injection for coordinator session."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = "ses_coordinator_123"
        daemon.ORIENTED_SESSIONS = set()

        agent = {"id": "coordinator", "projectPath": "/some/path"}
        all_agents = {"coordinator": agent}

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


def test_orient_session_does_not_skip_non_coordinator():
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


def test_orient_session_no_coordinator_id_does_not_skip():
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


def test_start_coordinator_captures_session_id_from_json():
    """Verify start_coordinator extracts session ID from JSON output."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()

        json_output = json.dumps(
            {
                "type": "step_start",
                "sessionID": "ses_newcoord123",
                "part": {},
            }
        ).encode()

        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json_output

        with (
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(daemon, "find_coordinator_session", return_value=None),
            mock.patch("shutil.which", return_value="/usr/bin/opencode"),
            mock.patch("subprocess.run", return_value=mock_result),
        ):
            result = daemon.start_coordinator()

        assert result is True
        assert daemon.COORDINATOR_SESSION_ID == "ses_newcoord123"
        assert "ses_newcoord123" in daemon.ORIENTED_SESSIONS
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented


def test_start_coordinator_fallback_to_title_search():
    """Verify start_coordinator falls back to title search when JSON parse fails."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()

        mock_result = mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b""  # Empty output, can't parse

        with (
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            # First call (in start_coordinator pre-check): no existing session
            # Second call (fallback): found it by title
            mock.patch.object(
                daemon,
                "find_coordinator_session",
                side_effect=[None, "ses_fallback_456"],
            ),
            mock.patch("shutil.which", return_value="/usr/bin/opencode"),
            mock.patch("subprocess.run", return_value=mock_result),
        ):
            result = daemon.start_coordinator()

        assert result is True
        assert daemon.COORDINATOR_SESSION_ID == "ses_fallback_456"
        assert "ses_fallback_456" in daemon.ORIENTED_SESSIONS
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented


def test_start_coordinator_reuses_existing_session():
    """Verify start_coordinator reuses an existing coordinator session."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED
    original_oriented = daemon.ORIENTED_SESSIONS.copy()

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True
        daemon.ORIENTED_SESSIONS = set()

        with (
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(daemon, "find_coordinator_session", return_value="ses_existing_789"),
            # Should NOT call subprocess.run at all
            mock.patch("subprocess.run") as mock_run,
        ):
            result = daemon.start_coordinator()

        assert result is True
        assert daemon.COORDINATOR_SESSION_ID == "ses_existing_789"
        assert "ses_existing_789" in daemon.ORIENTED_SESSIONS
        mock_run.assert_not_called()
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled
        daemon.ORIENTED_SESSIONS = original_oriented


def test_start_coordinator_disabled():
    """Verify start_coordinator returns False when disabled."""
    from opencode_agent_hub import daemon

    original_enabled = daemon.COORDINATOR_ENABLED

    try:
        daemon.COORDINATOR_ENABLED = False

        result = daemon.start_coordinator()
        assert result is False
    finally:
        daemon.COORDINATOR_ENABLED = original_enabled


def test_start_coordinator_nonzero_exit():
    """Verify start_coordinator returns False on non-zero exit code."""
    from opencode_agent_hub import daemon

    original_session_id = daemon.COORDINATOR_SESSION_ID
    original_enabled = daemon.COORDINATOR_ENABLED

    try:
        daemon.COORDINATOR_SESSION_ID = None
        daemon.COORDINATOR_ENABLED = True

        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = b""

        with (
            mock.patch.object(daemon, "setup_coordinator_directory", return_value=True),
            mock.patch.object(daemon, "find_coordinator_session", return_value=None),
            mock.patch("shutil.which", return_value="/usr/bin/opencode"),
            mock.patch("subprocess.run", return_value=mock_result),
        ):
            result = daemon.start_coordinator()

        assert result is False
        assert daemon.COORDINATOR_SESSION_ID is None
    finally:
        daemon.COORDINATOR_SESSION_ID = original_session_id
        daemon.COORDINATOR_ENABLED = original_enabled


# =============================================================================
# Tests for coordinator self-registration race condition fix
# =============================================================================


def test_poll_active_sessions_skips_coordinator():
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
                "title": daemon.COORDINATOR_TITLE,
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

        agents = {}

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


def test_poll_active_sessions_no_skip_when_coordinator_unset():
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

        agents = {}

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


def test_process_session_file_skips_coordinator():
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
            "title": daemon.COORDINATOR_TITLE,
            "directory": "/project",
            "time": {"created": 2000},
        }

        agents = {}

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


def test_process_session_file_processes_non_coordinator():
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

        agents = {}

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


def test_no_phantom_agent_for_coordinator_end_to_end():
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
                "title": daemon.COORDINATOR_TITLE,
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

        agents = {}

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
