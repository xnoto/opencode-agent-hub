"""Integration tests for agent detection and message injection flow."""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


def test_full_flow_agent_detected_and_used() -> None:
    """Integration test: agent detected from DB and used in injection."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up test database with agent
        db_path = Path(tmpdir) / "opencode.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)
        message_data = json.dumps({"role": "assistant", "agent": "detected-agent"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_target", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        # Set up directories
        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()

        original_db_path = daemon.OPENCODE_DB_PATH
        original_agents_dir = daemon.AGENTS_DIR
        daemon.OPENCODE_DB_PATH = db_path
        daemon.AGENTS_DIR = agents_dir
        daemon.SESSION_AGENTS = {}

        try:
            # Simulate session discovery
            session = {
                "id": "ses_target",
                "slug": "target-slug",
                "directory": "/test/project",
            }
            agents: dict[str, dict] = {}

            # This should detect "detected-agent" from DB
            agent = daemon.get_or_create_agent_for_session(session, agents)
            assert agent["id"] == "detected-agent"
            assert agent["sessionId"] == "ses_target"

            # Verify agent was saved to disk
            agent_file = agents_dir / "detected-agent.json"
            assert agent_file.exists()

            # Now simulate message delivery to this agent
            msg_path = Path(tmpdir) / "msg_inject.json"
            message = {
                "from": "sender-agent",
                "to": "detected-agent",
                "type": "task",
                "content": "Test task",
            }
            msg_path.write_text(json.dumps(message))

            # Add sender agent
            agents["sender-agent"] = {"id": "sender-agent", "projectPath": "/sender"}

            mock_sessions = [
                {
                    "id": "ses_target",
                    "directory": "/test/project",
                    "time": {"created": daemon.DAEMON_START_TIME_MS + 1000},
                }
            ]

            with (
                mock.patch.object(daemon, "get_sessions", return_value=mock_sessions),
                mock.patch.object(daemon, "inject_message") as mock_inject,
            ):
                daemon.process_message_file(msg_path, agents)

                # Verify inject was called with target agent (detected-agent, not sender)
                mock_inject.assert_called_once()
                call_kwargs = mock_inject.call_args[1]
                assert call_kwargs["agent"] == "detected-agent"

        finally:
            daemon.OPENCODE_DB_PATH = original_db_path
            daemon.AGENTS_DIR = original_agents_dir
            daemon.SESSION_AGENTS = {}


def test_agent_switching_mid_session() -> None:
    """Test that daemon uses most recent agent when TUI switches agents."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up test database with multiple messages showing agent switch
        db_path = Path(tmpdir) / "opencode.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)

        # Old message with gpt-4
        old_data = json.dumps({"role": "assistant", "agent": "gpt-4"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_old", "ses_switch", 1700000000000, old_data),
        )

        # Newer message with claude (agent switch)
        new_data = json.dumps({"role": "assistant", "agent": "claude"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_new", "ses_switch", 1700000001000, new_data),
        )

        conn.commit()
        conn.close()

        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()

        original_db_path = daemon.OPENCODE_DB_PATH
        original_agents_dir = daemon.AGENTS_DIR
        daemon.OPENCODE_DB_PATH = db_path
        daemon.AGENTS_DIR = agents_dir
        daemon.SESSION_AGENTS = {}

        try:
            session = {
                "id": "ses_switch",
                "slug": "switch-slug",
                "directory": "/test/project",
            }
            agents: dict[str, dict] = {}

            # Should detect claude (most recent), not gpt-4
            agent = daemon.get_or_create_agent_for_session(session, agents)
            assert agent["id"] == "claude"

        finally:
            daemon.OPENCODE_DB_PATH = original_db_path
            daemon.AGENTS_DIR = original_agents_dir
            daemon.SESSION_AGENTS = {}


def test_multiple_sessions_different_agents() -> None:
    """Test that different sessions can have different detected agents."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up test database with different agents per session
        db_path = Path(tmpdir) / "opencode.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)

        # Session A uses kimi
        kimi_data = json.dumps({"role": "assistant", "agent": "kimi"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)", ("msg_a", "ses_a", 1700000000000, kimi_data)
        )

        # Session B uses claude
        claude_data = json.dumps({"role": "assistant", "agent": "claude"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_b", "ses_b", 1700000000000, claude_data),
        )

        conn.commit()
        conn.close()

        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()

        original_db_path = daemon.OPENCODE_DB_PATH
        original_agents_dir = daemon.AGENTS_DIR
        daemon.OPENCODE_DB_PATH = db_path
        daemon.AGENTS_DIR = agents_dir
        daemon.SESSION_AGENTS = {}

        try:
            agents: dict[str, dict] = {}

            # Session A should get kimi
            session_a = {"id": "ses_a", "slug": "slug-a", "directory": "/a"}
            agent_a = daemon.get_or_create_agent_for_session(session_a, agents)
            assert agent_a["id"] == "kimi"

            # Session B should get claude
            session_b = {"id": "ses_b", "slug": "slug-b", "directory": "/b"}
            agent_b = daemon.get_or_create_agent_for_session(session_b, agents)
            assert agent_b["id"] == "claude"

            # Both should be tracked
            assert len(agents) == 2
            assert "kimi" in agents
            assert "claude" in agents

        finally:
            daemon.OPENCODE_DB_PATH = original_db_path
            daemon.AGENTS_DIR = original_agents_dir
            daemon.SESSION_AGENTS = {}


def test_new_session_no_messages_uses_slug() -> None:
    """Test that brand new sessions (no messages) fall back to slug."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Empty database
        db_path = Path(tmpdir) / "opencode.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()

        original_db_path = daemon.OPENCODE_DB_PATH
        original_agents_dir = daemon.AGENTS_DIR
        daemon.OPENCODE_DB_PATH = db_path
        daemon.AGENTS_DIR = agents_dir
        daemon.SESSION_AGENTS = {}

        try:
            # New session with no messages yet
            session = {
                "id": "ses_new",
                "slug": "my-custom-slug",
                "directory": "/new/project",
            }
            agents: dict[str, dict] = {}

            agent = daemon.get_or_create_agent_for_session(session, agents)

            # Should fall back to slug
            assert agent["id"] == "my-custom-slug"

        finally:
            daemon.OPENCODE_DB_PATH = original_db_path
            daemon.AGENTS_DIR = original_agents_dir
            daemon.SESSION_AGENTS = {}


def test_caching_uses_cached_agent() -> None:
    """Test that SESSION_AGENTS cache prevents repeated DB queries."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up database
        db_path = Path(tmpdir) / "opencode.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            )
        """)
        message_data = json.dumps({"role": "assistant", "agent": "cached-agent"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_1", "ses_cached", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        agents_dir = Path(tmpdir) / "agents"
        agents_dir.mkdir()

        original_db_path = daemon.OPENCODE_DB_PATH
        original_agents_dir = daemon.AGENTS_DIR
        daemon.OPENCODE_DB_PATH = db_path
        daemon.AGENTS_DIR = agents_dir

        # Pre-populate cache
        daemon.SESSION_AGENTS = {
            "ses_cached": {
                "agentId": "cached-agent",
                "directory": "/test",
                "slug": "test-slug",
            }
        }

        try:
            agents: dict[str, dict] = {
                "cached-agent": {
                    "id": "cached-agent",
                    "sessionId": "ses_cached",
                    "projectPath": "/test",
                }
            }

            session = {
                "id": "ses_cached",
                "slug": "different-slug",  # Different from cached
                "directory": "/test",
            }

            # Should return cached agent, not query DB for different slug
            agent = daemon.get_or_create_agent_for_session(session, agents)
            assert agent["id"] == "cached-agent"

        finally:
            daemon.OPENCODE_DB_PATH = original_db_path
            daemon.AGENTS_DIR = original_agents_dir
            daemon.SESSION_AGENTS = {}
