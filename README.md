# Trascrivi Bot

A self-hosted Telegram bot that transcribes the voice messages and audio files you
send it, using [Deepgram](https://deepgram.com/) for speech-to-text.

## Features

- **Instant silent acknowledgement** — when audio arrives the bot immediately replies
  with a *silent* "received" message (no notification), then sends a **separate message
  with a notification** once the transcript is ready, so you get pinged on completion.
- **Voice notes and audio files** — handles both Telegram voice messages and audio
  attachments (mp3, m4a, …).
- **Per-user settings** (`/settings`): transcription **model**, **language**, and
  **streaming** on/off.
  - *Streaming on*: for voice notes the bot edits the placeholder message live as the
    transcript builds up, then sends the final text as a new (notifying) message.
  - *Streaming off*: a single final transcription.
- **Pluggable providers** — Deepgram is built in; other providers can be added by
  implementing one class (see [Adding a provider](#adding-a-provider)).
- **Access control** — only approved users can use the bot. Newcomers request access
  with `/start`; admins approve or deny.
- **Polling mode** (configurable via `RUN_MODE`).

## How access control works

- **Admins** are listed in a file on the server (`config/admins.txt`), one Telegram
  user ID per line. Admins are always authorized.
- A new user sends `/start` → a pending **access request** is recorded and every admin
  is notified with inline **Approve / Deny** buttons.
- Admins can also manage requests with commands: `/requests`, `/approve <id>`,
  `/deny <id>`, `/users`, `/revoke <id>`.

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
| `DEEPGRAM_PRICE_PER_MINUTE` | `0.0043` | Per-minute rate used only for the admin `/debug` cost estimate (the API does not return a price). |

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

The `data/` and `config/` directories are mounted as volumes, so the database and the
admins file survive rebuilds. Stop the bot with `docker compose down`.

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
| `/settings` | users | Choose model, language, streaming. |
| `/status` | users | Show your access status and settings. |
| `/requests` | admins | List pending access requests. |
| `/approve <id>` | admins | Approve a user. |
| `/deny <id>` | admins | Deny a user. |
| `/users` | admins | List approved users. |
| `/revoke <id>` | admins | Revoke a user's access. |
| `/debug` | admins | Toggle a per-transcription estimated-cost report. |

## Project structure

```
src/trascrivi_bot/
├── main.py                 # entry point: build Application, register handlers, run_polling
├── config.py               # settings (pydantic-settings) + admins-file loader
├── db.py                   # SQLite access-control and settings store
├── security.py             # authorization / admin guards
├── services.py             # shared services injected into handlers
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
```
