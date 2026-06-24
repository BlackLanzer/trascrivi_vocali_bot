"""Command handlers: /start, /help, /settings, /status and admin commands."""

from __future__ import annotations

import logging
from contextlib import suppress

from telegram import Update, User
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..keyboards import (
    access_request_keyboard,
    describe_user,
    language_label,
    main_settings_keyboard,
    model_label,
)
from ..models import AccessStatus
from ..security import is_admin, is_authorized, require_admin, require_auth
from ..services import Services, get_services

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet authorized users; otherwise file or report an access request."""

    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    services = get_services(context)

    if is_authorized(context, user.id):
        await message.reply_text(
            "Ciao! 👋 Inviami un messaggio vocale o un file audio e te lo trascrivo.\n\n"
            "Usa /settings per scegliere modello, lingua e streaming.\n"
            "Usa /help per l'elenco dei comandi."
        )
        return

    record = services.db.get_access(user.id)
    if record is not None and record.status is AccessStatus.PENDING:
        await message.reply_text(
            "Hai già una richiesta di accesso in attesa di approvazione. ⏳"
        )
        return
    if record is not None and record.status is AccessStatus.DENIED:
        await message.reply_text(
            "La tua richiesta di accesso è stata rifiutata. "
            "Contatta un amministratore se ritieni sia un errore."
        )
        return

    services.db.create_request(user.id, user.username, user.first_name)
    await message.reply_text(
        "Richiesta di accesso inviata. ✅\n"
        "Un amministratore la esaminerà al più presto."
    )
    await _notify_admins_new_request(context, services, user)


@require_auth
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List available commands (admins see the admin section too)."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    lines = [
        "*Comandi disponibili*",
        "/start – avvia il bot",
        "/help – mostra questo messaggio",
        "/settings – modello, lingua e streaming",
        "/status – mostra le tue impostazioni e il tuo stato",
        "",
        "Inviami un messaggio vocale o un file audio per trascriverlo.",
    ]
    if is_admin(context, user.id):
        lines += [
            "",
            "*Comandi amministratore*",
            "/requests – richieste di accesso in attesa",
            "/approve <id> – approva un utente",
            "/deny <id> – rifiuta un utente",
            "/users – elenco utenti approvati",
            "/revoke <id> – revoca l'accesso a un utente",
        ]
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


@require_auth
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the interactive settings menu."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    services = get_services(context)
    user_settings = services.db.get_settings(user.id)
    await message.reply_text(
        "Impostazioni di trascrizione:",
        reply_markup=main_settings_keyboard(user_settings, services.transcriber),
    )


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's access status and current settings."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    services = get_services(context)
    transcriber = services.transcriber
    user_settings = services.db.get_settings(user.id)
    role = "amministratore" if is_admin(context, user.id) else "utente autorizzato"
    streaming = "attivo" if user_settings.streaming else "disattivo"
    await message.reply_text(
        f"Stato: {role}\n"
        f"Provider: {user_settings.provider}\n"
        f"Modello: {model_label(transcriber, user_settings.model)}\n"
        f"Lingua: {language_label(transcriber, user_settings.language)}\n"
        f"Streaming: {streaming}"
    )


# -- admin commands -------------------------------------------------------


@require_admin
async def requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List pending access requests, each with approve/deny buttons."""

    message = update.effective_message
    if message is None:
        return

    services = get_services(context)
    pending = services.db.list_by_status(AccessStatus.PENDING)
    if not pending:
        await message.reply_text("Nessuna richiesta di accesso in attesa. ✅")
        return

    await message.reply_text(f"Richieste in attesa: {len(pending)}")
    for record in pending:
        await message.reply_text(
            f"👤 {describe_user(record)}",
            reply_markup=access_request_keyboard(record.user_id),
        )


@require_admin
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _decide_via_command(update, context, AccessStatus.APPROVED)


@require_admin
async def deny_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _decide_via_command(update, context, AccessStatus.DENIED)


@require_admin
async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _decide_via_command(update, context, AccessStatus.DENIED, revoke=True)


@require_admin
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List approved users."""

    message = update.effective_message
    if message is None:
        return

    services = get_services(context)
    approved = services.db.list_by_status(AccessStatus.APPROVED)
    if not approved:
        await message.reply_text("Nessun utente approvato.")
        return
    lines = ["*Utenti approvati*"]
    lines += [f"• {describe_user(record)}" for record in approved]
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _decide_via_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: AccessStatus,
    *,
    revoke: bool = False,
) -> None:
    """Shared logic for /approve, /deny and /revoke."""

    message = update.effective_message
    admin = update.effective_user
    if message is None or admin is None:
        return

    if not context.args:
        await message.reply_text("Uso: indica l'id utente, es. /approve 123456789")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await message.reply_text("L'id utente deve essere un numero.")
        return

    services = get_services(context)
    record = services.db.get_access(target_id)
    if record is None:
        await message.reply_text("Nessun utente con questo id.")
        return

    services.db.set_status(target_id, status, decided_by=admin.id)

    if revoke:
        admin_text = f"Accesso revocato a {describe_user(record)}."
        user_text = "Il tuo accesso al bot è stato revocato."
    elif status is AccessStatus.APPROVED:
        admin_text = f"Utente approvato: {describe_user(record)}."
        user_text = "La tua richiesta di accesso è stata approvata! 🎉 Invia /start."
    else:
        admin_text = f"Utente rifiutato: {describe_user(record)}."
        user_text = "La tua richiesta di accesso è stata rifiutata."

    await message.reply_text(admin_text)
    await _notify_user(context, target_id, user_text)


# -- helpers --------------------------------------------------------------


async def _notify_admins_new_request(
    context: ContextTypes.DEFAULT_TYPE, services: Services, user: User
) -> None:
    """Ping every admin about a new access request (best effort)."""

    handle = f"@{user.username}" if user.username else (user.first_name or "—")
    text = f"🔔 Nuova richiesta di accesso da {handle} (id {user.id})."
    for admin_id in services.admins:
        with suppress(TelegramError):
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=access_request_keyboard(user.id),
            )


async def _notify_user(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str
) -> None:
    """Tell a user about an access decision (best effort)."""

    with suppress(TelegramError):
        await context.bot.send_message(chat_id=user_id, text=text)
