"""Provider selection — the single place new providers are registered."""

from __future__ import annotations

from ..config import Settings
from .base import Transcriber
from .deepgram_provider import DeepgramTranscriber


def get_transcriber(settings: Settings) -> Transcriber:
    """Instantiate the transcription provider named in ``settings``.

    To add a provider, implement :class:`Transcriber` and add a branch here.
    """

    provider = settings.transcription_provider.lower()
    if provider == "deepgram":
        return DeepgramTranscriber(api_key=settings.deepgram_api_key)
    raise ValueError(f"Unknown transcription provider: {settings.transcription_provider!r}")
