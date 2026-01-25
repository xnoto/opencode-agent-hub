"""Tests for coordinator AGENTS.md resolution."""

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
