"""Storage backends. SQLite is for development; Postgres is production.

Empty ``HISTOR_DATABASE_URL`` → one SQLite file under ``HISTOR_DATA_DIR``. A ``postgresql://``
URL → a pooled server. ``HISTOR_PROFILE=prod`` refuses to start without the URL
(:mod:`histor.config`): the log is the product, and a production log on a file that one bad
volume mount can replace is not one to publish tree heads over.

All SQL in HISTOR is written once, in the subset both engines agree on — ``?`` placeholders,
``INSERT … ON CONFLICT``, ``RETURNING`` — and never puts ``?`` or ``%`` inside a literal, so
translating placeholders for psycopg is a plain replace. Where the engines genuinely differ
(identity columns), the migration carries one statement per backend.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

# Every log append takes this lock inside its transaction on Postgres, so two processes
# pointed at the same database still append leaves one at a time. The value is arbitrary;
# it only has to be the same everywhere.
LOG_APPEND_LOCK = 0x4849_5354  # "HIST"


class Tx(Protocol):
    """One open transaction. ``execute`` returns rows as dicts (empty for statements)."""

    backend_type: str

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...

    def lock_log(self) -> None: ...


def is_postgres_url(url: str) -> bool:
    return (url or "").strip().startswith(("postgresql://", "postgres://"))


def translate_placeholders(sql: str) -> str:
    return sql.replace("?", "%s")


class _SQLiteTx:
    backend_type = "sqlite"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cur = self._conn.execute(sql, tuple(params))
        if cur.description is None:
            return []
        return [dict(row) for row in cur.fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def lock_log(self) -> None:
        """BEGIN IMMEDIATE already holds SQLite's single writer lock."""


class SQLiteBackend:
    backend_type = "sqlite"

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self._write_lock = threading.RLock()
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def read(self) -> Iterator[Tx]:
        # A connection per read, closed at once: a reader parked on an old snapshot is what
        # stops a WAL checkpoint, and an uncheckpointed WAL grows until the disk notices.
        conn = self._connect()
        try:
            yield _SQLiteTx(conn)
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield _SQLiteTx(conn)
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                conn.close()

    def maintenance(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()

    def close(self) -> None:
        """Connections are per call; nothing is held open."""


class _PostgresTx:
    backend_type = "postgresql"

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cur = self._conn.execute(translate_placeholders(sql), tuple(params))
        if cur.description is None:
            return []
        return [dict(row) for row in cur.fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def lock_log(self) -> None:
        self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOG_APPEND_LOCK,))


class PostgresBackend:
    backend_type = "postgresql"

    def __init__(self, database_url: str, *, max_size: int = 8) -> None:
        if "options=" in database_url.lower() and "application_name=" not in database_url.lower():
            # libpq `options` can set search_path and session GUCs behind the migrations' back.
            raise ValueError("HISTOR_DATABASE_URL with custom options= is refused; use a dedicated database")
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised when the extra is missing
            raise RuntimeError('Postgres needs the postgres extra: pip install "aimarket-histor[postgres]"') from exc
        self._pool = ConnectionPool(
            database_url, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=True
        )
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")

    @contextmanager
    def read(self) -> Iterator[Tx]:
        with self._pool.connection() as conn:
            yield _PostgresTx(conn)
            conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield _PostgresTx(conn)

    def maintenance(self) -> None:
        """Autovacuum owns this on Postgres."""

    def close(self) -> None:
        self._pool.close()


Backend = SQLiteBackend | PostgresBackend


def open_backend(sqlite_path: Path, database_url: str = "") -> Backend:
    url = (database_url or "").strip()
    if not url:
        return SQLiteBackend(sqlite_path)
    if is_postgres_url(url):
        return PostgresBackend(url)
    raise RuntimeError("HISTOR_DATABASE_URL must be postgresql://… or empty (SQLite under HISTOR_DATA_DIR)")
