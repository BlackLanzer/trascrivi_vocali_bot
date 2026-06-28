"""Optional transcript summarization backed by OpenRouter.

OpenRouter exposes an OpenAI-compatible ``/chat/completions`` endpoint, so we
call it directly with ``httpx`` (already a dependency) instead of pulling in the
``openai`` SDK. The summarizer is created only when ``OPENROUTER_API_KEY`` is
set; otherwise the bot runs without the summarize button.
"""

from __future__ import annotations

import logging

import httpx

from .config import Settings
from .transcription.base import ModelOption

logger = logging.getLogger(__name__)

# Selectable summarization models (OpenRouter slugs). Verify availability on
# openrouter.ai/models; the user can also override the default via env. These
# are picked for a good quality/price ratio on multilingual summaries.
_SUMMARY_MODELS = [
    ModelOption("deepseek/deepseek-chat", "DeepSeek (economico)"),
    ModelOption("google/gemini-2.5-flash", "Gemini Flash"),
    ModelOption("anthropic/claude-3.5-haiku", "Claude Haiku"),
    ModelOption("openai/gpt-4o-mini", "GPT mini"),
]

# Human-readable language names for the prompt (falls back to the raw code).
_LANGUAGE_NAMES = {
    "it": "italiano",
    "en": "inglese",
    "es": "spagnolo",
    "fr": "francese",
    "de": "tedesco",
    "multi": "italiano",
}

_REQUEST_TIMEOUT = 60.0


class Summarizer:
    """Summarize a transcript via OpenRouter's chat-completions API."""

    def __init__(self, api_key: str, base_url: str, default_model: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.default_model = default_model

    def available_models(self) -> list[ModelOption]:
        """Models offered in the settings menu."""

        return list(_SUMMARY_MODELS)

    def label_for(self, value: str) -> str:
        """Human-friendly label for a model slug, falling back to the slug."""

        for option in _SUMMARY_MODELS:
            if option.value == value:
                return option.label
        return value

    async def summarize(self, text: str, model: str, language: str) -> str:
        """Return a concise summary of ``text`` in ``language``."""

        lang_name = _LANGUAGE_NAMES.get(language, language)
        system_prompt = (
            f"Sei un assistente che riassume trascrizioni di messaggi vocali. "
            f"Riassumi il testo dell'utente in {lang_name}, in modo conciso e "
            f"fedele, evidenziando i punti principali. Rispondi solo con il "
            f"riassunto, senza preamboli."
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return str(data["choices"][0]["message"]["content"]).strip()


def get_summarizer(settings: Settings) -> Summarizer | None:
    """Build the :class:`Summarizer`, or ``None`` when no API key is set."""

    if not settings.openrouter_api_key:
        return None
    return Summarizer(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_model=settings.default_summary_model,
    )
