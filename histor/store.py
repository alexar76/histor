"""The ledger: targets, observations, content-addressed blobs, labels, the Merkle log, tree heads.

Backend-neutral: every statement here runs unchanged on SQLite (development) and Postgres
(production) through :mod:`histor.db`. The schema itself lives in :mod:`histor.migrations`.
"""

from __future__ import annotations

import json
from typing import Any

from histor import merkle
from histor.db import Backend, Tx


def dumps(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # An unpaired surrogate from a stranger's JSON: escaped, it is still the same JSON value,
        # and UTF-8 (so Postgres, and SQLite's encoder) can carry it.
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return text


class Store:
    def __init__(self, db: Backend) -> None:
        self.db = db

    @property
    def backend_type(self) -> str:
        return self.db.backend_type

    # -- meta ---------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self.db.read() as tx:
            row = tx.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    @staticmethod
    def set_meta(tx: Tx, key: str, value: str) -> None:
        tx.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # -- blobs --------------------------------------------------------------------

    @staticmethod
    def put_blob(tx: Tx, digest: str, kind: str, body: Any) -> None:
        tx.execute(
            "INSERT INTO blobs(digest, kind, body) VALUES(?, ?, ?) ON CONFLICT(digest) DO NOTHING",
            (digest, kind, dumps(body)),
        )

    def blob_text(self, digest: str, kind: str) -> str | None:
        """The stored JSON as text — served as is, never parsed and re-serialised per request."""
        with self.db.read() as tx:
            row = tx.one("SELECT body FROM blobs WHERE digest=? AND kind=?", (digest, kind))
        return None if row is None else str(row["body"])

    def set_meta_value(self, key: str, value: str) -> None:
        with self.db.transaction() as tx:
            Store.set_meta(tx, key, value)

    def has_blob(self, digest: str, kind: str) -> bool:
        with self.db.read() as tx:
            return tx.one("SELECT 1 AS x FROM blobs WHERE digest=? AND kind=?", (digest, kind)) is not None

    def get_blob(self, digest: str, kind: str | None = None) -> Any | None:
        with self.db.read() as tx:
            row = tx.one("SELECT kind, body FROM blobs WHERE digest=?", (digest,))
        if row is None or (kind is not None and row["kind"] != kind):
            return None
        return json.loads(row["body"])

    # -- classifier verdicts (advisory, NOT in the signed log) ---------------------

    @staticmethod
    def put_classification(tx: Tx, toolset: str, model: str, verdict: Any, updated_at: str) -> None:
        tx.execute(
            "INSERT INTO classifications(toolset, model, verdict, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(toolset, model) DO UPDATE SET verdict=excluded.verdict, updated_at=excluded.updated_at",
            (toolset, model, dumps(verdict), updated_at),
        )

    def classification(self, toolset: str, model: str) -> dict[str, Any] | None:
        """The advisory verdict for a tool set from one model, or None. Not a log entry."""
        with self.db.read() as tx:
            row = tx.one(
                "SELECT verdict, updated_at FROM classifications WHERE toolset=? AND model=?",
                (toolset, model),
            )
        if row is None:
            return None
        out = json.loads(row["verdict"])
        out["updatedAt"] = row["updated_at"]
        return out

    def latest_classification(self, toolset: str) -> dict[str, Any] | None:
        """The most recent advisory verdict for a tool set from ANY model (the verdict names its
        model), or None. Used by /check, which reads stored verdicts and never calls a model."""
        with self.db.read() as tx:
            row = tx.one(
                "SELECT verdict, updated_at FROM classifications WHERE toolset=? "
                "ORDER BY updated_at DESC, model LIMIT 1",
                (toolset,),
            )
        if row is None:
            return None
        out = json.loads(row["verdict"])
        out["updatedAt"] = row["updated_at"]
        return out

    # -- targets ------------------------------------------------------------------

    def target(self, target_id: str) -> dict[str, Any] | None:
        with self.db.read() as tx:
            return tx.one("SELECT * FROM targets WHERE id=?", (target_id,))

    def targets_by(self, *, endpoint: str | None = None, name: str | None = None) -> list[dict[str, Any]]:
        with self.db.read() as tx:
            if endpoint:
                return tx.execute("SELECT * FROM targets WHERE endpoint=? ORDER BY delisted, name", (endpoint,))
            return tx.execute("SELECT * FROM targets WHERE name=? ORDER BY delisted, endpoint", (name or "",))

    def all_targets(self) -> list[dict[str, Any]]:
        with self.db.read() as tx:
            return tx.execute("SELECT * FROM targets ORDER BY name, endpoint")

    def search_targets(self, q: str, *, state: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
        where = ["delisted = 0"]
        args: list[Any] = []
        if q:
            needle = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append(
                "(LOWER(name) LIKE ? ESCAPE '\\' OR LOWER(endpoint) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(title, '')) LIKE ? ESCAPE '\\')"
            )
            args += [needle, needle, needle]
        state_sql = {
            # Pinned = a tool-set digest on record. A set HISTOR could not digest has a names-only
            # descriptor (current_subject) but nothing a client could be compared with.
            "pinned": "current_toolset IS NOT NULL",
            "changed": "changes > 0",
            "flagged": "(block_matches > 0 OR advise_matches > 0 OR record_matches > 0)",
            "unobserved": "current_toolset IS NULL",
        }.get(state)
        if state_sql:
            where.append(state_sql)
        clause = " AND ".join(where)
        with self.db.read() as tx:
            total = int(tx.one(f"SELECT COUNT(*) AS n FROM targets WHERE {clause}", args)["n"])
            rows = tx.execute(
                f"SELECT * FROM targets WHERE {clause} "
                "ORDER BY CASE WHEN current_toolset IS NULL THEN 1 ELSE 0 END, changes DESC, name, endpoint "
                "LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
        return total, rows

    # -- observations ---------------------------------------------------------------

    def history(self, target_id: str, limit: int = 400) -> list[dict[str, Any]]:
        with self.db.read() as tx:
            return tx.execute(
                "SELECT observed_at, status, detail, subject, toolset, tool_count, server_name, server_version "
                "FROM observations WHERE target_id=? ORDER BY observed_at DESC, id DESC LIMIT ?",
                (target_id, limit),
            )

    def toolset_seen(self, target_id: str, toolset: str) -> dict[str, Any] | None:
        with self.db.read() as tx:
            row = tx.one(
                "SELECT MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen, COUNT(*) AS n "
                "FROM observations WHERE target_id=? AND toolset=?",
                (target_id, toolset),
            )
        if not row or not int(row["n"] or 0):
            return None
        return {"first_seen": row["first_seen"], "last_seen": row["last_seen"], "observations": int(row["n"])}

    # -- labels + log ---------------------------------------------------------------

    def label(self, label_id: str) -> dict[str, Any] | None:
        with self.db.read() as tx:
            return tx.one("SELECT * FROM labels WHERE id=?", (label_id,))

    def label_by_digest(self, digest: str) -> dict[str, Any] | None:
        with self.db.read() as tx:
            return tx.one("SELECT * FROM labels WHERE digest=?", (digest,))

    def labels_for(self, target_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.read() as tx:
            return tx.execute(
                "SELECT id, method, verdict, subject, observed_at, issued_at, digest, leaf_index FROM labels "
                "WHERE target_id=? ORDER BY leaf_index DESC LIMIT ?",
                (target_id, limit),
            )

    @staticmethod
    def log_size(tx: Tx) -> int:
        # MAX over the (level, idx) primary key is an index lookup on both engines; COUNT(*) would
        # scan every leaf on every append once the log holds millions of labels.
        row = tx.one("SELECT MAX(idx) AS last FROM log_nodes WHERE level=0")
        return 0 if row is None or row["last"] is None else int(row["last"]) + 1

    def tree_size(self) -> int:
        with self.db.read() as tx:
            return self.log_size(tx)

    @staticmethod
    def reader(tx: Tx) -> merkle.NodeReader:
        def read(level: int, idx: int) -> bytes:
            row = tx.one("SELECT hash FROM log_nodes WHERE level=? AND idx=?", (level, idx))
            if row is None:
                raise KeyError((level, idx))
            return bytes.fromhex(row["hash"])

        return read

    @classmethod
    def append_leaf(cls, tx: Tx, leaf: bytes) -> int:
        """Append one leaf inside *tx*. The caller must already hold ``tx.lock_log()``."""
        index = cls.log_size(tx)
        for level, idx, digest in merkle.appended_nodes(index, leaf, cls.reader(tx)):
            tx.execute("INSERT INTO log_nodes(level, idx, hash) VALUES(?, ?, ?)", (level, idx, digest.hex()))
        return index

    def latest_sth(self) -> dict[str, Any] | None:
        with self.db.read() as tx:
            row = tx.one("SELECT body FROM sths ORDER BY tree_size DESC LIMIT 1")
        return json.loads(row["body"]) if row else None

    def sth(self, tree_size: int) -> dict[str, Any] | None:
        with self.db.read() as tx:
            row = tx.one("SELECT body FROM sths WHERE tree_size=?", (tree_size,))
        return json.loads(row["body"]) if row else None

    def entries(self, start: int, end: int) -> list[dict[str, Any]]:
        with self.db.read() as tx:
            return tx.execute(
                "SELECT leaf_index, id, digest, method, verdict, target_id, issued_at FROM labels "
                "WHERE leaf_index >= ? AND leaf_index < ? ORDER BY leaf_index",
                (start, end),
            )

    # -- changes ------------------------------------------------------------------

    @staticmethod
    def _change_row(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["summary"] = json.loads(row["summary"])
        return row

    def changes(self, limit: int = 50, before_id: int | None = None, target_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT c.*, t.name, t.endpoint, t.title FROM changes c JOIN targets t ON t.id = c.target_id WHERE 1=1"
        args: list[Any] = []
        if before_id:
            sql += " AND c.id < ?"
            args.append(before_id)
        if target_id:
            sql += " AND c.target_id = ?"
            args.append(target_id)
        sql += " ORDER BY c.id DESC LIMIT ?"
        args.append(limit)
        with self.db.read() as tx:
            return [self._change_row(r) for r in tx.execute(sql, args)]

    def change(self, change_id: int) -> dict[str, Any] | None:
        with self.db.read() as tx:
            row = tx.one(
                "SELECT c.*, t.name, t.endpoint, t.title FROM changes c JOIN targets t ON t.id = c.target_id WHERE c.id=?",
                (change_id,),
            )
        return self._change_row(row) if row else None

    def count_changes_since(self, since: str) -> int:
        with self.db.read() as tx:
            return int(tx.one("SELECT COUNT(*) AS n FROM changes WHERE observed_at >= ?", (since,))["n"])

    # -- client reports -------------------------------------------------------------

    def add_client_report(self, target_id: str, toolset: str, day: str) -> None:
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO client_reports(target_id, toolset, day, count) VALUES(?, ?, ?, 1) "
                "ON CONFLICT(target_id, toolset, day) DO UPDATE SET count = client_reports.count + 1",
                (target_id, toolset, day),
            )

    def client_reports(self, target_id: str, since_day: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.read() as tx:
            rows = tx.execute(
                "SELECT toolset, SUM(count) AS reports, MIN(day) AS first_day, MAX(day) AS last_day "
                "FROM client_reports WHERE target_id=? AND day >= ? GROUP BY toolset ORDER BY reports DESC LIMIT ?",
                (target_id, since_day, limit),
            )
        return [{**r, "reports": int(r["reports"])} for r in rows]

    def prune_client_reports(self, before_day: str) -> int:
        """Reports are a rolling window, not an archive; nothing reads past 30 days."""
        with self.db.transaction() as tx:
            rows = tx.execute("DELETE FROM client_reports WHERE day < ? RETURNING day", (before_day,))
        return len(rows)

    def client_report_total(self, since_day: str) -> int:
        with self.db.read() as tx:
            row = tx.one("SELECT COALESCE(SUM(count), 0) AS n FROM client_reports WHERE day >= ?", (since_day,))
        return int(row["n"])

    # -- runs -----------------------------------------------------------------------

    def last_successful_run(self) -> dict[str, Any] | None:
        """The newest finished run whose stats carry no error — what the crawl interval counts from."""
        with self.db.read() as tx:
            rows = tx.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 50")
        for row in rows:
            stats = json.loads(row["stats"]) if row.get("stats") else {}
            if not stats.get("error"):
                row["stats"] = stats
                return row
        return None

    def mark_interrupted_runs(self, now: str) -> int:
        """A run with no finish time after a restart was interrupted: say so instead of leaving it open."""
        with self.db.transaction() as tx:
            rows = tx.execute("SELECT id, stats FROM runs WHERE finished_at IS NULL")
            for row in rows:
                stats = json.loads(row["stats"]) if row.get("stats") else {}
                stats["error"] = "interrupted: the process restarted before the crawl finished"
                tx.execute("UPDATE runs SET finished_at=?, stats=? WHERE id=?", (now, dumps(stats), row["id"]))
        return len(rows)

    def last_run(self, finished_only: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM runs"
        if finished_only:
            sql += " WHERE finished_at IS NOT NULL"
        with self.db.read() as tx:
            row = tx.one(sql + " ORDER BY id DESC LIMIT 1")
        if row is None:
            return None
        row["stats"] = json.loads(row["stats"]) if row.get("stats") else {}
        return row

    # -- stats ----------------------------------------------------------------------

    def target_counts(self) -> dict[str, int]:
        with self.db.read() as tx:
            row = tx.one(
                "SELECT COUNT(*) AS listed, "
                "SUM(CASE WHEN current_toolset IS NOT NULL THEN 1 ELSE 0 END) AS pinned, "
                "SUM(CASE WHEN changes > 0 THEN 1 ELSE 0 END) AS ever_changed, "
                "SUM(CASE WHEN block_matches > 0 THEN 1 ELSE 0 END) AS block_tier, "
                "SUM(CASE WHEN COALESCE(block_matches, 0) = 0 AND advise_matches > 0 THEN 1 ELSE 0 END) AS advise_only, "
                "SUM(CASE WHEN record_matches > 0 THEN 1 ELSE 0 END) AS name_records, "
                "SUM(CASE WHEN skip_reason IS NOT NULL THEN 1 ELSE 0 END) AS not_attempted "
                "FROM targets WHERE delisted = 0"
            )
            servers = tx.one("SELECT COUNT(DISTINCT name) AS n FROM targets WHERE delisted = 0")
        out = {k: int(v or 0) for k, v in row.items()}
        out["servers"] = int(servers["n"] or 0)
        return out
