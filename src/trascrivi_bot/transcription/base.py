"""Abstractions every transcription provider must implement.

Adding a new provider means writing one subclass of :class:`Transcriber` and
registering it in ``factory.py`` — nothing else in the bot needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass


@dataclass(slots=True)
class TranscriptionConfig:
    """The knobs a user can set, passed to the provider on every request."""

    model: str
    language: str
    smart_format: bool = True


@dataclass(slots=True)
class TranscriptResult:
    """The outcome of a one-shot (pre-recorded) transcription."""

    text: str
    confidence: float | None = None
    duration: float | None = None
    request_id: str | None = None


@dataclass(slots=True)
class StreamEvent:
    """One update emitted while streaming.

    ``text`` is the best transcript so far for the current segment; ``is_final``
    marks it as settled (won't be revised).
    """

    text: str
    is_final: bool


@dataclass(slots=True)
class ModelOption:
    """A selectable model, with a human-friendly label for the settings menu.

    ``price_per_minute`` is the provider's published per-minute rate (USD), shown
    in the menu and used for the admin cost estimate; ``None`` when unknown.
    """

    value: str
    label: str
    price_per_minute: float | None = None


@dataclass(slots=True)
class LanguageOption:
    """A selectable language, with a human-friendly label."""

    value: str
    label: str


class Transcriber(ABC):
    """Common interface for all speech-to-text providers."""

    name: str

    @abstractmethod
    async def transcribe_file(
        self, audio: bytes, config: TranscriptionConfig, mimetype: str | None = None
    ) -> TranscriptResult:
        """Transcribe a complete audio file and return the final text."""

    def supports_streaming(self, mimetype: str | None) -> bool:
        """Whether this provider can stream audio of the given mimetype."""

        return False

    def transcribe_stream(
        self, audio: bytes, config: TranscriptionConfig, mimetype: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream interim/final transcripts for a complete audio file.

        Implementations are async generators. The default raises, so providers
        that don't support streaming need not implement it.
        """

        raise NotImplementedError(f"{self.name} does not support streaming")

    @abstractmethod
    def available_models(self) -> list[ModelOption]:
        """Models offered in the settings menu (provider-specific)."""

    def price_for(self, model: str) -> float | None:
        """Per-minute price (USD) of ``model``, or ``None`` if unknown."""

        for option in self.available_models():
            if option.value == model:
                return option.price_per_minute
        return None

    @abstractmethod
    def available_languages(self) -> list[LanguageOption]:
        """Languages offered in the settings menu (provider-specific)."""
