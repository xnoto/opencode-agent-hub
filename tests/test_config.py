"""Tests for configuration file support."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock


def test_get_config_value_env_precedence() -> None:
    """Verify environment variables take precedence over config file."""
    from opencode_agent_hub.config import _get_config_value

    config: dict[str, Any] = {"opencode_port": 5000}

    # Env var should override config
    with mock.patch.dict(os.environ, {"OPENCODE_PORT": "6000"}):
        value = _get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, config, int)
        assert value == 6000


def test_get_config_value_config_file() -> None:
    """Verify config file values are used when env var not set."""
    from opencode_agent_hub.config import _get_config_value

    config: dict[str, Any] = {"opencode_port": 5000}

    # Clear env var to ensure config file is used
    with mock.patch.dict(os.environ, {}, clear=True):
        # Remove OPENCODE_PORT if it exists
        os.environ.pop("OPENCODE_PORT", None)
        value = _get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, config, int)
        assert value == 5000


def test_get_config_value_default() -> None:
    """Verify default is used when neither env var nor config file has value."""
    from opencode_agent_hub.config import _get_config_value

    config: dict[str, Any] = {}

    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("OPENCODE_PORT", None)
        value = _get_config_value("OPENCODE_PORT", ["opencode_port"], 4096, config, int)
        assert value == 4096


def test_get_config_value_nested_path() -> None:
    """Verify nested config paths work correctly."""
    from opencode_agent_hub.config import _get_config_value

    config: dict[str, Any] = {
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


def test_get_config_value_bool_coercion() -> None:
    """Verify boolean string coercion works for env vars."""
    from opencode_agent_hub.config import _get_config_value

    # Test env var bool coercion
    for true_val in ["true", "True", "TRUE", "1", "yes", "YES"]:
        with mock.patch.dict(os.environ, {"TEST_BOOL": true_val}):
            value = _get_config_value("TEST_BOOL", ["test"], False, {}, bool)
            assert value is True, f"Failed for '{true_val}'"

    for false_val in ["false", "False", "0", "no", ""]:
        with mock.patch.dict(os.environ, {"TEST_BOOL": false_val}):
            value = _get_config_value("TEST_BOOL", ["test"], True, {}, bool)
            assert value is False, f"Failed for '{false_val}'"


def test_get_config_value_int_coercion() -> None:
    """Verify integer coercion works for env vars and config file values."""
    from opencode_agent_hub.config import _get_config_value

    # From env var - string "42" should be coerced to int 42
    with mock.patch.dict(os.environ, {"TEST_INT": "42"}):
        value = _get_config_value("TEST_INT", ["test"], 0, {}, int)
        assert value == 42
        assert isinstance(value, int)

    # From config file (string) - returns default because implementation
    # doesn't coerce strings from config, only validates types
    config: dict[str, Any] = {"test": "99"}
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_INT", None)
        value = _get_config_value("TEST_INT", ["test"], 0, config, int)
        # Implementation doesn't coerce string "99" to int, returns default
        assert value == 0

    # From config file (int) - works correctly
    config = {"test": 77}
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("TEST_INT", None)
        value = _get_config_value("TEST_INT", ["test"], 0, config, int)
        assert value == 77


def test_get_config_value_missing_nested_key() -> None:
    """Verify missing nested keys return default."""
    from opencode_agent_hub.config import _get_config_value

    config: dict[str, Any] = {"rate_limit": {}}  # Missing 'enabled' key

    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ.pop("AGENT_HUB_RATE_LIMIT", None)
        value = _get_config_value(
            "AGENT_HUB_RATE_LIMIT", ["rate_limit", "enabled"], False, config, bool
        )
        assert value is False


def test_load_config_file_not_exists() -> None:
    """Verify _load_config_file returns empty dict when file doesn't exist."""

    # Mock CONFIG_FILE to a non-existent path using patch.object
    from opencode_agent_hub import config as config_module

    original_config_file = config_module.CONFIG_FILE
    try:
        config_module.CONFIG_FILE = Path("/nonexistent/config.json")
        result = config_module._load_config_file()
        assert result == {}
    finally:
        config_module.CONFIG_FILE = original_config_file


def test_load_config_file_valid() -> None:
    """Verify _load_config_file loads valid JSON."""
    from opencode_agent_hub import config as config_module

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"opencode_port": 5000, "log_level": "DEBUG"}, f)
        f.flush()

        original_config_file = config_module.CONFIG_FILE
        try:
            config_module.CONFIG_FILE = Path(f.name)
            result = config_module._load_config_file()
            assert result == {"opencode_port": 5000, "log_level": "DEBUG"}
        finally:
            config_module.CONFIG_FILE = original_config_file

    # Cleanup
    os.unlink(f.name)


def test_load_config_file_invalid_json() -> None:
    """Verify _load_config_file returns empty dict for invalid JSON."""
    from opencode_agent_hub import config as config_module

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("not valid json {{{")
        f.flush()

        original_config_file = config_module.CONFIG_FILE
        try:
            config_module.CONFIG_FILE = Path(f.name)
            result = config_module._load_config_file()
            assert result == {}
        finally:
            config_module.CONFIG_FILE = original_config_file

    # Cleanup
    os.unlink(f.name)
