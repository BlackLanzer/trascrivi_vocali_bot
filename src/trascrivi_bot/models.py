"""Domain models and enums shared across the bot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccessStatus(str, Enum):
    """Lifecycle of a user's access to the bot."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(slots=True)
class AccessRecord:
    """A row of the ``access`` table: who asked for access and its outcome."""

    user_id: int
    username: str | None
    first_name: str | None
    status: AccessStatus
    requested_at: str | None = None
    decided_at: str | None = None
    decided_by: int | None = None


@dataclass(slots=True)
class UserSettings:
    """Per-user transcription preferences (a row of the ``settings`` table)."""

    user_id: int
    provider: str
    model: str
    language: str
    streaming: bool
