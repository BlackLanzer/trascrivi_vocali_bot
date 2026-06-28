"""The core flow: receive a voice/audio message and reply with its transcript."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..keyboards import stop_streaming_keyboard, summarize_keyboard
from ..security import is_admin, require_auth
from ..services import get_services
from ..transcription.base import Transcriber, TranscriptionConfig

logger = logging.getLogger(__name__)

# Telegram limits.
MAX_MESSAGE_LENGTH = 4096
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API download cap.

# Registry (in ``context.bot_data``) of in-flight streaming transcriptions,
# keyed by (chat_id, placeholder.message_id). The "stop" button looks up the
# matching Event and sets it so ``_run_streaming`` can break out cooperatively.
ACTIVE_STREAMS_KEY = "active_streams"


def _active_streams(
    context: ContextTypes.DEFAULT_TYPE,
) -> dict[tuple[int, int], asyncio.Event]:
    """Return the active-streams registry, lazily creating it in ``bot_data``."""

    streams = context.bot_data.get(ACTIVE_STREAMS_KEY)
    if streams is None:
        streams = {}
        context.bot_data[ACTIVE_STREAMS_KEY] = streams
    return streams

# Live streaming: don't edit the placeholder more than once per second, and give
# the whole streaming operation a generous upper bound.
_EDIT_INTERVAL = 1.0
_STREAM_TIMEOUT = 300.0

# Map common audio mimetypes to a file extension for the on-disk cache.
_MIME_EXTENSIONS = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/ogg; codecs=opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _audio_extension(mimetype: str | None) -> str:
    """Best-effort file extension for a cached audio file."""

    return _MIME_EXTENSIONS.get(mimetype or "", ".bin")


_RECEIVED_TEXT = "🎙️ Messaggio ricevuto, trascrivo…"
_DONE_TEXT = "✅ Trascrizione pronta 👇"
_ERROR_TEXT = "⚠️ Si è verificato un errore durante la trascrizione. Riprova."
_EMPTY_TEXT = "(nessun parlato rilevato)"


@require_auth
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    media = message.voice or message.audio
    if media is None:
        return

    if media.file_size and media.file_size > MAX_DOWNLOAD_BYTES:
        await message.reply_text("Il file è troppo grande (massimo 20 MB).")
        return

    services = get_services(context)
    transcriber = services.transcriber
    db = services.db
    user_settings = db.get_settings(user.id)
    config = TranscriptionConfig(
        model=user_settings.model, language=user_settings.language
    )
    mimetype = media.mime_type

    # A cache hit short-circuits the whole pipeline: no download, no Deepgram
    # call, no streaming. Look it up before deciding the placeholder layout.
    cached = db.get_cached_transcript(
        media.file_unique_id, user_settings.model, user_settings.language
    )
    use_streaming = (
        cached is None
        and user_settings.streaming
        and transcriber.supports_streaming(mimetype)
    )

    # 1) Immediate silent acknowledgement (no notification). Streaming gets a
    #    "stop" button; the pre-recorded path stays button-less.
    placeholder = await message.reply_text(
        _RECEIVED_TEXT,
        disable_notification=True,
        reply_markup=stop_streaming_keyboard() if use_streaming else None,
    )

    # Deepgram metadata is only available on the pre-recorded path; default to
    # None so the debug report can fall back to the Telegram media duration.
    audio_duration: float | None = None
    request_id: str | None = None
    cache_hit = cached is not None
    started = time.monotonic()

    if cached is not None:
        final_text = cached.text
        audio_duration = cached.duration
        request_id = cached.request_id
        transcript_id = cached.id
    else:
        # 2) Download the audio bytes and cache them on disk.
        try:
            tg_file = await context.bot.get_file(media.file_id)
            audio_bytes = bytes(await tg_file.download_as_bytearray())
        except TelegramError:
            logger.exception("Failed to download media from Telegram")
            await _finish_with_error(placeholder)
            return

        audio_path = _save_audio(
            services.settings.cache_dir, media.file_unique_id, mimetype, audio_bytes
        )

        # 3) Transcribe (streaming when enabled and supported, else pre-recorded).
        try:
            if use_streaming:
                stop_event = asyncio.Event()
                stream_key = (placeholder.chat_id, placeholder.message_id)
                _active_streams(context)[stream_key] = stop_event
                try:
                    final_text = await asyncio.wait_for(
                        _run_streaming(
                            placeholder,
                            audio_bytes,
                            config,
                            transcriber,
                            mimetype,
                            stop_event,
                        ),
                        timeout=_STREAM_TIMEOUT,
                    )
                finally:
                    _active_streams(context).pop(stream_key, None)
            else:
                result = await transcriber.transcribe_file(
                    audio_bytes, config, mimetype
                )
                final_text = result.text
                audio_duration = result.duration
                request_id = result.request_id
        except Exception:  # noqa: BLE001 - never let an error crash the bot
            logger.exception("Transcription failed")
            await _finish_with_error(placeholder)
            return

        # Cache the result for future identical requests.
        transcript_id = db.store_transcript(
            file_unique_id=media.file_unique_id,
            model=user_settings.model,
            language=user_settings.language,
            text=final_text.strip() or _EMPTY_TEXT,
            audio_path=str(audio_path) if audio_path is not None else None,
            duration=audio_duration,
            request_id=request_id,
        )

    elapsed = time.monotonic() - started

    final_text = final_text.strip() or _EMPTY_TEXT

    # 4) Mark the placeholder done (silent edit) and send the transcript as a
    #    new message *with* a notification so the user gets pinged. When a
    #    summarizer is configured, attach a "summarize" button to the last chunk.
    with suppress(TelegramError):
        await placeholder.edit_text(_DONE_TEXT)
    summarize_markup = (
        summarize_keyboard(transcript_id)
        if services.summarizer is not None
        else None
    )
    await _send_transcript(message, final_text, summarize_markup)

    # 5) Estimate cost (used for both the structured server log and the admin
    #    debug report). Prefer Deepgram's reported duration (pre-recorded);
    #    otherwise fall back to the Telegram media duration, always present.
    tg_duration = media.duration
    fallback = (
        tg_duration.total_seconds()
        if isinstance(tg_duration, timedelta)
        else float(tg_duration)
    )
    duration = audio_duration if audio_duration is not None else fallback
    model_rate = transcriber.price_for(user_settings.model)
    rate = (
        model_rate
        if model_rate is not None
        else services.settings.deepgram_price_per_minute
    )
    cost = (duration / 60.0) * rate

    # Structured per-request log line for server-side observability.
    chat = update.effective_chat
    chat_kind = chat.type if chat is not None else "?"
    chat_id = chat.id if chat is not None else "?"
    mode = "cache" if cache_hit else ("streaming" if use_streaming else "pre-recorded")
    logger.info(
        "transcription user=%s chat=%s/%s file=%s model=%s lang=%s mode=%s "
        "cache=%s duration=%.0fs elapsed=%.1fs cost=$%.4f request_id=%s",
        user.id,
        chat_kind,
        chat_id,
        media.file_unique_id,
        user_settings.model,
        user_settings.language,
        mode,
        "hit" if cache_hit else "miss",
        duration,
        elapsed,
        cost,
        request_id,
    )

    # 6) Admin debug report: an ESTIMATED request cost, sent only to admins who
    #    enabled debug mode via /debug.
    if user_settings.debug and is_admin(context, user.id):
        lines = [
            "🔧 Debug",
            f"Modello: {user_settings.model} | Lingua: {user_settings.language}",
            f"Modalità: {'streaming' if use_streaming else 'pre-registrato'}",
            f"Durata audio: {duration:.0f}s",
            f"Elaborazione: {elapsed:.1f}s",
            f"Costo stimato: ${cost:.4f} (tariffa ${rate:.4f}/min)",
        ]
        if request_id is not None:
            lines.append(f"request_id: {request_id}")
        with suppress(TelegramError):
            await message.reply_text("\n".join(lines))


async def _run_streaming(
    placeholder: Message,
    audio: bytes,
    config: TranscriptionConfig,
    transcriber: Transcriber,
    mimetype: str | None,
    stop_event: asyncio.Event,
) -> str:
    """Edit the placeholder live as interim transcripts arrive; return the final text.

    Deepgram sends growing interim results per segment, then a final result that
    settles the segment. We keep the settled parts and append the current interim
    for display, throttling edits to respect Telegram's rate limits.

    ``stop_event`` lets the user stop early via the inline button: we check it at
    the top of each loop and break, then close the generator so the underlying
    Deepgram socket shuts down promptly, returning whatever was collected so far.
    """

    final_parts: list[str] = []
    interim = ""
    last_shown = ""
    last_edit = 0.0
    loop = asyncio.get_event_loop()

    agen = transcriber.transcribe_stream(audio, config, mimetype)
    try:
        async for event in agen:
            if stop_event.is_set():
                break

            if event.is_final:
                if event.text:
                    final_parts.append(event.text)
                interim = ""
            else:
                interim = event.text

            shown = " ".join([*final_parts, interim]).strip()
            now = loop.time()
            if shown and shown != last_shown and (now - last_edit) >= _EDIT_INTERVAL:
                with suppress(TelegramError):
                    await placeholder.edit_text(
                        shown[:MAX_MESSAGE_LENGTH],
                        reply_markup=stop_streaming_keyboard(),
                    )
                last_shown = shown
                last_edit = now
    finally:
        # Close the generator so the underlying ``async with ...connect()``
        # socket is torn down promptly when we stop iterating early.
        await agen.aclose()

    return " ".join(final_parts).strip()


async def _send_transcript(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send the transcript as new message(s) with a notification.

    Splits into <=4096-char chunks (Telegram's limit) when necessary. The
    optional ``reply_markup`` (the summarize button) goes on the last chunk only.
    """

    chunks = _split_text(text, MAX_MESSAGE_LENGTH)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await message.reply_text(chunk, reply_markup=markup)


def _save_audio(
    cache_dir: Path, file_unique_id: str, mimetype: str | None, audio: bytes
) -> Path | None:
    """Write the audio bytes to the on-disk cache; return the path (or None).

    Failures are non-fatal: the transcript is still cached in the DB even if the
    audio file can't be written.
    """

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{file_unique_id}{_audio_extension(mimetype)}"
        path.write_bytes(audio)
        return path
    except OSError:
        logger.exception("Failed to write cached audio file")
        return None


async def _finish_with_error(placeholder: Message) -> None:
    with suppress(TelegramError):
        await placeholder.edit_text(_ERROR_TEXT)


def _split_text(text: str, limit: int) -> list[str]:
    """Split ``text`` into chunks of at most ``limit`` characters.

    Prefers to break on newlines/spaces so words aren't cut mid-way.
    """

    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = max(window.rfind("\n"), window.rfind(" "))
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
