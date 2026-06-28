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
    debug: bool
    summary_model: str


@dataclass(slots=True)
class GroupRecord:
    """A row of the ``groups`` table: a group the bot was added to and its status.

    Once a group is ``approved`` by an admin, voice notes from any member are
    transcribed. The chat id is negative for Telegram groups/supergroups.
    """

    chat_id: int
    title: str | None
    status: AccessStatus
    requested_at: str | None = None
    decided_at: str | None = None
    decided_by: int | None = None


@dataclass(slots=True)
class CachedTranscript:
    """A cached transcription (a row of the ``transcripts`` table).

    Keyed by (``file_unique_id``, ``model``, ``language``): re-sending the same
    audio with the same settings reuses the result instead of re-billing.
    """

    id: int
    file_unique_id: str
    model: str
    language: str
    text: str
    audio_path: str | None = None
    duration: float | None = None
    request_id: str | None = None
    created_at: str | None = None
