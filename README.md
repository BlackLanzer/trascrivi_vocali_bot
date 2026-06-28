# Trascrivi Bot

A self-hosted Telegram bot that transcribes the voice messages and audio files you
send it, using [Deepgram](https://deepgram.com/) for speech-to-text.

## Features

- **Instant silent acknowledgement** — when audio arrives the bot immediately replies
  with a *silent* "received" message (no notification), then sends a **separate message
  with a notification** once the transcript is ready, so you get pinged on completion.
- **Voice notes and audio files** — handles both Telegram voice messages and audio
  attachments (mp3, m4a, …).
- **Per-user settings** (`/settings`): transcription **model** (with per-minute prices
  shown in the picker), **language**, **streaming** on/off, and the **summary model**.
  - *Streaming on*: for voice notes the bot edits the placeholder message live as the
    transcript builds up, then sends the final text as a new (notifying) message.
  - *Streaming off*: a single final transcription.
- **On-demand summaries** — when an OpenRouter key is configured, each transcript gets a
  **📝 Riassumi** button that summarizes it in its language (model selectable in
  `/settings`). See [Summarization](#summarization).
- **Audio + transcript cache** — re-sending the same audio with the same model and
  language reuses the stored transcript instead of re-billing; a background task prunes
  the cache. See [Caching](#caching).
- **Group support** — admins enable a group once; afterwards any member's voice notes are
  transcribed. See [Groups](#groups).
- **Pluggable providers** — Deepgram is built in; other providers can be added by
  implementing one class (see [Adding a provider](#adding-a-provider)).
- **Access control** — only approved users can use the bot. Newcomers request access
  with `/start`; admins approve or deny.
- **Server-side logging** — a structured line per transcription plus a rotating log file
  (see [Logging](#logging)).
- **Polling mode** (configurable via `RUN_MODE`).

## How access control works

- **Admins** are listed in a file on the server (`config/admins.txt`), one Telegram
  user ID per line. Admins are always authorized.
- A new user sends `/start` → a pending **access request** is recorded and every admin
  is notified with inline **Approve / Deny** buttons.
- Admins can also manage requests with commands: `/requests`, `/approve <id>`,
  `/deny <id>`, `/users`, `/revoke <id>`, and groups with `/groups` (see [Groups](#groups)).

To find a Telegram user ID, message [@userinfobot](https://t.me/userinfobot).

## Prerequisites

- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- A Deepgram API key from the [Deepgram console](https://console.deepgram.com/).
- For deployment: Docker + Docker Compose. For development: Python 3.10+.

## Configuration

All configuration is via environment variables (or a `.env` file). Copy the template:

```bash
cp .env.example .env
```

Then edit `.env` and set at least `TELEGRAM_BOT_TOKEN` and `DEEPGRAM_API_KEY`.

| Variable | Default | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | — | **Required.** Bot token from @BotFather. |
| `DEEPGRAM_API_KEY` | — | **Required.** Deepgram API key. |
| `TRANSCRIPTION_PROVIDER` | `deepgram` | Provider to use. |
| `RUN_MODE` | `polling` | Update mode (only `polling` is implemented). |
| `ADMINS_FILE` | `config/admins.txt` | File of admin user IDs. |
| `DB_PATH` | `data/bot.db` | SQLite database path. |
| `DEFAULT_MODEL` | `nova-3` | Default model for new users. |
| `DEFAULT_LANGUAGE` | `it` | Default language for new users. |
| `DEFAULT_STREAMING` | `false` | Default streaming setting for new users. |
| `DEEPGRAM_PRICE_PER_MINUTE` | `0.0043` | Fallback per-minute rate for the admin `/debug` cost estimate; per-model prices (shown in the model picker) take precedence. |
| `OPENROUTER_API_KEY` | _(empty)_ | OpenRouter key enabling the summarize button. Empty = summaries disabled. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base URL. |
| `DEFAULT_SUMMARY_MODEL` | `deepseek/deepseek-chat` | Default summarization model for new users (selectable in `/settings`). |
| `CACHE_DIR` | `data/cache` | Directory for cached audio files. |
| `CACHE_RETENTION_DAYS` | `7` | Days to keep cached audio + transcripts before cleanup. |
| `CACHE_CLEANUP_INTERVAL_HOURS` | `6` | How often the background cleanup runs. |
| `LOG_LEVEL` | `INFO` | Root log level. |
| `LOG_DIR` | `data/logs` | Directory for the rotating log file (`bot.log`). |
| `LOG_MAX_BYTES` | `5000000` | Max size per log file before rotation. |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated log files to keep. |

Set up the admins file:

```bash
cp config/admins.txt.example config/admins.txt
# edit config/admins.txt and add your numeric Telegram user ID(s)
```

## Run with Docker (recommended for deployment)

```bash
cp .env.example .env                       # fill in your tokens
cp config/admins.txt.example config/admins.txt   # add your admin ID(s)

docker compose up -d --build
docker compose logs -f                     # follow the logs
```

The `data/` and `config/` directories are mounted as volumes, so the database, the audio
cache (`data/cache`), the rotating logs (`data/logs`) and the admins file survive
rebuilds. Stop the bot with `docker compose down`.

> **Permissions note:** the container runs as a non-root user (uid 1000). The bind-mounted
> `data/` and `config/` directories must be writable by that uid. On most single-user
> Linux hosts (where your user is uid 1000) this works out of the box.

## Run locally (development)

This project uses a `src/` layout, so set `PYTHONPATH=src` when running.

```bash
python -m venv .venv          # if 'ensurepip' is missing: pip install virtualenv && python -m virtualenv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # fill in your tokens
cp config/admins.txt.example config/admins.txt   # add your admin ID(s)

PYTHONPATH=src python -m trascrivi_bot.main
```

## Usage

1. Add your own Telegram user ID to `config/admins.txt` so you're an admin.
2. Start the bot. Send `/start` from your account — as an admin you're authorized
   immediately.
3. Send a voice message → a silent "received" reply appears instantly, then a new
   (notifying) message arrives with the transcript.
4. Use `/settings` to change model, language, or toggle streaming.
5. When another person sends `/start`, you'll get an Approve/Deny prompt (or run
   `/requests`).

### Commands

| Command | Who | Description |
| --- | --- | --- |
| `/start` | everyone | Start the bot / request access. |
| `/help` | users | Show available commands. |
| `/settings` | users | Choose model, language, streaming, summary model. |
| `/model [value]` | users | Show or set the transcription model. |
| `/language [code]` | users | Show or set the transcription language. |
| `/streaming [on\|off]` | users | Toggle or set live streaming. |
| `/status` | users | Show your access status and settings. |
| `/requests` | admins | List pending access requests. |
| `/approve <id>` | admins | Approve a user. |
| `/deny <id>` | admins | Deny a user. |
| `/users` | admins | List approved users. |
| `/revoke <id>` | admins | Revoke a user's access. |
| `/groups` | admins | List groups and enable/disable them. |
| `/debug` | admins | Toggle a per-transcription estimated-cost report. |

> In a group, `/settings`, `/model`, `/language` and `/streaming` act on the **group's**
> shared settings rather than your personal ones.

## Summarization

When `OPENROUTER_API_KEY` is set, every transcript gets a **📝 Riassumi** button. Tapping
it summarizes the transcript in its language via [OpenRouter](https://openrouter.ai/) and
replies with the summary — so you only pay for a summary when you actually ask for one.

The model is per-user (per-group in groups) and selectable in `/settings` from a short
list (DeepSeek, Gemini Flash, Claude Haiku, GPT mini). The default comes from
`DEFAULT_SUMMARY_MODEL`; you can point it at **any** slug listed on
[openrouter.ai/models](https://openrouter.ai/models). Leave `OPENROUTER_API_KEY` empty to
disable summaries entirely (no button is shown).

> Deepgram's own `summarize` feature is English-only, so it isn't used here; OpenRouter
> summarizes in any language, including Italian.

## Caching

Each transcription is cached in the database keyed by
`(file_unique_id, model, language)`, and the audio bytes are stored under `CACHE_DIR`
(`data/cache`). Re-sending the **same** audio with the **same** model and language reuses
the stored transcript — no re-download, no Deepgram call, no cost (logged as `cache=hit`).

A background task runs every `CACHE_CLEANUP_INTERVAL_HOURS` and deletes cached transcripts
and audio older than `CACHE_RETENTION_DAYS` (default 7 days).

> **Privacy note:** audio files are written to disk under `data/cache`. Lower
> `CACHE_RETENTION_DAYS` (or clear `data/cache`) if you don't want them kept.

## Groups

Group access is gated by the **admin**, not by individual membership:

1. Add the bot to a group. The bot files a pending request and notifies every admin with
   **✅ Abilita / ❌ Rifiuta** buttons (also listed under `/groups`).
2. Once an admin enables the group, voice notes from **any** member are transcribed.
3. The group shares one set of settings (model, language, streaming, summary model),
   editable by members via `/settings` in the group.

> **BotFather privacy mode (important):** to receive voice notes in groups, the bot must
> have **group privacy disabled**. In [@BotFather](https://t.me/BotFather): `/setprivacy`
> → select your bot → **Disable**. With privacy enabled (the default) Telegram only
> delivers commands and replies to the bot, so plain voice messages never arrive.

## Logging

Logs go to **stdout** (visible via `docker compose logs -f`) and to a **rotating file** at
`LOG_DIR/bot.log` (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`). Set verbosity with `LOG_LEVEL`.
Each transcription emits one structured line — user, chat type/id, file id, model,
language, mode (streaming/pre-recorded/cache), cache hit/miss, duration, processing time,
estimated cost, and Deepgram request id — alongside access- and group-decision events.

## Project structure

```
src/trascrivi_bot/
├── main.py                 # entry point: build Application, register handlers, run_polling
├── config.py               # settings (pydantic-settings) + admins-file loader
├── db.py                   # SQLite access-control and settings store
├── security.py             # chat-aware authorization / admin guards
├── services.py             # shared services injected into handlers
├── summarization.py        # optional OpenRouter-backed transcript summaries
├── keyboards.py            # inline-keyboard builders
├── models.py               # dataclasses and enums
├── handlers/
│   ├── commands.py         # /start, /help, /settings, /status, admin commands
│   ├── media.py            # the core flow: download → transcribe → reply
│   └── callbacks.py        # inline-keyboard callbacks
└── transcription/
    ├── base.py             # Transcriber interface + result types
    ├── deepgram_provider.py
    └── factory.py          # provider selection
```

## Adding a provider

1. Create a class implementing `transcription.base.Transcriber`
   (`transcribe_file`, `available_models`, `available_languages`; optionally
   `supports_streaming` + `transcribe_stream`).
2. Register it in `transcription/factory.py`.
3. Set `TRANSCRIPTION_PROVIDER` to its name.

## Notes

- Telegram caps bot file downloads at 20 MB; larger audio is rejected with a message.
- Streaming is only meaningful for voice notes (OGG/Opus, streamed via Deepgram's
  `ogg-opus` live encoding). Audio *files* always use the pre-recorded path, which
  accepts virtually any format natively.
- **No inline mode.** Telegram's inline queries (`@bot …`) accept only text typed by the
  user — there is no way to send a voice note through inline — so transcribing audio via
  inline is not possible on the platform. Send or forward audio to the bot (in a private
  chat or an enabled group) instead.
```
