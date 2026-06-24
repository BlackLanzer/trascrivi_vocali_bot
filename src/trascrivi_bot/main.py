"""Entry point: build the Application, register handlers and start polling."""

from __future__ import annotations

import logging
from contextlib import suppress

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import Settings, get_settings, load_admins
from .db import Database
from .handlers import callbacks, commands, media
from .services import SERVICES_KEY, Services, get_services_from_bot_data
from .transcription.factory import get_transcriber

logger = logging.getLogger(__name__)

# Commands shown when typing "/" — base set for every user.
_USER_COMMANDS = [
    BotCommand("start", "Avvia il bot / richiedi accesso"),
    BotCommand("help", "Mostra i comandi disponibili"),
    BotCommand("settings", "Modello, lingua e streaming"),
    BotCommand("model", "Mostra o cambia il modello"),
    BotCommand("language", "Mostra o cambia la lingua"),
    BotCommand("streaming", "Attiva/disattiva lo streaming"),
    BotCommand("status", "Mostra stato e impostazioni"),
]

# Extra commands shown only to admins (in their private chat with the bot).
_ADMIN_COMMANDS = _USER_COMMANDS + [
    BotCommand("requests", "Richieste di accesso in attesa"),
    BotCommand("approve", "Approva un utente: /approve <id>"),
    BotCommand("deny", "Rifiuta un utente: /deny <id>"),
    BotCommand("users", "Elenco utenti approvati"),
    BotCommand("revoke", "Revoca l'accesso: /revoke <id>"),
    BotCommand("debug", "Attiva/disattiva il report di costo stimato"),
]


async def _post_init(application: Application) -> None:
    """Register the command list with Telegram so it appears under "/".

    Base commands go to the default scope (all users); the full admin set is
    scoped to each admin's private chat. Per-admin calls are best-effort: they
    fail harmlessly if an admin has never opened a chat with the bot.
    """

    await application.bot.set_my_commands(
        _USER_COMMANDS, scope=BotCommandScopeDefault()
    )
    admins = get_services_from_bot_data(application.bot_data).admins
    for admin_id in admins:
        with suppress(TelegramError):
            await application.bot.set_my_commands(
                _ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )


def build_application(settings: Settings) -> Application:
    """Assemble the bot: services, handlers and the Application instance."""

    db = Database(
        settings.db_path,
        default_provider=settings.transcription_provider,
        default_model=settings.default_model,
        default_language=settings.default_language,
        default_streaming=settings.default_streaming,
    )
    admins = load_admins(settings.admins_file)
    transcriber = get_transcriber(settings)
    services = Services(
        settings=settings, db=db, admins=admins, transcriber=transcriber
    )

    if not admins:
        logger.warning(
            "No admins configured (%s is empty or missing); "
            "access requests cannot be approved until you add one.",
            settings.admins_file,
        )

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )
    application.bot_data[SERVICES_KEY] = services

    # Commands.
    application.add_handler(CommandHandler("start", commands.start))
    application.add_handler(CommandHandler("help", commands.help_command))
    application.add_handler(CommandHandler("settings", commands.settings_command))
    application.add_handler(CommandHandler("model", commands.model_command))
    application.add_handler(CommandHandler("language", commands.language_command))
    application.add_handler(CommandHandler("streaming", commands.streaming_command))
    application.add_handler(CommandHandler("status", commands.status_command))
    application.add_handler(CommandHandler("requests", commands.requests_command))
    application.add_handler(CommandHandler("approve", commands.approve_command))
    application.add_handler(CommandHandler("deny", commands.deny_command))
    application.add_handler(CommandHandler("users", commands.users_command))
    application.add_handler(CommandHandler("revoke", commands.revoke_command))
    application.add_handler(CommandHandler("debug", commands.debug_command))

    # Inline-keyboard callbacks.
    application.add_handler(
        CallbackQueryHandler(callbacks.settings_callback, pattern=r"^(menu|set):")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.access_callback, pattern=r"^req:")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.stop_streaming_callback, pattern=r"^stream:")
    )

    # The core flow: voice notes and audio files.
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, media.handle_media)
    )

    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    settings = get_settings()

    if settings.run_mode != "polling":
        raise SystemExit(
            f"Unsupported RUN_MODE {settings.run_mode!r}; only 'polling' is implemented."
        )

    application = build_application(settings)
    logger.info("Bot starting in polling mode")
    # run_polling() is blocking and manages its own event loop.
    application.run_polling()


if __name__ == "__main__":
    main()
