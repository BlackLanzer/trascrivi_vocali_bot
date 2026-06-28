"""Shared services wired at startup and reachable from every handler.

The single :class:`Services` instance is stored in ``application.bot_data`` so
handlers can fetch it (typed) via :func:`get_services` instead of poking at the
untyped ``bot_data`` dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .config import Settings
from .db import Database
from .summarization import Summarizer
from .transcription.base import Transcriber

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# Key under which the Services bundle is stored in bot_data.
SERVICES_KEY = "services"


@dataclass(slots=True)
class Services:
    """Everything a handler might need, assembled once in ``main``."""

    settings: Settings
    db: Database
    admins: set[int]
    transcriber: Transcriber
    summarizer: Summarizer | None = None


def get_services_from_bot_data(bot_data: dict[str, Any]) -> Services:
    """Retrieve the shared :class:`Services` bundle from a raw ``bot_data`` dict."""

    services = bot_data.get(SERVICES_KEY)
    if not isinstance(services, Services):  # pragma: no cover - wiring guard
        raise RuntimeError("Services not initialised in bot_data")
    return services


def get_services(context: "ContextTypes.DEFAULT_TYPE") -> Services:
    """Retrieve the shared :class:`Services` bundle from a handler context."""

    return get_services_from_bot_data(context.application.bot_data)
