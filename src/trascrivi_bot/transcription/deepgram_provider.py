"""Deepgram implementation of the :class:`Transcriber` interface.

Verified against ``deepgram-sdk==7.3.1``:

* Pre-recorded: ``await client.listen.v1.media.transcribe_file(request=<bytes>, ...)``
  returning a ``ListenV1Response`` whose transcript lives at
  ``results.channels[0].alternatives[0].transcript``.
* Streaming: ``async with client.listen.v1.connect(...) as socket`` yields an
  ``AsyncV1SocketClient``; ``socket.send_media(bytes)`` feeds audio and iterating
  the socket yields ``ListenV1Results`` messages with ``is_final`` flags. The live
  endpoint accepts ``encoding="ogg-opus"``, so Telegram voice notes (OGG/Opus) can
  be streamed as-is — no ffmpeg/transcoding required.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import suppress

from deepgram import AsyncDeepgramClient
from deepgram.listen.v1.types.listen_v1metadata import ListenV1Metadata
from deepgram.listen.v1.types.listen_v1results import ListenV1Results

from .base import (
    LanguageOption,
    ModelOption,
    StreamEvent,
    Transcriber,
    TranscriptionConfig,
    TranscriptResult,
)

logger = logging.getLogger(__name__)

# Telegram voice notes are OGG/Opus; the live socket accepts this container.
_OGG_MIMETYPES = frozenset({"audio/ogg", "audio/opus", "audio/ogg; codecs=opus"})

# How the audio is fed to the live socket: send a chunk, pause briefly. This
# gives Deepgram a steady flow so interim results arrive progressively, while
# staying much faster than real time.
_STREAM_CHUNK_SIZE = 8192
_STREAM_CHUNK_PAUSE = 0.05


class DeepgramTranscriber(Transcriber):
    """Speech-to-text backed by Deepgram's hosted API."""

    name = "deepgram"

    def __init__(self, api_key: str) -> None:
        self._client = AsyncDeepgramClient(api_key=api_key)

    # -- pre-recorded -----------------------------------------------------

    async def transcribe_file(
        self, audio: bytes, config: TranscriptionConfig, mimetype: str | None = None
    ) -> TranscriptResult:
        response = await self._client.listen.v1.media.transcribe_file(
            request=audio,
            model=config.model,
            language=config.language,
            smart_format=config.smart_format,
        )

        transcript = ""
        confidence: float | None = None
        results = getattr(response, "results", None)
        channels = getattr(results, "channels", None) if results else None
        if channels:
            alternatives = channels[0].alternatives
            if alternatives:
                transcript = alternatives[0].transcript or ""
                confidence = alternatives[0].confidence

        # Metadata carries the billed audio duration (seconds) and the request
        # id; both may be absent, so guard like the transcript extraction above.
        duration: float | None = None
        request_id: str | None = None
        metadata = getattr(response, "metadata", None)
        if metadata is not None:
            duration = getattr(metadata, "duration", None)
            request_id = getattr(metadata, "request_id", None)

        return TranscriptResult(
            text=transcript.strip(),
            confidence=confidence,
            duration=duration,
            request_id=request_id,
        )

    # -- streaming --------------------------------------------------------

    def supports_streaming(self, mimetype: str | None) -> bool:
        # Only OGG/Opus (Telegram voice notes) can be streamed without
        # transcoding; everything else falls back to the pre-recorded path.
        return mimetype in _OGG_MIMETYPES

    async def transcribe_stream(  # type: ignore[override]
        self, audio: bytes, config: TranscriptionConfig, mimetype: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        async with self._client.listen.v1.connect(
            model=config.model,
            language=config.language,
            encoding="ogg-opus",
            interim_results=True,
            smart_format=config.smart_format,
        ) as socket:
            sender = asyncio.create_task(self._feed_audio(socket, audio))
            try:
                async for message in socket:
                    if isinstance(message, ListenV1Results):
                        event = _results_to_event(message)
                        if event is not None:
                            yield event
                    elif isinstance(message, ListenV1Metadata):
                        # Metadata is emitted once the stream is fully processed.
                        break
            finally:
                sender.cancel()
                with suppress(asyncio.CancelledError):
                    await sender

    async def _feed_audio(self, socket: object, audio: bytes) -> None:
        """Send the audio in paced chunks, then signal end-of-stream."""

        try:
            for start in range(0, len(audio), _STREAM_CHUNK_SIZE):
                await socket.send_media(audio[start : start + _STREAM_CHUNK_SIZE])  # type: ignore[attr-defined]
                await asyncio.sleep(_STREAM_CHUNK_PAUSE)
            await socket.send_finalize()  # type: ignore[attr-defined]
            await socket.send_close_stream()  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - network/transport errors
            logger.exception("Error while streaming audio to Deepgram")

    # -- settings menu options -------------------------------------------

    def available_models(self) -> list[ModelOption]:
        # Prices are Deepgram's published pay-as-you-go per-minute rates (USD);
        # verify against deepgram.com/pricing as they may change.
        return [
            ModelOption("nova-3", "Nova 3 (consigliato)", price_per_minute=0.0043),
            ModelOption("nova-2", "Nova 2", price_per_minute=0.0043),
            ModelOption("enhanced", "Enhanced", price_per_minute=0.0145),
        ]

    def available_languages(self) -> list[LanguageOption]:
        return [
            LanguageOption("it", "Italiano"),
            LanguageOption("en", "English"),
            LanguageOption("es", "Español"),
            LanguageOption("fr", "Français"),
            LanguageOption("de", "Deutsch"),
            LanguageOption("multi", "Multilingua"),
        ]


def _results_to_event(message: ListenV1Results) -> StreamEvent | None:
    """Convert a live ``ListenV1Results`` message into a :class:`StreamEvent`."""

    channel = message.channel
    alternatives = channel.alternatives if channel else None
    text = (alternatives[0].transcript or "") if alternatives else ""
    is_final = bool(message.is_final)
    if not text and not is_final:
        return None
    return StreamEvent(text=text.strip(), is_final=is_final)
