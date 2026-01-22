"""Placeholder tests for opencode-agent-hub."""


def test_import():
    """Verify the package can be imported."""
    import opencode_agent_hub
    from importlib.metadata import version

    assert opencode_agent_hub.__version__ == version("opencode-agent-hub")


def test_daemon_import():
    """Verify daemon module can be imported."""
    from opencode_agent_hub import daemon

    assert hasattr(daemon, "main")


def test_watch_import():
    """Verify watch module can be imported."""
    from opencode_agent_hub import watch

    assert hasattr(watch, "main")
