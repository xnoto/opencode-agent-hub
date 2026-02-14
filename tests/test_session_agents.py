"""Tests for session-based agent identity functionality."""

import json
import tempfile
from pathlib import Path
from unittest import mock


def test_generate_agent_id_for_session_with_slug():
    """Verify agent ID is generated from session slug when available."""
    from opencode_agent_hub.daemon import generate_agent_id_for_session

    session = {
        "id": "ses_abc123def456",
        "slug": "cosmic-panda",
        "directory": "/home/user/project",
    }

    agent_id = generate_agent_id_for_session(session)
    assert agent_id == "cosmic-panda"


def test_generate_agent_id_for_session_without_slug():
    """Verify agent ID is generated from session ID when slug is missing."""
    from opencode_agent_hub.daemon import generate_agent_id_for_session

    session = {
        "id": "ses_abc123def456ghi789",
        "directory": "/home/user/project",
    }

    agent_id = generate_agent_id_for_session(session)
    # Should use "session-" prefix with truncated ID (after ses_ prefix)
    assert agent_id == "session-abc123def456"


def test_generate_agent_id_for_session_empty_slug():
    """Verify empty slug falls back to session ID."""
    from opencode_agent_hub.daemon import generate_agent_id_for_session

    session = {
        "id": "ses_xyz789",
        "slug": "",
        "directory": "/home/user/project",
    }

    agent_id = generate_agent_id_for_session(session)
    # Empty slug treated as falsy, falls back to session ID format
    assert agent_id == "session-xyz789"


def test_get_or_create_agent_for_session_new():
    """Verify new agent is created for unknown session."""
    from opencode_agent_hub import daemon

    # Clear session agents
    daemon.SESSION_AGENTS = {}

    session = {
        "id": "ses_new123",
        "slug": "brave-tiger",
        "directory": "/home/user/newproject",
    }
    agents = {}

    agent = daemon.get_or_create_agent_for_session(session, agents)

    assert agent["id"] == "brave-tiger"
    assert agent["sessionId"] == "ses_new123"
    assert agent["projectPath"] == "/home/user/newproject"
    assert "ses_new123" in daemon.SESSION_AGENTS


def test_get_or_create_agent_for_session_existing():
    """Verify existing agent is returned for known session."""
    from opencode_agent_hub import daemon

    session = {
        "id": "ses_existing",
        "slug": "lazy-bear",
        "directory": "/home/user/existingproject",
    }

    # Pre-populate session agents
    daemon.SESSION_AGENTS = {
        "ses_existing": {
            "agentId": "lazy-bear",
            "directory": "/home/user/existingproject",
            "slug": "lazy-bear",
        }
    }

    existing_agent = {
        "id": "lazy-bear",
        "sessionId": "ses_existing",
        "projectPath": "/home/user/existingproject",
        "lastSeen": 12345,
    }
    agents = {"lazy-bear": existing_agent}

    agent = daemon.get_or_create_agent_for_session(session, agents)

    # Should return existing agent, not create new one
    assert agent["id"] == "lazy-bear"
    assert agent["lastSeen"] == 12345


def test_find_session_for_agent_with_session_id():
    """Verify session lookup works with sessionId field."""
    from opencode_agent_hub.daemon import find_session_for_agent

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


def test_find_session_for_agent_fallback_to_session_agents():
    """Verify session lookup falls back to SESSION_AGENTS mapping for legacy agents."""
    from opencode_agent_hub import daemon
    from opencode_agent_hub.daemon import find_session_for_agent

    # Set up SESSION_AGENTS mapping for legacy agent
    daemon.SESSION_AGENTS = {
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

    session = find_session_for_agent(agent, sessions)

    assert session is not None
    assert session["id"] == "ses_match"

    # Cleanup
    daemon.SESSION_AGENTS = {}


def test_gc_session_agents_removes_stale():
    """Verify gc_session_agents removes mappings for non-existent sessions."""
    from opencode_agent_hub import daemon

    # Set up session agents with one that doesn't exist anymore
    daemon.SESSION_AGENTS = {
        "ses_active": {"agentId": "active-agent", "directory": "/active"},
        "ses_stale": {"agentId": "stale-agent", "directory": "/stale"},
    }

    # Mock get_sessions to return only one session
    with mock.patch.object(daemon, "get_sessions") as mock_get_sessions:
        mock_get_sessions.return_value = [
            {"id": "ses_active", "directory": "/active"},
        ]

        # Mock save to avoid file I/O
        with mock.patch.object(daemon, "save_session_agents"):
            cleaned = daemon.gc_session_agents()

    assert cleaned == 1
    assert "ses_active" in daemon.SESSION_AGENTS
    assert "ses_stale" not in daemon.SESSION_AGENTS


def test_gc_session_agents_empty():
    """Verify gc_session_agents handles empty mapping."""
    from opencode_agent_hub import daemon

    daemon.SESSION_AGENTS = {}

    cleaned = daemon.gc_session_agents()

    assert cleaned == 0


def test_gc_session_agents_api_failure():
    """Verify gc_session_agents doesn't clear on API failure."""
    from opencode_agent_hub import daemon

    daemon.SESSION_AGENTS = {
        "ses_keep": {"agentId": "keep-agent", "directory": "/keep"},
    }

    # Mock get_sessions to return None (simulating API failure)
    with mock.patch.object(daemon, "get_sessions") as mock_get_sessions:
        mock_get_sessions.return_value = None

        cleaned = daemon.gc_session_agents()

    # Should not clean anything on API failure
    assert cleaned == 0
    assert "ses_keep" in daemon.SESSION_AGENTS


def test_save_load_session_agents():
    """Verify session agents can be saved and loaded."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock the file path
        original_file = daemon.SESSION_AGENTS_FILE
        original_dir = daemon.AGENT_HUB_DIR
        daemon.SESSION_AGENTS_FILE = Path(tmpdir) / "session_agents.json"
        daemon.AGENT_HUB_DIR = Path(tmpdir)

        try:
            # Set and save
            daemon.SESSION_AGENTS = {
                "ses_test": {"agentId": "test-agent", "directory": "/test"},
            }
            daemon.save_session_agents()

            # Verify file was written
            assert daemon.SESSION_AGENTS_FILE.exists()
            content = json.loads(daemon.SESSION_AGENTS_FILE.read_text())
            assert content == daemon.SESSION_AGENTS

            # Clear and reload
            daemon.SESSION_AGENTS = {}
            loaded = daemon.load_session_agents()
            assert loaded == {"ses_test": {"agentId": "test-agent", "directory": "/test"}}

        finally:
            daemon.SESSION_AGENTS_FILE = original_file
            daemon.AGENT_HUB_DIR = original_dir
