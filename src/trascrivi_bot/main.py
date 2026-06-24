"""Entry point: build the Application, register handlers and start polling."""

from __future__ import annotations

import logging

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
from .services import SERVICES_KEY, Services
from .transcription.factory import get_transcriber

logger = logging.getLogger(__name__)


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

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data[SERVICES_KEY] = services

    # Commands.
    application.add_handler(CommandHandler("start", commands.start))
    application.add_handler(CommandHandler("help", commands.help_command))
    application.add_handler(CommandHandler("settings", commands.settings_command))
    application.add_handler(CommandHandler("status", commands.status_command))
    application.add_handler(CommandHandler("requests", commands.requests_command))
    application.add_handler(CommandHandler("approve", commands.approve_command))
    application.add_handler(CommandHandler("deny", commands.deny_command))
    application.add_handler(CommandHandler("users", commands.users_command))
    application.add_handler(CommandHandler("revoke", commands.revoke_command))

    # Inline-keyboard callbacks.
    application.add_handler(
        CallbackQueryHandler(callbacks.settings_callback, pattern=r"^(menu|set):")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.access_callback, pattern=r"^req:")
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
