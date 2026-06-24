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

from .models import AccessRecord, AccessStatus, UserSettings


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


def _row_to_settings(row: sqlite3.Row) -> UserSettings:
    return UserSettings(
        user_id=row["user_id"],
        provider=row["provider"],
        model=row["model"],
        language=row["language"],
        streaming=bool(row["streaming"]),
        debug=bool(row["debug"]),
    )
