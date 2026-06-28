"""Application configuration loaded from environment / ``.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Every field is populated from an environment variable of the same name in
    upper case (e.g. ``telegram_bot_token`` <- ``TELEGRAM_BOT_TOKEN``), or from a
    ``.env`` file in the working directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets (required).
    telegram_bot_token: str
    deepgram_api_key: str

    # Transcription provider selection.
    transcription_provider: str = "deepgram"

    # How the bot receives updates. Only "polling" is implemented; the field
    # leaves the door open for a future "webhook" mode.
    run_mode: str = "polling"

    # Where state lives. Both are mounted as volumes in the Docker deployment.
    admins_file: Path = Path("config/admins.txt")
    db_path: Path = Path("data/bot.db")

    # Defaults applied to a user the first time they are seen.
    default_model: str = "nova-3"
    default_language: str = "it"
    default_streaming: bool = False

    # Per-minute rate used only for the admin /debug cost estimate. The Deepgram
    # API does not return a price, so this is a configurable estimate. Acts as a
    # fallback when the selected model has no per-model price.
    deepgram_price_per_minute: float = 0.0043

    # Logging. Logs go both to stdout (for ``docker compose logs``) and to a
    # rotating file under ``log_dir``.
    log_level: str = "INFO"
    log_dir: Path = Path("data/logs")
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 5


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once and cached."""

    # The required fields come from the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]


def load_admins(path: Path) -> set[int]:
    """Read admin Telegram user IDs from ``path`` (one integer per line).

    Blank lines and lines starting with ``#`` are ignored. A missing file simply
    yields an empty set (the bot starts with no admins).
    """

    admins: set[int] = set()
    if not path.exists():
        return admins

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            admins.add(int(line))
        except ValueError:
            # Skip malformed entries rather than crashing the whole bot.
            continue

    return admins
