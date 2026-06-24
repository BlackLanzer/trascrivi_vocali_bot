"""Access-control helpers and handler guards."""

from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from .models import AccessStatus
from .services import get_services

# A plain message-handler coroutine (matches python-telegram-bot's expectation).
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]


def is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if ``user_id`` is listed in the admins file."""

    return user_id in get_services(context).admins


def is_authorized(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """True if the user may use the bot (admin, or access status ``approved``)."""

    if is_admin(context, user_id):
        return True
    record = get_services(context).db.get_access(user_id)
    return record is not None and record.status is AccessStatus.APPROVED


def require_auth(func: Handler) -> Handler:
    """Decorator: only run ``func`` for authorized users.

    Unauthorized users get a short Italian message that depends on their state
    (unknown, pending, denied) instead of being silently ignored.
    """

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or update.effective_message is None:
            return
        if is_authorized(context, user.id):
            await func(update, context)
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
        await update.effective_message.reply_text(text)

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
