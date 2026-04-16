"""Tests for agent ID generation functionality."""

from opencode_agent_hub.sessions import generate_agent_id_for_session


def test_generate_agent_id_for_session_deterministic() -> None:
    """Same session ID should generate same agent ID."""
    session = {"id": "ses_abc123", "slug": "test-slug", "directory": "/tmp/test"}

    id1 = generate_agent_id_for_session(session)
    id2 = generate_agent_id_for_session(session)

    assert id1 == id2
    assert "-" in id1


def test_generate_agent_id_for_session_different_sessions() -> None:
    """Different session IDs should generate different agent IDs."""
    session1 = {"id": "ses_abc123", "slug": "test", "directory": "/tmp/test1"}
    session2 = {"id": "ses_def456", "slug": "test", "directory": "/tmp/test2"}

    id1 = generate_agent_id_for_session(session1)
    id2 = generate_agent_id_for_session(session2)

    assert id1 != id2


def test_generate_agent_id_for_session_without_id() -> None:
    """Session without ID should fallback to random generation."""
    session = {"id": "", "slug": "test", "directory": "/tmp/test"}

    agent_id = generate_agent_id_for_session(session)

    assert "-" in agent_id
    # Should contain suffix
    parts = agent_id.split("-")
    assert len(parts) >= 2


def test_generate_agent_id_for_session_empty_slug() -> None:
    """Session with empty slug but valid ID should use ID-based generation."""
    session = {"id": "ses_xyz789", "slug": "", "directory": "/tmp/test"}

    agent_id = generate_agent_id_for_session(session)

    # Should be deterministic based on ID
    agent_id2 = generate_agent_id_for_session(session)
    assert agent_id == agent_id2
    assert "-" in agent_id
