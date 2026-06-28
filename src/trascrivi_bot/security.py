"""Access-control helpers and handler guards."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Any

from telegram import Chat, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from .keyboards import group_request_keyboard
from .models import AccessStatus
from .services import Services, get_services

logger = logging.getLogger(__name__)

# A plain message-handler coroutine (matches python-telegram-bot's expectation).
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

# Chat types in which access is granted per-group rather than per-user.
_GROUP_CHAT_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


def is_group_chat(chat: Chat | None) -> bool:
    """True if ``chat`` is a Telegram group or supergroup."""

    return chat is not None and chat.type in _GROUP_CHAT_TYPES


def settings_key(update: Update) -> int | None:
    """The id whose settings apply: the group's in a group, else the user's.

    Group chats share one settings row (keyed by the negative chat id) so the
    model/language/streaming choices are common to all members.
    """

    chat = update.effective_chat
    if is_group_chat(chat):
        return chat.id  # type: ignore[union-attr]
    user = update.effective_user
    return user.id if user is not None else None


def is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if ``user_id`` is listed in the admins file."""

    return user_id in get_services(context).admins


def is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if the user may use the bot (admin, or access status ``approved``)."""

    if is_admin(context, user_id):
        return True
    record = get_services(context).db.get_access(user_id)
    return record is not None and record.status is AccessStatus.APPROVED


def is_update_authorized(
    context: ContextTypes.DEFAULT_TYPE, update: Update
) -> bool:
    """Chat-aware authorization for an update.

    Admins are always allowed. In a group, access depends on the *group* being
    approved (any member may then use the bot). In a private chat, it depends on
    the user being approved.
    """

    user = update.effective_user
    if user is None:
        return False
    if is_admin(context, user.id):
        return True
    if is_group_chat(update.effective_chat):
        group = get_services(context).db.get_group(update.effective_chat.id)  # type: ignore[union-attr]
        return group is not None and group.status is AccessStatus.APPROVED
    return is_authorized(context, user.id)


async def notify_admins_new_group(
    context: ContextTypes.DEFAULT_TYPE, services: Services, chat: Chat
) -> None:
    """Ping every admin about a new group awaiting approval (best effort)."""

    title = chat.title or "—"
    text = (
        f"🔔 Il bot è stato aggiunto al gruppo «{title}» (id {chat.id}).\n"
        "Vuoi abilitare la trascrizione per questo gruppo?"
    )
    for admin_id in services.admins:
        with suppress(TelegramError):
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=group_request_keyboard(chat.id),
            )


async def register_group_request(
    context: ContextTypes.DEFAULT_TYPE, chat: Chat
) -> None:
    """File a pending request for ``chat`` and notify admins, once."""

    services = get_services(context)
    if services.db.add_pending_group(chat.id, chat.title):
        logger.info("group request filed chat=%s title=%s", chat.id, chat.title)
        await notify_admins_new_group(context, services, chat)


def require_auth(func: Handler) -> Handler:
    """Decorator: only run ``func`` for authorized users.

    Unauthorized users get a short Italian message that depends on their state
    (unknown, pending, denied) instead of being silently ignored.
    """

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        if is_update_authorized(context, update):
            await func(update, context)
            return

        chat = update.effective_chat
        if is_group_chat(chat):
            assert chat is not None  # narrowed by is_group_chat
            group = get_services(context).db.get_group(chat.id)
            if group is None:
                await register_group_request(context, chat)
                await message.reply_text(
                    "Questo gruppo non è ancora abilitato. "
                    "Ho inviato una richiesta agli amministratori. ⏳"
                )
            elif group.status is AccessStatus.PENDING:
                await message.reply_text(
                    "Questo gruppo è in attesa di approvazione. ⏳"
                )
            # Denied groups get no reply, to avoid noise in the chat.
            return

        record = get_services(context).db.get_access(user.id)
        if record is None:
            text = (
                "Non sei ancora autorizzato a usare questo bot.\n"
                "Invia /start per richiedere l'accesso."
            )
        elif record.status is AccessStatus.PENDING:
            text = "La tua richiesta di accesso è in attesa di approvazione. ⏳"
        else:  # DENIED
            text = "La tua richiesta di accesso è stata rifiutata."
        await message.reply_text(text)

    return wrapper


def require_admin(func: Handler) -> Handler:
    """Decorator: only run ``func`` for admins; others get a refusal."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or update.effective_message is None:
            return
        if not is_admin(context, user.id):
            await update.effective_message.reply_text(
                "Questo comando è riservato agli amministratori."
            )
            return
        await func(update, context)

    return wrapper
