"""Inline-keyboard callback handlers: settings menu and access decisions."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..keyboards import (
    describe_user,
    language_keyboard,
    main_settings_keyboard,
    model_keyboard,
)
from ..handlers.media import ACTIVE_STREAMS_KEY
from ..models import AccessStatus
from ..security import is_admin, is_authorized
from ..services import get_services

logger = logging.getLogger(__name__)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the settings menu (``menu:*`` and ``set:*`` callback data)."""

    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or query.data is None:
        return
    await query.answer()

    if not is_authorized(context, user.id):
        await query.edit_message_text("Non sei autorizzato.")
        return

    services = get_services(context)
    db = services.db
    transcriber = services.transcriber
    data = query.data

    if data == "menu:close":
        with suppress(TelegramError):
            await query.edit_message_text("Impostazioni salvate. ✅")
        return

    if data == "menu:model":
        user_settings = db.get_settings(user.id)
        await query.edit_message_text(
            "Scegli il modello:",
            reply_markup=model_keyboard(user_settings, transcriber),
        )
        return

    if data == "menu:lang":
        user_settings = db.get_settings(user.id)
        await query.edit_message_text(
            "Scegli la lingua:",
            reply_markup=language_keyboard(user_settings, transcriber),
        )
        return

    if data == "menu:main":
        await _show_main_menu(query, db, transcriber, user.id)
        return

    # data is "set:<field>:<value>"
    parts = data.split(":", 2)
    if len(parts) == 3 and parts[0] == "set":
        _, field, value = parts
        if field == "model":
            db.update_setting(user.id, "model", value)
        elif field == "lang":
            db.update_setting(user.id, "language", value)
        elif field == "stream":
            db.update_setting(user.id, "streaming", value == "1")
        await _show_main_menu(query, db, transcriber, user.id)


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


async def _show_main_menu(query, db, transcriber, user_id: int) -> None:  # type: ignore[no-untyped-def]
    """Refresh the message to show the top-level settings menu."""

    user_settings = db.get_settings(user_id)
    with suppress(TelegramError):
        await query.edit_message_text(
            "Impostazioni di trascrizione:",
            reply_markup=main_settings_keyboard(user_settings, transcriber),
        )
