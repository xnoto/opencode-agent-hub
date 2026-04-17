"""Data models for the agent hub daemon.

This module contains pure data classes and task definitions used for
inter-thread communication and message queuing.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator


class MessageType(StrEnum):
    """Valid message types."""

    MESSAGE = "message"
    COMPLETION = "completion"
    DELIVERY_STATUS = "delivery-status"
    TASK = "task"
    QUESTION = "question"
    CONTEXT = "context"
    ERROR = "error"


class MessagePriority(StrEnum):
    """Valid message priorities."""

    NORMAL = "normal"
    URGENT = "urgent"


class MessageSchema(BaseModel):
    """Pydantic model for validating hub messages.

    Validates required fields, types, and enum values.
    Extra fields are allowed (messages may carry arbitrary metadata).
    """

    model_config = {"extra": "allow"}

    from_: str
    to: str
    content: str
    type: MessageType = MessageType.MESSAGE
    priority: MessagePriority = MessagePriority.NORMAL
    threadId: str | None = None
    timestamp: float | None = None

    @field_validator("from_", "to", "content", mode="before")
    @classmethod
    def must_be_non_empty_string(cls, v: Any, info: Any) -> str:
        if not isinstance(v, str):
            field_name = "from" if info.field_name == "from_" else info.field_name
            raise ValueError(f"Field '{field_name}' must be str, got {type(v).__name__}")
        if not v.strip():
            field_name = "from" if info.field_name == "from_" else info.field_name
            raise ValueError(f"Missing or empty required field: '{field_name}'")
        return v


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
