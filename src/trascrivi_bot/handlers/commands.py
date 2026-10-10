"""Command handlers: /start, /help, /settings, /status and admin commands."""

from __future__ import annotations

import logging
from contextlib import suppress

from telegram import Update, User
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from ..keyboards import (
    access_request_keyboard,
    describe_group,
    describe_user,
    group_request_keyboard,
    language_label,
    main_settings_keyboard,
    model_label,
)
from ..models import AccessStatus
from ..security import (
    is_admin,
    is_authorized,
    is_group_chat,
    register_group_request,
    require_admin,
    require_auth,
    settings_key,
)
from ..services import Services, get_services
from ..transcription.base import LanguageOption, ModelOption

# Chat-member statuses that mean the bot is present in a chat.
_PRESENT_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
}

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
    logger.info("access request filed user=%s username=%s", user.id, user.username)
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
        "/model – mostra o cambia il modello",
        "/language – mostra o cambia la lingua",
        "/streaming – attiva/disattiva lo streaming",
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
            "/groups – gruppi e abilitazione",
            "/debug – attiva/disattiva il report di costo stimato",
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
    key = settings_key(update) or user.id
    user_settings = services.db.get_settings(key)
    await message.reply_text(
        "Impostazioni di trascrizione:",
        reply_markup=main_settings_keyboard(
            user_settings, services.transcriber, services.summarizer
        ),
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
    key = settings_key(update) or user.id
    user_settings = services.db.get_settings(key)
    role = "amministratore" if is_admin(context, user.id) else "utente autorizzato"
    context_label = (
        "gruppo" if is_group_chat(update.effective_chat) else "chat privata"
    )
    streaming = "attivo" if user_settings.streaming else "disattivo"
    await message.reply_text(
        f"Stato: {role}\n"
        f"Contesto: {context_label}\n"
        f"Provider: {user_settings.provider}\n"
        f"Modello: {model_label(transcriber, user_settings.model)}\n"
        f"Lingua: {language_label(transcriber, user_settings.language)}\n"
        f"Streaming: {streaming}"
    )


@require_auth
async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or set the transcription model (mirrors the /settings menu)."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    services = get_services(context)
    key = settings_key(update) or user.id
    options = services.transcriber.available_models()
    user_settings = services.db.get_settings(key)

    if not context.args:
        await message.reply_text(
            f"Modello attuale: *{model_label(services.transcriber, user_settings.model)}*\n\n"
            "Per cambiarlo: `/model <valore>`\n\n"
            "Valori disponibili:\n" + _format_options(options),
            parse_mode="Markdown",
        )
        return

    value = context.args[0]
    if not any(option.value == value for option in options):
        await message.reply_text(
            f"Modello non valido: `{escape_markdown(value, version=1)}`\n\n"
            "Valori disponibili:\n" + _format_options(options),
            parse_mode="Markdown",
        )
        return

    services.db.update_setting(key, "model", value)
    await message.reply_text(
        f"Modello impostato su *{model_label(services.transcriber, value)}*.",
        parse_mode="Markdown",
    )


@require_auth
async def language_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show or set the transcription language (mirrors the /settings menu)."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    services = get_services(context)
    key = settings_key(update) or user.id
    options = services.transcriber.available_languages()
    user_settings = services.db.get_settings(key)

    if not context.args:
        await message.reply_text(
            f"Lingua attuale: *{language_label(services.transcriber, user_settings.language)}*\n\n"
            "Per cambiarla: `/language <codice>`\n\n"
            "Codici disponibili:\n" + _format_options(options),
            parse_mode="Markdown",
        )
        return

    value = context.args[0]
    if not any(option.value == value for option in options):
        await message.reply_text(
            f"Lingua non valida: `{escape_markdown(value, version=1)}`\n\n"
            "Codici disponibili:\n" + _format_options(options),
            parse_mode="Markdown",
        )
        return

    services.db.update_setting(key, "language", value)
    await message.reply_text(
        f"Lingua impostata su *{language_label(services.transcriber, value)}*.",
        parse_mode="Markdown",
    )


_STREAMING_ON = {"on", "si", "sì", "true", "1"}
_STREAMING_OFF = {"off", "no", "false", "0"}


@require_auth
async def streaming_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Toggle or set live streaming (mirrors the /settings menu)."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    services = get_services(context)
    key = settings_key(update) or user.id
    user_settings = services.db.get_settings(key)

    if not context.args:
        new_value = not user_settings.streaming
    else:
        arg = context.args[0].lower()
        if arg in _STREAMING_ON:
            new_value = True
        elif arg in _STREAMING_OFF:
            new_value = False
        else:
            await message.reply_text(
                "Uso: `/streaming` per invertire, oppure "
                "`/streaming on` / `/streaming off`.",
                parse_mode="Markdown",
            )
            return

    services.db.update_setting(key, "streaming", new_value)
    state = "attivo" if new_value else "disattivo"
    await message.reply_text(f"Streaming {state}.")


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
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle the admin's debug mode (per-transcription cost estimate report)."""

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    services = get_services(context)
    current = services.db.get_settings(user.id).debug
    new_state = not current
    services.db.update_setting(user.id, "debug", new_state)

    if new_state:
        await message.reply_text(
            "🔧 Modalità debug attivata. Dopo ogni trascrizione riceverai "
            "un report con il costo stimato della richiesta."
        )
    else:
        await message.reply_text("🔧 Modalità debug disattivata.")


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
    lines += [
        f"• {escape_markdown(describe_user(record), version=1)}"
        for record in approved
    ]
    await message.reply_text("\n".join(lines), parse_mode="Markdown")


@require_admin
async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List known groups; pending ones get approve/deny buttons."""

    message = update.effective_message
    if message is None:
        return

    services = get_services(context)
    groups = services.db.list_groups()
    if not groups:
        await message.reply_text("Nessun gruppo registrato.")
        return

    approved = [g for g in groups if g.status is AccessStatus.APPROVED]
    pending = [g for g in groups if g.status is AccessStatus.PENDING]

    if approved:
        lines = ["*Gruppi abilitati*"]
        lines += [
            f"• {escape_markdown(describe_group(g), version=1)}" for g in approved
        ]
        await message.reply_text("\n".join(lines), parse_mode="Markdown")

    if pending:
        await message.reply_text(f"Gruppi in attesa: {len(pending)}")
        for group in pending:
            await message.reply_text(
                f"👥 {describe_group(group)}",
                reply_markup=group_request_keyboard(group.chat_id),
            )
    elif not approved:
        await message.reply_text("Nessun gruppo in attesa. ✅")


async def on_my_chat_member(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """When the bot is added to a group, file a pending request for it."""

    member_update = update.my_chat_member
    if member_update is None:
        return
    chat = member_update.chat
    if not is_group_chat(chat):
        return
    if member_update.new_chat_member.status in _PRESENT_STATUSES:
        await register_group_request(context, chat)


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
    logger.info(
        "access decision target=%s status=%s revoke=%s by_admin=%s",
        target_id,
        status.value,
        revoke,
        admin.id,
    )

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


def _format_options(options: list[ModelOption] | list[LanguageOption]) -> str:
    """Render an option list, one per line, as "`value` – label"."""

    return "\n".join(f"`{option.value}` – {escape_markdown(option.label, version=1)}" for option in options)


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
