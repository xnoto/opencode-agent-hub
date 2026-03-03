"""Placeholder tests for opencode-agent-hub."""


def test_import() -> None:
    """Verify the package can be imported."""
    from importlib.metadata import version

    import opencode_agent_hub

    assert opencode_agent_hub.__version__ == version("opencode-agent-hub")


def test_daemon_import() -> None:
    """Verify daemon module can be imported."""
    from opencode_agent_hub import daemon

    assert hasattr(daemon, "main")


def test_watch_import() -> None:
    """Verify watch module can be imported."""
    from opencode_agent_hub import watch

    assert hasattr(watch, "main")
