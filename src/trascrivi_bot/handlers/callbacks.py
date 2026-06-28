"""Inline-keyboard callback handlers: settings menu and access decisions."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..keyboards import (
    describe_group,
    describe_user,
    language_keyboard,
    main_settings_keyboard,
    model_keyboard,
    summary_model_keyboard,
)
from ..handlers.media import ACTIVE_STREAMS_KEY
from ..models import AccessStatus
from ..security import is_admin, is_update_authorized, settings_key
from ..services import get_services

logger = logging.getLogger(__name__)

# Telegram's per-message character limit.
_MAX_MESSAGE_LENGTH = 4096


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the settings menu (``menu:*`` and ``set:*`` callback data)."""

    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or query.data is None:
        return
    await query.answer()

    if not is_update_authorized(context, update):
        await query.edit_message_text("Non sei autorizzato.")
        return

    services = get_services(context)
    db = services.db
    transcriber = services.transcriber
    key = settings_key(update)
    if key is None:
        return
    data = query.data

    if data == "menu:close":
        with suppress(TelegramError):
            await query.edit_message_text("Impostazioni salvate. ✅")
        return

    if data == "menu:model":
        user_settings = db.get_settings(key)
        await query.edit_message_text(
            "Scegli il modello:",
            reply_markup=model_keyboard(user_settings, transcriber),
        )
        return

    if data == "menu:lang":
        user_settings = db.get_settings(key)
        await query.edit_message_text(
            "Scegli la lingua:",
            reply_markup=language_keyboard(user_settings, transcriber),
        )
        return

    if data == "menu:summodel" and services.summarizer is not None:
        user_settings = db.get_settings(key)
        await query.edit_message_text(
            "Scegli il modello per il riassunto:",
            reply_markup=summary_model_keyboard(user_settings, services.summarizer),
        )
        return

    if data == "menu:main":
        await _show_main_menu(query, services, key)
        return

    # data is "set:<field>:<value>"
    parts = data.split(":", 2)
    if len(parts) == 3 and parts[0] == "set":
        _, field, value = parts
        if field == "model":
            db.update_setting(key, "model", value)
        elif field == "lang":
            db.update_setting(key, "language", value)
        elif field == "stream":
            db.update_setting(key, "streaming", value == "1")
        elif field == "summodel":
            db.update_setting(key, "summary_model", value)
        await _show_main_menu(query, services, key)


async def access_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle approve/deny buttons (``req:approve:<id>`` / ``req:deny:<id>``)."""

    query = update.callback_query
    admin = update.effective_user
    if query is None or admin is None or query.data is None:
        return
    await query.answer()

    if not is_admin(context, admin.id):
        await query.edit_message_text("Solo gli amministratori possono decidere.")
        return

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, action, raw_id = parts
    try:
        target_id = int(raw_id)
    except ValueError:
        return

    services = get_services(context)
    record = services.db.get_access(target_id)
    if record is None:
        await query.edit_message_text("Richiesta non più disponibile.")
        return

    if action == "approve":
        services.db.set_status(target_id, AccessStatus.APPROVED, decided_by=admin.id)
        await query.edit_message_text(f"✅ Approvato: {describe_user(record)}")
        user_text = "La tua richiesta di accesso è stata approvata! 🎉 Invia /start."
    elif action == "deny":
        services.db.set_status(target_id, AccessStatus.DENIED, decided_by=admin.id)
        await query.edit_message_text(f"❌ Rifiutato: {describe_user(record)}")
        user_text = "La tua richiesta di accesso è stata rifiutata."
    else:
        return

    logger.info(
        "access decision (callback) target=%s action=%s by_admin=%s",
        target_id,
        action,
        admin.id,
    )

    with suppress(TelegramError):
        await context.bot.send_message(chat_id=target_id, text=user_text)


async def group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle group approve/deny buttons (``grp:approve:<id>`` / ``grp:deny:<id>``)."""

    query = update.callback_query
    admin = update.effective_user
    if query is None or admin is None or query.data is None:
        return
    await query.answer()

    if not is_admin(context, admin.id):
        await query.edit_message_text("Solo gli amministratori possono decidere.")
        return

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, action, raw_id = parts
    try:
        chat_id = int(raw_id)
    except ValueError:
        return

    services = get_services(context)
    group = services.db.get_group(chat_id)
    if group is None:
        await query.edit_message_text("Gruppo non più disponibile.")
        return

    if action == "approve":
        services.db.set_group_status(chat_id, AccessStatus.APPROVED, admin.id)
        await query.edit_message_text(f"✅ Gruppo abilitato: {describe_group(group)}")
        group_text = (
            "✅ Questo gruppo è stato abilitato: invierò la trascrizione dei "
            "messaggi vocali."
        )
    elif action == "deny":
        services.db.set_group_status(chat_id, AccessStatus.DENIED, admin.id)
        await query.edit_message_text(f"❌ Gruppo rifiutato: {describe_group(group)}")
        group_text = ""
    else:
        return

    logger.info(
        "group decision chat=%s action=%s by_admin=%s", chat_id, action, admin.id
    )
    if group_text:
        with suppress(TelegramError):
            await context.bot.send_message(chat_id=chat_id, text=group_text)


async def stop_streaming_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the "stop streaming" button (``stream:stop`` callback data).

    Looks up the active-stream Event for this placeholder message and sets it so
    ``_run_streaming`` breaks out cooperatively. Robust if the stream already
    finished or the entry is missing — we always acknowledge the tap.
    """

    query = update.callback_query
    if query is None:
        return

    message = query.message
    if isinstance(message, Message):
        key = (message.chat_id, message.message_id)
        streams: dict[tuple[int, int], asyncio.Event] = context.bot_data.get(
            ACTIVE_STREAMS_KEY, {}
        )
        stop_event = streams.get(key)
        if stop_event is not None:
            stop_event.set()

    await query.answer("Interrompo…")


async def summarize_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Summarize a cached transcript on demand (``sum:<transcript_id>``)."""

    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or query.data is None:
        return
    await query.answer()

    message = query.message
    if not isinstance(message, Message) or not is_update_authorized(context, update):
        return

    services = get_services(context)
    summarizer = services.summarizer
    if summarizer is None:
        with suppress(TelegramError):
            await message.reply_text("Riassunto non disponibile.")
        return

    try:
        transcript_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    cached = services.db.get_transcript(transcript_id)
    if cached is None:
        with suppress(TelegramError):
            await message.reply_text(
                "Trascrizione non più disponibile per il riassunto."
            )
        return

    placeholder = await message.reply_text("📝 Riassumo…", disable_notification=True)
    key = settings_key(update)
    summary_model = services.db.get_settings(
        key if key is not None else user.id
    ).summary_model
    try:
        summary = await summarizer.summarize(
            cached.text, summary_model, cached.language
        )
    except Exception:  # noqa: BLE001 - never let a summary error crash the bot
        logger.exception("Summarization failed")
        with suppress(TelegramError):
            await placeholder.edit_text(
                "⚠️ Errore durante il riassunto. Riprova."
            )
        return

    summary = summary.strip() or "(riassunto vuoto)"
    with suppress(TelegramError):
        await placeholder.edit_text(f"📝 Riassunto:\n\n{summary}"[:_MAX_MESSAGE_LENGTH])


async def _show_main_menu(query, services, user_id: int) -> None:  # type: ignore[no-untyped-def]
    """Refresh the message to show the top-level settings menu."""

    user_settings = services.db.get_settings(user_id)
    with suppress(TelegramError):
        await query.edit_message_text(
            "Impostazioni di trascrizione:",
            reply_markup=main_settings_keyboard(
                user_settings, services.transcriber, services.summarizer
            ),
        )
