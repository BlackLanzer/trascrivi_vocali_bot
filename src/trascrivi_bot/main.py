"""Entry point: build the Application, register handlers and start polling."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import Settings, get_settings, load_admins
from .db import Database
from .handlers import callbacks, commands, media
from .services import SERVICES_KEY, Services, get_services_from_bot_data
from .summarization import get_summarizer
from .transcription.factory import get_transcriber

logger = logging.getLogger(__name__)

# Handle (in ``bot_data``) for the background cache-cleanup task.
_CLEANUP_TASK_KEY = "cache_cleanup_task"

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
    BotCommand("groups", "Gruppi e abilitazione"),
    BotCommand("debug", "Attiva/disattiva il report di costo stimato"),
]


def _configure_logging(settings: Settings) -> None:
    """Send logs to stdout and to a rotating file under ``settings.log_dir``.

    The stdout handler keeps ``docker compose logs`` useful; the rotating file
    handler keeps history across restarts. Level comes from ``LOG_LEVEL``.
    """

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_dir / "bot.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root.handlers = [stream_handler, file_handler]


def _cleanup_expired_cache(
    db: Database, cache_dir: Path, retention_days: int
) -> None:
    """Remove cached transcripts (and their audio) older than the retention."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = db.delete_transcripts_older_than(cutoff.isoformat())
    for raw_path in removed:
        with suppress(OSError):
            Path(raw_path).unlink(missing_ok=True)

    # Sweep orphan audio files (e.g. left by a crash before the DB row landed).
    orphans = 0
    if cache_dir.exists():
        cutoff_ts = cutoff.timestamp()
        for entry in cache_dir.iterdir():
            with suppress(OSError):
                if entry.is_file() and entry.stat().st_mtime < cutoff_ts:
                    entry.unlink()
                    orphans += 1

    logger.info(
        "cache cleanup: removed %d expired transcripts, %d orphan files",
        len(removed),
        orphans,
    )


async def _cache_cleanup_loop(application: Application) -> None:
    """Periodically prune the cache for the lifetime of the application."""

    services = get_services_from_bot_data(application.bot_data)
    settings = services.settings
    interval = max(1, settings.cache_cleanup_interval_hours) * 3600
    while True:
        with suppress(Exception):
            _cleanup_expired_cache(
                services.db, settings.cache_dir, settings.cache_retention_days
            )
        await asyncio.sleep(interval)


async def _post_init(application: Application) -> None:
    """Register the command list with Telegram and start the cleanup task.

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

    # APScheduler isn't installed, so application.job_queue is None; run the
    # periodic cache cleanup as a plain background task instead.
    application.bot_data[_CLEANUP_TASK_KEY] = asyncio.create_task(
        _cache_cleanup_loop(application)
    )


async def _post_shutdown(application: Application) -> None:
    """Cancel the background cache-cleanup task on shutdown."""

    task = application.bot_data.get(_CLEANUP_TASK_KEY)
    if isinstance(task, asyncio.Task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def build_application(settings: Settings) -> Application:
    """Assemble the bot: services, handlers and the Application instance."""

    db = Database(
        settings.db_path,
        default_provider=settings.transcription_provider,
        default_model=settings.default_model,
        default_language=settings.default_language,
        default_streaming=settings.default_streaming,
        default_summary_model=settings.default_summary_model,
    )
    admins = load_admins(settings.admins_file)
    transcriber = get_transcriber(settings)
    summarizer = get_summarizer(settings)
    services = Services(
        settings=settings,
        db=db,
        admins=admins,
        transcriber=transcriber,
        summarizer=summarizer,
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
        .post_shutdown(_post_shutdown)
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
    application.add_handler(CommandHandler("groups", commands.groups_command))
    application.add_handler(CommandHandler("debug", commands.debug_command))

    # Inline-keyboard callbacks.
    application.add_handler(
        CallbackQueryHandler(callbacks.settings_callback, pattern=r"^(menu|set):")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.access_callback, pattern=r"^req:")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.group_callback, pattern=r"^grp:")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.stop_streaming_callback, pattern=r"^stream:")
    )
    application.add_handler(
        CallbackQueryHandler(callbacks.summarize_callback, pattern=r"^sum:")
    )

    # Detect being added to / removed from groups.
    application.add_handler(
        ChatMemberHandler(commands.on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    # The core flow: voice notes and audio files.
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, media.handle_media)
    )

    return application


def main() -> None:
    settings = get_settings()
    _configure_logging(settings)

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
