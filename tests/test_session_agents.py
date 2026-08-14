"""Tests for session-based agent identity functionality."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from opencode_agent_hub.garbage_collector import gc_session_agents
from opencode_agent_hub.persistence import (
    load_session_agents,
    save_session_agents,
)
from opencode_agent_hub.sessions import (
    find_session_for_agent,
    find_sessions_for_agent,
    format_notification,
    generate_agent_id_for_session,
    get_sessions_from_db,
)


def test_generate_agent_id_for_session_with_slug() -> None:
    """Verify agent ID is generated from session slug when available."""
    session = {
        "id": "ses_abc123def456",
        "slug": "cosmic-panda",
        "directory": "/home/user/project",
    }

    agent_id = generate_agent_id_for_session(session)
    # Generate pseudorandom ID, should contain hyphenated parts
    assert "-" in agent_id


def test_generate_agent_id_for_session_without_slug() -> None:
    """Verify agent ID is generated when slug is missing."""
    session = {
        "id": "ses_abc123def456ghi789",
        "directory": "/home/user/project",
    }

    agent_id = generate_agent_id_for_session(session)
    # Should generate a pseudorandom ID with hyphenated parts
    assert "-" in agent_id


def test_generate_agent_id_for_session_empty_slug() -> None:
    """Verify empty slug still generates a valid agent ID."""
    session = {
        "id": "ses_xyz789",
        "slug": "",
        "directory": "/home/user/project",
    }

    agent_id = generate_agent_id_for_session(session)
    # Should generate a pseudorandom ID with hyphenated parts
    assert "-" in agent_id


def test_find_session_for_agent_with_session_id() -> None:
    """Verify session lookup works with sessionId field."""
    agent = {
        "id": "test-agent",
        "sessionId": "ses_target",
        "projectPath": "/home/user/project",
    }

    sessions = [
        {"id": "ses_other", "directory": "/home/user/other"},
        {"id": "ses_target", "directory": "/home/user/project"},
    ]

    session = find_session_for_agent(agent, sessions)

    assert session is not None
    assert session["id"] == "ses_target"


def test_find_session_for_agent_fallback_to_session_agents() -> None:
    """Verify session lookup falls back to SESSION_AGENTS mapping for legacy agents."""
    # Set up SESSION_AGENTS mapping for legacy agent
    test_session_agents = {
        "ses_match": {"agentId": "legacy-agent", "directory": "/home/user/project"},
    }

    agent = {
        "id": "legacy-agent",
        "projectPath": "/home/user/project",
        # No sessionId - legacy agent
    }

    sessions = [
        {"id": "ses_match", "directory": "/home/user/project"},
        {"id": "ses_other", "directory": "/home/user/other"},
    ]

    with mock.patch("opencode_agent_hub.config.SESSION_AGENTS", test_session_agents):
        session = find_session_for_agent(agent, sessions)

    assert session is not None
    assert session["id"] == "ses_match"


def test_gc_session_agents_removes_stale() -> None:
    """Verify gc_session_agents removes mappings for missing and stale sessions."""
    import time as _time

    now_ms = int(_time.time() * 1000)

    # Set up session agents: one active, one missing from DB, one in DB but stale
    test_session_agents = {
        "ses_active": {"agentId": "active-agent", "directory": "/active"},
        "ses_missing": {"agentId": "missing-agent", "directory": "/missing"},
        "ses_stale": {"agentId": "stale-agent", "directory": "/stale"},
    }

    with (
        mock.patch("opencode_agent_hub.garbage_collector.SESSION_AGENTS", test_session_agents),
        mock.patch("opencode_agent_hub.garbage_collector.get_sessions") as mock_get_sessions,
    ):
        mock_get_sessions.return_value = [
            # Active: updated recently
            {"id": "ses_active", "directory": "/active", "time": {"updated": now_ms}},
            # Stale: updated 2 hours ago (beyond AGENT_STALE_SECONDS=3600)
            {"id": "ses_stale", "directory": "/stale", "time": {"updated": now_ms - 7200_000}},
            # ses_missing not returned at all
        ]

        with mock.patch("opencode_agent_hub.garbage_collector.save_session_agents"):
            cleaned = gc_session_agents()

    assert cleaned == 2
    assert "ses_active" in test_session_agents
    assert "ses_missing" not in test_session_agents
    assert "ses_stale" not in test_session_agents


def test_gc_session_agents_empty() -> None:
    """Verify gc_session_agents handles empty mapping."""
    test_session_agents: dict[str, dict] = {}

    with mock.patch("opencode_agent_hub.garbage_collector.SESSION_AGENTS", test_session_agents):
        cleaned = gc_session_agents()

    assert cleaned == 0


def test_gc_session_agents_api_failure() -> None:
    """Verify gc_session_agents doesn't clear on API failure."""
    test_session_agents = {
        "ses_keep": {"agentId": "keep-agent", "directory": "/keep"},
    }

    with mock.patch("opencode_agent_hub.garbage_collector.SESSION_AGENTS", test_session_agents):
        # Mock get_sessions to return None (simulating API failure)
        with mock.patch("opencode_agent_hub.garbage_collector.get_sessions") as mock_get_sessions:
            mock_get_sessions.return_value = None

            cleaned = gc_session_agents()

        # Should not clean anything on API failure
        assert cleaned == 0
        assert "ses_keep" in test_session_agents


def test_save_load_session_agents() -> None:
    """Verify session agents can be saved and loaded."""

    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock the file path
        from opencode_agent_hub import config, persistence

        test_file = Path(tmpdir) / "session_agents.json"
        test_dir = Path(tmpdir)

        # Patch in both config and persistence modules
        with (
            mock.patch.object(config, "SESSION_AGENTS_FILE", test_file),
            mock.patch.object(config, "AGENT_HUB_DIR", test_dir),
            mock.patch.object(persistence, "SESSION_AGENTS_FILE", test_file),
            mock.patch.object(persistence, "AGENT_HUB_DIR", test_dir),
        ):
            # Set and save - patch SESSION_AGENTS in persistence module where it's used
            test_data = {
                "ses_test": {"agentId": "test-agent", "directory": "/test"},
            }
            with mock.patch.object(persistence, "SESSION_AGENTS", test_data):
                save_session_agents()

            # Verify file was written
            assert test_file.exists()
            content = json.loads(test_file.read_text())
            assert content == test_data

            # Clear and reload - start with empty dict
            with mock.patch.object(persistence, "SESSION_AGENTS", {}):
                loaded = load_session_agents()
            assert loaded == {"ses_test": {"agentId": "test-agent", "directory": "/test"}}


# =============================================================================
# Tests for get_sessions_from_db (SQLite session discovery)
# =============================================================================


def test_get_sessions_from_db_returns_none_when_db_missing() -> None:
    """Verify None returned when the SQLite database file doesn't exist."""
    with mock.patch(
        "opencode_agent_hub.sessions.OPENCODE_DB_PATH", Path("/nonexistent/opencode.db")
    ):
        result = get_sessions_from_db()
        assert result is None


def test_get_sessions_from_db_reads_sessions(tmp_path: Path) -> None:
    """Verify sessions are read from a real SQLite database."""
    import sqlite3

    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE session ("
        "  id TEXT PRIMARY KEY,"
        "  slug TEXT,"
        "  project_id TEXT,"
        "  directory TEXT,"
        "  title TEXT,"
        "  version TEXT,"
        "  time_created INTEGER,"
        "  time_updated INTEGER,"
        "  time_archived INTEGER"
        ")"
    )
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ses_test123",
            "cool-slug",
            "proj_abc",
            "/home/user/project",
            "Test Session",
            "1.0.0",
            1700000000000,
            1700000060000,
            None,
        ),
    )
    # Archived session should be excluded
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ses_archived",
            "old-slug",
            "proj_abc",
            "/home/user/project",
            "Archived",
            "1.0.0",
            1699000000000,
            1699000060000,
            1699000120000,
        ),
    )
    conn.commit()
    conn.close()

    with mock.patch("opencode_agent_hub.sessions.OPENCODE_DB_PATH", db_path):
        result = get_sessions_from_db()
        assert result is not None
        assert len(result) == 1
        session = result[0]
        assert session["id"] == "ses_test123"
        assert session["slug"] == "cool-slug"
        assert session["projectID"] == "proj_abc"
        assert session["directory"] == "/home/user/project"
        assert session["title"] == "Test Session"
        assert session["time"]["created"] == 1700000000000
        assert session["time"]["updated"] == 1700000060000


# =============================================================================
# Tests for find_sessions_for_agent
# =============================================================================


def test_find_sessions_for_agent_by_session_id() -> None:
    """Verify find_sessions_for_agent works with sessionId field."""
    agent = {
        "id": "test-agent",
        "sessionId": "ses_target",
        "projectPath": "/home/user/project",
    }

    sessions = [
        {"id": "ses_other", "directory": "/home/user/other"},
        {"id": "ses_target", "directory": "/home/user/project"},
    ]

    result = find_sessions_for_agent(agent, sessions)

    assert len(result) == 1
    assert result[0]["id"] == "ses_target"


def test_find_sessions_for_agent_fallback_directory() -> None:
    """Verify find_sessions_for_agent falls back to directory matching."""
    agent = {
        "id": "test-agent",
        # No sessionId - uses projectPath
        "projectPath": "/home/user/project",
    }

    sessions = [
        {"id": "ses_match", "directory": "/home/user/project"},
        {"id": "ses_other", "directory": "/home/user/other"},
    ]

    result = find_sessions_for_agent(agent, sessions)

    assert len(result) == 1
    assert result[0]["id"] == "ses_match"


def test_find_sessions_for_agent_returns_most_recent() -> None:
    """Verify returns most recent session when multiple match."""
    agent = {
        "id": "test-agent",
        "projectPath": "/home/user/project",
    }

    sessions = [
        {
            "id": "ses_older",
            "directory": "/home/user/project",
            "time": {"updated": 1000},
        },
        {
            "id": "ses_newer",
            "directory": "/home/user/project",
            "time": {"updated": 2000},
        },
    ]

    result = find_sessions_for_agent(agent, sessions)

    assert len(result) == 1
    assert result[0]["id"] == "ses_newer"


def test_find_sessions_for_agent_no_match() -> None:
    """Verify empty list returned when no sessions match."""
    agent = {
        "id": "test-agent",
        "sessionId": "ses_nonexistent",
        "projectPath": "/home/user/project",
    }

    sessions = [
        {"id": "ses_other", "directory": "/home/user/other"},
    ]

    result = find_sessions_for_agent(agent, sessions)

    assert result == []


# =============================================================================
# Tests for format_notification
# =============================================================================


def test_format_notification_basic() -> None:
    """Test basic message formatting."""
    msg = {
        "from": "agent-a",
        "type": "task",
        "content": "Please review this code",
        "priority": "normal",
    }

    result = format_notification(msg, "agent-b")

    assert "[task] from agent-a" in result
    assert "Please review this code" in result
    assert 'agent-hub_send_message(from="agent-b"' in result


def test_format_notification_urgent() -> None:
    """Test urgent priority adds prefix."""
    msg = {
        "from": "agent-a",
        "type": "task",
        "content": "Critical bug fix needed",
        "priority": "urgent",
    }

    result = format_notification(msg, "agent-b")

    assert result.startswith("URGENT: ")
    assert "[task] from agent-a" in result


def test_format_notification_with_thread() -> None:
    """Test thread ID inclusion."""
    msg = {
        "from": "agent-a",
        "type": "context",
        "content": "Update on progress",
        "priority": "normal",
        "threadId": "thread-123",
    }

    result = format_notification(msg, "agent-b")

    assert "(thread: thread-123)" in result
