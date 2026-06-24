# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted Telegram bot that transcribes voice messages and audio files via Deepgram. Python 3.10+, fully type-hinted, async, `src/` layout. User-facing bot strings are **Italian**; code and comments are **English**.

## Commands

This machine lacks `ensurepip`, so `python -m venv` fails. Use `virtualenv`:

```bash
pip3 install --user virtualenv && python3 -m virtualenv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install mypy        # dev-only, not in requirements.txt
```

```bash
.venv/bin/mypy                                    # type-check (config in pyproject.toml; must stay clean)
PYTHONPATH=src .venv/bin/python -m trascrivi_bot.main   # run locally (needs .env with real tokens)
docker compose up -d --build                      # deploy; logs: docker compose logs -f
```

There is **no test suite**. Verify changes with: `mypy`, then an import/build smoke check (build the app graph without network — a dummy `TELEGRAM_BOT_TOKEN` reaches Telegram and 401s, which confirms wiring), then exercise pure logic (DB round-trips, `_split_text`, streaming-gating) directly in a `PYTHONPATH=src` Python session.

## Library API rule (important)

Both `deepgram-sdk` (7.x) and `python-telegram-bot` (22.x) are pinned in `requirements.txt`. The Deepgram v7 SDK is a generated client whose API differs sharply from older versions and from most online examples. **Do not trust training knowledge or web snippets for these APIs** — verify against the installed package via `inspect.signature(...)` / `dir(...)`. Facts already verified and relied upon:

- Pre-recorded: `await client.listen.v1.media.transcribe_file(request=<bytes>, model=, language=, smart_format=)` → transcript at `resp.results.channels[0].alternatives[0].transcript`.
- Live: `async with client.listen.v1.connect(model=, encoding="ogg-opus", interim_results=True, ...) as socket`; `socket.send_media(bytes)` / `send_finalize()` / `send_close_stream()`; iterating the socket yields `ListenV1Results` (`.is_final`, `.channel.alternatives[0].transcript`) and a terminal `ListenV1Metadata`.
- The live socket accepts `encoding="ogg-opus"`, so Telegram OGG/Opus voice notes stream as-is — **no ffmpeg anywhere** in this project.
- `Application.run_polling()` is **synchronous/blocking** (manages its own loop); do not `await` it.

## Architecture

**Dependency injection via `bot_data`.** `main.build_application()` constructs one `Services` bundle (`settings`, `db`, `admins`, `transcriber`) and stores it in `application.bot_data`. Handlers retrieve it typed via `services.get_services(context)` — never read `bot_data` directly. This is the seam connecting every layer.

**Provider abstraction (`transcription/`).** `base.Transcriber` is the ABC every provider implements; `factory.get_transcriber(settings)` is the **only** place a provider is selected/instantiated. Adding a provider = new `Transcriber` subclass + one branch in the factory; nothing else changes. The transcriber also owns the settings-menu option lists (`available_models` / `available_languages`), so menus stay provider-specific.

**Streaming vs pre-recorded (`handlers/media.py` — the core flow).** On voice/audio: send a **silent** placeholder (`disable_notification=True`), download bytes, transcribe, then send the transcript as a **new** message **with** a notification (this two-message pattern is a hard product requirement — the user is pinged only on completion). Streaming is used only when the user enabled it **and** `transcriber.supports_streaming(mimetype)` is true (OGG voice notes only); otherwise it falls back to the pre-recorded path. Audio *files* (mp3/m4a/…) always go pre-recorded. Streaming edits the placeholder live, throttled to ≤1 edit/sec. Transcripts are split to Telegram's 4096-char limit.

**Access control — two stores, by design.** Admins live in a plain server file `config/admins.txt` (loaded once at startup into `Services.admins`); everything else (pending/approved/denied users, per-user settings) is in **SQLite** (`db.Database`). `security.require_auth` / `require_admin` decorators gate handlers and send state-appropriate Italian replies to unauthorized users. `/start` files a request and pings all admins with inline approve/deny buttons; approve/deny exist both as commands (`handlers/commands.py`) and callbacks (`handlers/callbacks.py`).

**Callback-data convention.** Inline buttons encode actions as colon-delimited strings routed by `CallbackQueryHandler` patterns: `menu:*` and `set:<field>:<value>` → `callbacks.settings_callback` (pattern `^(menu|set):`); `req:<approve|deny>:<user_id>` → `callbacks.access_callback` (pattern `^req:`). Keep new buttons within these prefixes or register a new pattern in `main.py`.

## Conventions

- `db.Database` guards a single `check_same_thread=False` connection with a `threading.Lock`; `update_setting` whitelists column names (never interpolate untrusted keys into SQL).
- New user-facing strings: Italian. New settings: thread the default through `Settings` (config.py) → `Database` constructor defaults → `get_settings` row creation.
- `data/` and `config/` are bind-mounted in Docker and must be writable by uid 1000 (the container's non-root user).
