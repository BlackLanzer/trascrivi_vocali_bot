"""The core flow: receive a voice/audio message and reply with its transcript."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..security import require_auth
from ..services import get_services
from ..transcription.base import Transcriber, TranscriptionConfig

logger = logging.getLogger(__name__)

# Telegram limits.
MAX_MESSAGE_LENGTH = 4096
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API download cap.

# Live streaming: don't edit the placeholder more than once per second, and give
# the whole streaming operation a generous upper bound.
_EDIT_INTERVAL = 1.0
_STREAM_TIMEOUT = 300.0

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

    # 1) Immediate silent acknowledgement (no notification).
    placeholder = await message.reply_text(_RECEIVED_TEXT, disable_notification=True)

    services = get_services(context)
    transcriber = services.transcriber
    user_settings = services.db.get_settings(user.id)
    config = TranscriptionConfig(
        model=user_settings.model, language=user_settings.language
    )
    mimetype = media.mime_type

    # 2) Download the audio bytes.
    try:
        tg_file = await context.bot.get_file(media.file_id)
        audio_bytes = bytes(await tg_file.download_as_bytearray())
    except TelegramError:
        logger.exception("Failed to download media from Telegram")
        await _finish_with_error(placeholder)
        return

    # 3) Transcribe (streaming when enabled and supported, else pre-recorded).
    use_streaming = user_settings.streaming and transcriber.supports_streaming(mimetype)
    try:
        if use_streaming:
            final_text = await asyncio.wait_for(
                _run_streaming(placeholder, audio_bytes, config, transcriber, mimetype),
                timeout=_STREAM_TIMEOUT,
            )
        else:
            result = await transcriber.transcribe_file(audio_bytes, config, mimetype)
            final_text = result.text
    except Exception:  # noqa: BLE001 - never let a transcription error crash the bot
        logger.exception("Transcription failed")
        await _finish_with_error(placeholder)
        return

    final_text = final_text.strip() or _EMPTY_TEXT

    # 4) Mark the placeholder done (silent edit) and send the transcript as a
    #    new message *with* a notification so the user gets pinged.
    with suppress(TelegramError):
        await placeholder.edit_text(_DONE_TEXT)
    await _send_transcript(message, final_text)


async def _run_streaming(
    placeholder: Message,
    audio: bytes,
    config: TranscriptionConfig,
    transcriber: Transcriber,
    mimetype: str | None,
) -> str:
    """Edit the placeholder live as interim transcripts arrive; return the final text.

    Deepgram sends growing interim results per segment, then a final result that
    settles the segment. We keep the settled parts and append the current interim
    for display, throttling edits to respect Telegram's rate limits.
    """

    final_parts: list[str] = []
    interim = ""
    last_shown = ""
    last_edit = 0.0
    loop = asyncio.get_event_loop()

    async for event in transcriber.transcribe_stream(audio, config, mimetype):
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
                await placeholder.edit_text(shown[:MAX_MESSAGE_LENGTH])
            last_shown = shown
            last_edit = now

    return " ".join(final_parts).strip()


async def _send_transcript(message: Message, text: str) -> None:
    """Send the transcript as new message(s) with a notification.

    Splits into <=4096-char chunks (Telegram's limit) when necessary.
    """

    for chunk in _split_text(text, MAX_MESSAGE_LENGTH):
        await message.reply_text(chunk)


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
