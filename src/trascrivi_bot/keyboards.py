"""Inline-keyboard builders for the settings menu and access requests."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .models import AccessRecord, GroupRecord, UserSettings
from .summarization import Summarizer
from .transcription.base import Transcriber


def language_label(transcriber: Transcriber, value: str) -> str:
    """Human-friendly label for a language code, falling back to the code."""

    for option in transcriber.available_languages():
        if option.value == value:
            return option.label
    return value


def model_label(transcriber: Transcriber, value: str) -> str:
    """Human-friendly label for a model id, falling back to the id."""

    for option in transcriber.available_models():
        if option.value == value:
            return option.label
    return value


def main_settings_keyboard(
    settings: UserSettings,
    transcriber: Transcriber,
    summarizer: Summarizer | None = None,
) -> InlineKeyboardMarkup:
    """Top-level settings menu showing the current values.

    The summary-model row appears only when a summarizer is configured.
    """

    streaming_label = "🟢 Attivo" if settings.streaming else "⚪️ Disattivo"
    toggle_target = 0 if settings.streaming else 1
    rows = [
        [
            InlineKeyboardButton(
                f"🧠 Modello: {model_label(transcriber, settings.model)}",
                callback_data="menu:model",
            )
        ],
        [
            InlineKeyboardButton(
                f"🌐 Lingua: {language_label(transcriber, settings.language)}",
                callback_data="menu:lang",
            )
        ],
        [
            InlineKeyboardButton(
                f"🎙️ Streaming: {streaming_label}",
                callback_data=f"set:stream:{toggle_target}",
            )
        ],
    ]
    if summarizer is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📝 Riassunto: {summarizer.label_for(settings.summary_model)}",
                    callback_data="menu:summodel",
                )
            ]
        )
    rows.append([InlineKeyboardButton("✅ Chiudi", callback_data="menu:close")])
    return InlineKeyboardMarkup(rows)


def summary_model_keyboard(
    settings: UserSettings, summarizer: Summarizer
) -> InlineKeyboardMarkup:
    """Submenu to pick the summarization model."""

    rows = [
        [
            InlineKeyboardButton(
                f"{'• ' if option.value == settings.summary_model else ''}{option.label}",
                callback_data=f"set:summodel:{option.value}",
            )
        ]
        for option in summarizer.available_models()
    ]
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def summarize_keyboard(transcript_id: int) -> InlineKeyboardMarkup:
    """Single "summarize" button attached under a transcript."""

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📝 Riassumi", callback_data=f"sum:{transcript_id}")]]
    )


def model_keyboard(settings: UserSettings, transcriber: Transcriber) -> InlineKeyboardMarkup:
    """Submenu to pick a model, showing each model's per-minute price."""

    rows = []
    for option in transcriber.available_models():
        marker = "• " if option.value == settings.model else ""
        price = (
            f" · ${option.price_per_minute:.4f}/min"
            if option.price_per_minute is not None
            else ""
        )
        rows.append(
            [
                InlineKeyboardButton(
                    f"{marker}{option.label}{price}",
                    callback_data=f"set:model:{option.value}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def language_keyboard(settings: UserSettings, transcriber: Transcriber) -> InlineKeyboardMarkup:
    """Submenu to pick a language."""

    rows = [
        [
            InlineKeyboardButton(
                f"{'• ' if option.value == settings.language else ''}{option.label}",
                callback_data=f"set:lang:{option.value}",
            )
        ]
        for option in transcriber.available_languages()
    ]
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def stop_streaming_keyboard() -> InlineKeyboardMarkup:
    """Single "stop" button shown on the live streaming placeholder."""

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏹ Ferma", callback_data="stream:stop")]]
    )


def access_request_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Approve/deny buttons attached to an admin's view of a request."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approva", callback_data=f"req:approve:{user_id}"),
                InlineKeyboardButton("❌ Rifiuta", callback_data=f"req:deny:{user_id}"),
            ]
        ]
    )


def describe_user(record: AccessRecord) -> str:
    """Short label for a user in admin listings."""

    handle = f"@{record.username}" if record.username else (record.first_name or "—")
    return f"{handle} (id {record.user_id})"


def group_request_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Approve/deny buttons attached to an admin's view of a group request."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Abilita", callback_data=f"grp:approve:{chat_id}"
                ),
                InlineKeyboardButton(
                    "❌ Rifiuta", callback_data=f"grp:deny:{chat_id}"
                ),
            ]
        ]
    )


def describe_group(record: GroupRecord) -> str:
    """Short label for a group in admin listings."""

    title = record.title or "—"
    return f"{title} (id {record.chat_id})"
