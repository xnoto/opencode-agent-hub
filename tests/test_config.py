"""Tests for configuration file support."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock


def test_get_config_value_env_precedence():
    """Verify environment variables take precedence over config file."""
    from opencode_agent_hub.daemon import _get_config_value

    config = {"opencode_port": 5000}

    # Env var should override config
    with mock.patch.dict(os.environ, {"OPENCODE_PORT": "6000"}):
        value = _get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, config, int)
        assert value == 6000


def test_get_config_value_config_file():
    """Verify config file values are used when env var not set."""
    from opencode_agent_hub.daemon import _get_config_value

    config = {"opencode_port": 5000}

    # Clear env var to ensure config file is used
    with mock.patch.dict(os.environ, {}, clear=True):
        # Remove OPENCODE_PORT if it exists
        os.environ.pop("OPENCODE_PORT", None)
        value = _get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, config, int)
        assert value == 5000


def test_get_config_value_default():
    """Verify default is used when neither env var nor config file has value."""
    from opencode_agent_hub.daemon import _get_config_value

    config = {}

    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("OPENCODE_PORT", None)
        value = _get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, config, int)
        assert value == 4096


def test_get_config_value_nested_path():
    """Verify nested config paths work correctly."""
    from opencode_agent_hub.daemon import _get_config_value

    config = {
        "rate_limit": {
            "enabled": True,
            "max_messages": 20,
        }
    }

    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("AGENT_HUB_RATE_LIMIT", None)
        os.environ.pop("AGENT_HUB_RATE_LIMIT_MAX", None)

        enabled = _get_config_value(
            "AGENT_HUB_RATE_LIMIT", ["rate_limit", "enabled"], False, config, bool
        )
        assert enabled is True

        max_msgs = _get_config_value(
            "AGENT_HUB_RATE_LIMIT_MAX", ["rate_limit", "max_messages"], 10, config, int
        )
        assert max_msgs == 20


def test_get_config_value_bool_coercion():
    """Verify boolean string coercion works."""
    from opencode_agent_hub.daemon import _get_config_value

    # Test env var bool coercion
    for true_val in ["true", "True", "TRUE", "1", "yes", "YES"]:
        with mock.patch.dict(os.environ, {"TEST_BOOL": true_val}):
            value = _get_config_value("TEST_BOOL", ["test"], False, {}, bool)
            assert value is True, f"Failed for '{true_val}'"

    for false_val in ["false", "False", "0", "no", ""]:
        with mock.patch.dict(os.environ, {"TEST_BOOL": false_val}):
            value = _get_config_value("TEST_BOOL", ["test"], True, {}, bool)
            assert value is False, f"Failed for '{false_val}'"


def test_get_config_value_int_coercion():
    """Verify integer coercion works for string values."""
    from opencode_agent_hub.daemon import _get_config_value

    # From env var
    with mock.patch.dict(os.environ, {"TEST_INT": "42"}):
        value = _get_config_value("TEST_INT", ["test"], 0, {}, int)
        assert value == 42
        assert isinstance(value, int)

    # From config file (string)
    config = {"test": "99"}
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_INT", None)
        value = _get_config_value("TEST_INT", ["test"], 0, config, int)
        assert value == 99

    # From config file (int)
    config = {"test": 77}
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_INT", None)
        value = _get_config_value("TEST_INT", ["test"], 0, config, int)
        assert value == 77


def test_get_config_value_missing_nested_key():
    """Verify missing nested keys return default."""
    from opencode_agent_hub.daemon import _get_config_value

    config = {"rate_limit": {}}  # Missing 'enabled' key

    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("AGENT_HUB_RATE_LIMIT", None)
        value = _get_config_value(
            "AGENT_HUB_RATE_LIMIT", ["rate_limit", "enabled"], False, config, bool
        )
        assert value is False


def test_load_config_file_not_exists():
    """Verify _load_config_file returns empty dict when file doesn't exist."""

    # Mock CONFIG_FILE to a non-existent path
    with mock.patch("opencode_agent_hub.daemon.CONFIG_FILE", Path("/nonexistent/config.json")):
        # Re-import to test, but we can just call the function directly
        from opencode_agent_hub import daemon

        # Manually call with mocked path
        original = daemon.CONFIG_FILE
        daemon.CONFIG_FILE = Path("/nonexistent/config.json")
        result = daemon._load_config_file()
        daemon.CONFIG_FILE = original

        assert result == {}


def test_load_config_file_valid():
    """Verify _load_config_file loads valid JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"opencode_port": 5000, "log_level": "DEBUG"}, f)
        f.flush()

        from opencode_agent_hub import daemon

        original = daemon.CONFIG_FILE
        daemon.CONFIG_FILE = Path(f.name)
        result = daemon._load_config_file()
        daemon.CONFIG_FILE = original

        assert result == {"opencode_port": 5000, "log_level": "DEBUG"}

    # Cleanup
    os.unlink(f.name)


def test_load_config_file_invalid_json():
    """Verify _load_config_file returns empty dict for invalid JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("not valid json {{{")
        f.flush()

        from opencode_agent_hub import daemon

        original = daemon.CONFIG_FILE
        daemon.CONFIG_FILE = Path(f.name)
        result = daemon._load_config_file()
        daemon.CONFIG_FILE = original

        assert result == {}

    # Cleanup
    os.unlink(f.name)
