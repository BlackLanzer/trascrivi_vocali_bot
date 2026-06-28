"""SQLite persistence layer for access control and per-user settings.

A single :class:`Database` instance is created at startup and shared across all
handlers. python-telegram-bot runs handlers on one event loop, but downloads and
other awaited work can interleave, so every write/read is guarded by a lock and
the connection is opened with ``check_same_thread=False``.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import AccessRecord, AccessStatus, CachedTranscript, UserSettings


def _utcnow() -> str:
    """Return an ISO-8601 UTC timestamp (stored as TEXT)."""

    return datetime.now(timezone.utc).isoformat()


class Database:
    """Typed wrapper around the SQLite database."""

    def __init__(
        self,
        path: Path,
        *,
        default_provider: str,
        default_model: str,
        default_language: str,
        default_streaming: bool,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        self._default_provider = default_provider
        self._default_model = default_model
        self._default_language = default_language
        self._default_streaming = default_streaming

        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS access (
                    user_id      INTEGER PRIMARY KEY,
                    username     TEXT,
                    first_name   TEXT,
                    status       TEXT NOT NULL
                                 CHECK (status IN ('pending', 'approved', 'denied')),
                    requested_at TEXT,
                    decided_at   TEXT,
                    decided_by   INTEGER
                );

                CREATE TABLE IF NOT EXISTS settings (
                    user_id   INTEGER PRIMARY KEY,
                    provider  TEXT NOT NULL,
                    model     TEXT NOT NULL,
                    language  TEXT NOT NULL,
                    streaming INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transcripts (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_unique_id TEXT NOT NULL,
                    model          TEXT NOT NULL,
                    language       TEXT NOT NULL,
                    text           TEXT NOT NULL,
                    audio_path     TEXT,
                    duration       REAL,
                    request_id     TEXT,
                    created_at     TEXT NOT NULL,
                    UNIQUE(file_unique_id, model, language)
                );
                """
            )

            # Lightweight migration: older databases predate the ``debug``
            # column. Add it if missing so existing deployments keep working.
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(settings)")
            }
            if "debug" not in columns:
                self._conn.execute(
                    "ALTER TABLE settings ADD COLUMN debug INTEGER NOT NULL DEFAULT 0"
                )

            self._conn.commit()

    # -- access control ---------------------------------------------------

    def get_access(self, user_id: int) -> AccessRecord | None:
        """Return the access record for ``user_id``, or ``None`` if unknown."""

        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM access WHERE user_id = ?", (user_id,)
            ).fetchone()
        return _row_to_access(row) if row is not None else None

    def create_request(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> AccessRecord:
        """Insert (or refresh) a pending access request and return it.

        If the user already exists with a non-pending status, the status is left
        untouched; callers should check :meth:`get_access` first.
        """

        now = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO access (user_id, username, first_name, status, requested_at)
                VALUES (?, ?, ?, 'pending', ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (user_id, username, first_name, now),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM access WHERE user_id = ?", (user_id,)
            ).fetchone()
        return _row_to_access(row)

    def set_status(
        self, user_id: int, status: AccessStatus, decided_by: int
    ) -> None:
        """Update a user's access status (approve / deny / revoke)."""

        with self._lock:
            self._conn.execute(
                """
                UPDATE access
                   SET status = ?, decided_at = ?, decided_by = ?
                 WHERE user_id = ?
                """,
                (status.value, _utcnow(), decided_by, user_id),
            )
            self._conn.commit()

    def list_by_status(self, status: AccessStatus) -> list[AccessRecord]:
        """Return all access records with the given status, oldest first."""

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM access WHERE status = ? ORDER BY requested_at",
                (status.value,),
            ).fetchall()
        return [_row_to_access(row) for row in rows]

    # -- transcript cache -------------------------------------------------

    def get_cached_transcript(
        self, file_unique_id: str, model: str, language: str
    ) -> CachedTranscript | None:
        """Return a cached transcript for this (file, model, language), if any."""

        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM transcripts
                 WHERE file_unique_id = ? AND model = ? AND language = ?
                """,
                (file_unique_id, model, language),
            ).fetchone()
        return _row_to_transcript(row) if row is not None else None

    def get_transcript(self, transcript_id: int) -> CachedTranscript | None:
        """Return a cached transcript by its row id, or ``None`` if absent."""

        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM transcripts WHERE id = ?", (transcript_id,)
            ).fetchone()
        return _row_to_transcript(row) if row is not None else None

    def store_transcript(
        self,
        *,
        file_unique_id: str,
        model: str,
        language: str,
        text: str,
        audio_path: str | None,
        duration: float | None,
        request_id: str | None,
    ) -> int:
        """Insert (or replace) a cached transcript and return its row id."""

        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO transcripts
                    (file_unique_id, model, language, text, audio_path,
                     duration, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_unique_id, model, language) DO UPDATE SET
                    text = excluded.text,
                    audio_path = excluded.audio_path,
                    duration = excluded.duration,
                    request_id = excluded.request_id,
                    created_at = excluded.created_at
                """,
                (
                    file_unique_id,
                    model,
                    language,
                    text,
                    audio_path,
                    duration,
                    request_id,
                    _utcnow(),
                ),
            )
            self._conn.commit()
            # lastrowid is unreliable on the UPDATE branch; look the row up.
            row = self._conn.execute(
                """
                SELECT id FROM transcripts
                 WHERE file_unique_id = ? AND model = ? AND language = ?
                """,
                (file_unique_id, model, language),
            ).fetchone()
        return int(row["id"])

    def delete_transcripts_older_than(self, cutoff_iso: str) -> list[str]:
        """Delete transcripts created before ``cutoff_iso``.

        Returns the ``audio_path`` values of the deleted rows (non-null only) so
        the caller can remove the cached audio files from disk.
        """

        with self._lock:
            rows = self._conn.execute(
                "SELECT audio_path FROM transcripts WHERE created_at < ?",
                (cutoff_iso,),
            ).fetchall()
            self._conn.execute(
                "DELETE FROM transcripts WHERE created_at < ?", (cutoff_iso,)
            )
            self._conn.commit()
        return [row["audio_path"] for row in rows if row["audio_path"]]

    # -- per-user settings ------------------------------------------------

    def get_settings(self, user_id: int) -> UserSettings:
        """Return the user's settings, creating a default row on first access."""

        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO settings
                        (user_id, provider, model, language, streaming, debug)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        self._default_provider,
                        self._default_model,
                        self._default_language,
                        int(self._default_streaming),
                        0,
                    ),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM settings WHERE user_id = ?", (user_id,)
                ).fetchone()
        return _row_to_settings(row)

    def update_setting(self, user_id: int, key: str, value: str | bool) -> None:
        """Update a single settings column for a user.

        ``key`` must be one of ``model``, ``language``, ``streaming`` or
        ``provider`` (validated to avoid SQL injection via column name).
        """

        if key not in {"model", "language", "streaming", "provider", "debug"}:
            raise ValueError(f"Unknown setting: {key!r}")

        # Ensure a row exists before updating.
        self.get_settings(user_id)

        stored: str | int = (
            int(value) if key in {"streaming", "debug"} else str(value)
        )
        with self._lock:
            self._conn.execute(
                f"UPDATE settings SET {key} = ? WHERE user_id = ?",  # noqa: S608 - key is whitelisted
                (stored, user_id),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_access(row: sqlite3.Row) -> AccessRecord:
    return AccessRecord(
        user_id=row["user_id"],
        username=row["username"],
        first_name=row["first_name"],
        status=AccessStatus(row["status"]),
        requested_at=row["requested_at"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
    )


def _row_to_transcript(row: sqlite3.Row) -> CachedTranscript:
    return CachedTranscript(
        id=row["id"],
        file_unique_id=row["file_unique_id"],
        model=row["model"],
        language=row["language"],
        text=row["text"],
        audio_path=row["audio_path"],
        duration=row["duration"],
        request_id=row["request_id"],
        created_at=row["created_at"],
    )


def _row_to_settings(row: sqlite3.Row) -> UserSettings:
    return UserSettings(
        user_id=row["user_id"],
        provider=row["provider"],
        model=row["model"],
        language=row["language"],
        streaming=bool(row["streaming"]),
        debug=bool(row["debug"]),
    )
