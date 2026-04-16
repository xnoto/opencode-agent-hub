"""Data models for the agent hub daemon.

This module contains pure data classes and task definitions used for
inter-thread communication and message queuing.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class InjectionTask:
    """Task for injecting a message into a session."""

    session_id: str
    text: str
    # Optional metadata for delivery feedback on failure
    original_sender: str | None = None
    original_message_id: str | None = None
    thread_id: str | None = None
    target_agent: str | None = None


@dataclass
class MessageTask:
    """Task for processing a message file."""

    path: Path


@dataclass
class SessionTask:
    """Task for processing a session file."""

    path: Path


class PreflightError(Exception):
    """Raised when preflight checks fail."""

    pass
