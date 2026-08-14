"""Utility functions for the agent hub daemon.

This module contains pure utility functions with no external dependencies
that are used throughout the codebase.
"""

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, indent: int | None = 2) -> None:
    """Write JSON to file atomically using temp file + rename.

    This prevents readers from seeing partial/empty files during writes.
    POSIX guarantees rename() is atomic - readers see old or new, never partial.

    Args:
        path: Target file path
        data: JSON-serializable data
        indent: JSON indentation (None for compact, 2 for pretty-print)

    Raises:
        OSError: If write fails
    """
    json_str = json.dumps(data, indent=indent)
    temp_path = path.with_suffix(f".tmp.{os.getpid()}")

    try:
        temp_path.write_text(json_str, encoding="utf-8")

        # Atomic rename - readers see either old or new, never partial
        temp_path.rename(path)
    except Exception:
        with suppress(OSError):
            temp_path.unlink()
        raise


def validate_path_within_dir(path: Path, allowed_dir: Path) -> Path:
    """Validate that a path is within an allowed directory.

    This prevents path traversal attacks where malicious filenames like
    '../../../etc/passwd' could be used to write files outside intended
    directories.

    Args:
        path: The path to validate
        allowed_dir: The directory that must contain the path

    Returns:
        The resolved path if valid

    Raises:
        ValueError: If the path is outside the allowed directory
    """
    # Resolve to absolute paths (handles .., symlinks, etc.)
    resolved_path = path.resolve()
    resolved_allowed = allowed_dir.resolve()

    # Check if path is within allowed directory
    try:
        resolved_path.relative_to(resolved_allowed)
    except ValueError as e:
        raise ValueError(f"Path traversal detected: {path} is not within {allowed_dir}") from e

    return resolved_path
