"""Integration test for daemon session interaction.

This test verifies that:
1. Daemon detects new sessions via SQLite
2. Injects orientation message once (not multiple times)
3. Properly tracks oriented sessions
"""

import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def test_db(tmp_path: Path):
    """Create a test SQLite database with session table."""
    db_path = tmp_path / "test_opencode.db"
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
    conn.commit()
    conn.close()
    return db_path


def test_daemon_orients_new_session_once(test_db: Path, tmp_path: Path):
    """Verify daemon orients a new session exactly once."""
    from opencode_agent_hub.config import ORIENTED_SESSIONS
    from opencode_agent_hub.sessions import (
        orient_session,
    )

    # Clear state
    ORIENTED_SESSIONS.clear()

    # Create test session in database
    conn = sqlite3.connect(str(test_db))
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ses_test_integration",
            "test-session",
            "proj_test",
            "/tmp/test-project",
            "Test Session",
            "1.0.0",
            now_ms,
            now_ms,
            None,
        ),
    )
    conn.commit()
    conn.close()

    # Track injection calls
    injection_calls = []

    def mock_inject(session_id: str, text: str) -> None:
        injection_calls.append((session_id, text))

    # Mock dependencies
    agents = {}
    session = {
        "id": "ses_test_integration",
        "directory": "/tmp/test-project",
        "time": {"created": now_ms, "updated": now_ms},
    }

    with (
        mock.patch("opencode_agent_hub.messaging.inject_message", side_effect=mock_inject),
        mock.patch("opencode_agent_hub.sessions.get_session_agent", return_value="gpt"),
        mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
        mock.patch("opencode_agent_hub.config.COORDINATOR_SESSION_ID", None),
    ):
        # Call orient_session directly - it should add to ORIENTED_SESSIONS after success
        result = orient_session(
            "ses_test_integration",
            "/tmp/test-project",
            agents,
            session=session,
        )

    # Verify injection occurred
    assert result is True
    assert len(injection_calls) == 1, f"Expected 1 injection, got {len(injection_calls)}"
    session_id, text = injection_calls[0]
    assert session_id == "ses_test_integration"
    assert "AGENT HUB:" in text
    assert "EXECUTE NOW: agent-hub_register_agent" in text

    # Verify session is tracked as oriented (orient_session adds it)
    assert "ses_test_integration" in ORIENTED_SESSIONS

    print("✅ Session oriented exactly once")


def test_orient_session_skips_already_oriented():
    """Verify orient_session skips sessions already in ORIENTED_SESSIONS."""
    from opencode_agent_hub.config import ORIENTED_SESSIONS
    from opencode_agent_hub.sessions import orient_session

    # Clear state
    ORIENTED_SESSIONS.clear()

    # Pre-add session to ORIENTED_SESSIONS (simulating it was already oriented)
    ORIENTED_SESSIONS.add("ses_already_oriented")

    injection_calls = []

    def mock_inject(session_id: str, text: str) -> None:
        injection_calls.append((session_id, text))

    agents = {}
    session = {
        "id": "ses_already_oriented",
        "directory": "/tmp/test",
        "time": {"created": int(time.time() * 1000)},
    }

    with (
        mock.patch("opencode_agent_hub.messaging.inject_message", side_effect=mock_inject),
        mock.patch("opencode_agent_hub.config.COORDINATOR_SESSION_ID", None),
    ):
        # Call orient_session for an already-oriented session
        result = orient_session(
            "ses_already_oriented",
            "/tmp/test",
            agents,
            session=session,
        )

    # Should return False (skipped) and not inject
    assert result is False
    assert len(injection_calls) == 0, "Should not inject for already-oriented session"

    print("✅ orient_session correctly skips already-oriented sessions")


def test_orient_session_thread_safe():
    """Verify orient_session is thread-safe (no duplicate injections)."""
    from opencode_agent_hub.config import ORIENTED_SESSIONS
    from opencode_agent_hub.sessions import orient_session

    ORIENTED_SESSIONS.clear()

    injection_count = 0
    injection_lock = threading.Lock()

    def mock_inject(session_id: str, text: str) -> None:
        nonlocal injection_count
        with injection_lock:
            injection_count += 1

    agents = {}

    with (
        mock.patch("opencode_agent_hub.messaging.inject_message", side_effect=mock_inject),
        mock.patch("opencode_agent_hub.sessions.get_session_agent", return_value="gpt"),
        mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
        mock.patch("opencode_agent_hub.config.COORDINATOR_SESSION_ID", None),
    ):
        # Simulate multiple threads trying to orient the same session
        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=orient_session,
                args=("ses_concurrent", "/tmp/test", agents),
            )
            threads.append(t)

        # Start all threads simultaneously
        for t in threads:
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

    # Should only inject once despite 10 concurrent attempts
    assert injection_count == 1, f"Expected 1 injection, got {injection_count}"

    print("✅ Thread-safe: only 1 injection despite 10 concurrent attempts")


def test_session_detection_from_sqlite(test_db: Path):
    """Verify sessions are detected from SQLite database."""
    from opencode_agent_hub.sessions import get_sessions_from_db

    # Create test session in database
    conn = sqlite3.connect(str(test_db))
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ses_sqlite_test",
            "sqlite-test",
            "proj_sqlite",
            "/tmp/sqlite-test",
            "SQLite Test",
            "1.0.0",
            now_ms,
            now_ms,
            None,
        ),
    )
    conn.commit()
    conn.close()

    with mock.patch("opencode_agent_hub.sessions.OPENCODE_DB_PATH", test_db):
        sessions = get_sessions_from_db()

    assert sessions is not None
    assert len(sessions) == 1
    assert sessions[0]["id"] == "ses_sqlite_test"
    assert sessions[0]["directory"] == "/tmp/sqlite-test"

    print("✅ Sessions detected from SQLite")


def test_orient_session_idempotent():
    """Verify calling orient_session twice on same session only injects once."""
    from opencode_agent_hub.config import ORIENTED_SESSIONS
    from opencode_agent_hub.sessions import orient_session

    ORIENTED_SESSIONS.clear()

    injection_calls = []

    def mock_inject(session_id: str, text: str) -> None:
        injection_calls.append((session_id, text))

    agents = {}
    session = {
        "id": "ses_idempotent",
        "directory": "/tmp/test",
        "time": {"created": int(time.time() * 1000)},
    }

    with (
        mock.patch("opencode_agent_hub.messaging.inject_message", side_effect=mock_inject),
        mock.patch("opencode_agent_hub.sessions.get_session_agent", return_value="gpt"),
        mock.patch("opencode_agent_hub.persistence.save_oriented_sessions"),
        mock.patch("opencode_agent_hub.config.COORDINATOR_SESSION_ID", None),
    ):
        # First call - should inject
        result1 = orient_session("ses_idempotent", "/tmp/test", agents, session=session)

        # Second call - should skip
        result2 = orient_session("ses_idempotent", "/tmp/test", agents, session=session)

    # First call succeeds, second is skipped
    assert result1 is True
    assert result2 is False

    # Only one injection total
    assert len(injection_calls) == 1, f"Expected 1 injection, got {len(injection_calls)}"

    # Session is in ORIENTED_SESSIONS
    assert "ses_idempotent" in ORIENTED_SESSIONS

    print("✅ orient_session is idempotent")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        # Setup database
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
        conn.commit()
        conn.close()

        print("\n🧪 Running integration tests...\n")

        # Run tests
        test_daemon_orients_new_session_once(db_path, Path(tmpdir))
        test_orient_session_skips_already_oriented()
        test_orient_session_thread_safe()
        test_session_detection_from_sqlite(db_path)
        test_orient_session_idempotent()

        print("\n✅ All integration tests passed!")
