"""Tests for agent detection from SQLite database."""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest


def test_get_agent_from_session_messages_returns_agent() -> None:
    """Verify agent is extracted from most recent message."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test database
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

        # Insert a message with agent
        message_data = json.dumps(
            {"role": "assistant", "agent": "claude", "modelID": "claude-3-sonnet"}
        )
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        # Mock the database path
        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent == "claude"
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_no_messages() -> None:
    """Verify None returned when session has no messages."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_empty")
            assert agent is None
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_db_missing() -> None:
    """Verify None returned when database doesn't exist."""
    from opencode_agent_hub import daemon

    original_path = daemon.OPENCODE_DB_PATH
    daemon.OPENCODE_DB_PATH = Path("/nonexistent/opencode.db")

    try:
        agent = daemon.get_agent_from_session_messages("ses_test")
        assert agent is None
    finally:
        daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_missing_agent_field() -> None:
    """Verify None returned when message has no agent field."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # Insert message without agent field
        message_data = json.dumps({"role": "user", "content": "hello"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent is None
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_uses_most_recent() -> None:
    """Verify agent from most recent message is returned."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # Insert older message with different agent
        old_message = json.dumps({"role": "assistant", "agent": "gpt-4"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_old", "ses_test", 1700000000000, old_message),
        )

        # Insert newer message with different agent
        new_message = json.dumps({"role": "assistant", "agent": "kimi"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_new", "ses_test", 1700000001000, new_message),
        )

        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent == "kimi"  # Should return newest, not oldest
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_invalid_json() -> None:
    """Verify None returned when message data is invalid JSON."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # Insert message with invalid JSON
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test", 1700000000000, "not valid json"),
        )
        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent is None
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_agent_not_string() -> None:
    """Verify None returned when agent is not a string."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # Insert message with agent as object (not string)
        message_data = json.dumps({"role": "assistant", "agent": {"name": "kimi"}})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent is None
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_empty_string() -> None:
    """Verify None returned when agent is empty string."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # Insert message with empty agent string
        message_data = json.dumps({"role": "assistant", "agent": ""})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent is None
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_null() -> None:
    """Verify None returned when agent is null."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # Insert message with null agent
        message_data = json.dumps({"role": "assistant", "agent": None})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path

        try:
            agent = daemon.get_agent_from_session_messages("ses_test")
            assert agent is None
        finally:
            daemon.OPENCODE_DB_PATH = original_path


def test_get_agent_from_session_messages_db_locked() -> None:
    """Verify None returned gracefully when database is locked."""
    from opencode_agent_hub import daemon

    # Mock sqlite3.connect to raise OperationalError
    with mock.patch("sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")):
        agent = daemon.get_agent_from_session_messages("ses_test")
        assert agent is None


def test_get_or_create_agent_uses_detected_agent_from_db() -> None:
    """Verify agent from database is used when available."""
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
        message_data = json.dumps({"role": "assistant", "agent": "custom-agent"})
        conn.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            ("msg_123", "ses_test123", 1700000000000, message_data),
        )
        conn.commit()
        conn.close()

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path
        daemon.SESSION_AGENTS = {}

        try:
            session = {
                "id": "ses_test123",
                "slug": "test-slug",
                "directory": "/home/user/project",
            }
            agents: dict[str, dict] = {}

            agent = daemon.get_or_create_agent_for_session(session, agents)

            # Should use detected agent, not slug
            assert agent["id"] == "custom-agent"
            assert agent["sessionId"] == "ses_test123"
        finally:
            daemon.OPENCODE_DB_PATH = original_path
            daemon.SESSION_AGENTS = {}


def test_get_or_create_agent_falls_back_to_slug_when_no_db_agent() -> None:
    """Verify slug is used when no agent in database."""
    from opencode_agent_hub import daemon

    with tempfile.TemporaryDirectory() as tmpdir:
        # Empty database - no messages
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

        original_path = daemon.OPENCODE_DB_PATH
        daemon.OPENCODE_DB_PATH = db_path
        daemon.SESSION_AGENTS = {}

        try:
            session = {
                "id": "ses_test456",
                "slug": "fallback-slug",
                "directory": "/home/user/project",
            }
            agents: dict[str, dict] = {}

            agent = daemon.get_or_create_agent_for_session(session, agents)

            # Should fall back to slug
            assert agent["id"] == "fallback-slug"
        finally:
            daemon.OPENCODE_DB_PATH = original_path
            daemon.SESSION_AGENTS = {}


def test_get_or_create_agent_falls_back_when_db_missing() -> None:
    """Verify slug is used when database doesn't exist."""
    from opencode_agent_hub import daemon

    original_path = daemon.OPENCODE_DB_PATH
    daemon.OPENCODE_DB_PATH = Path("/nonexistent/opencode.db")
    daemon.SESSION_AGENTS = {}

    try:
        session = {
            "id": "ses_test789",
            "slug": "no-db-slug",
            "directory": "/home/user/project",
        }
        agents: dict[str, dict] = {}

        agent = daemon.get_or_create_agent_for_session(session, agents)

        # Should fall back to slug
        assert agent["id"] == "no-db-slug"
    finally:
        daemon.OPENCODE_DB_PATH = original_path
        daemon.SESSION_AGENTS = {}
