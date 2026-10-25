"""Durable remote polling, independent of package discovery and sandbox completion.

Intervals are targets, not guarantees: expose overdue work and measured gaps. Polling still
cannot recover states that appear and disappear between observations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit

from histor.config import Settings
from histor.db import Tx
from histor.registry import Target
from histor.store import Store


def after(ts: str, seconds: int) -> str:
    return (datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class RemoteSchedule:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    def add(self, tx: Tx, target: Target, now: str) -> None:
        if target.skip_reason or target.transport != "streamable-http":
            return
        host = urlsplit(target.endpoint).hostname or ""
        tx.execute("INSERT INTO remote_schedule(target_id, host, next_due) VALUES(?, ?, ?) "
                   "ON CONFLICT(target_id) DO NOTHING", (target.id, host, now))

    def due(self, now: str, limit: int) -> list[Target]:
        with self.store.db.read() as tx:
            rows = tx.execute(
                "WITH ready AS (SELECT t.*, s.next_due, s.hot_until, "
                "ROW_NUMBER() OVER (PARTITION BY s.host ORDER BY "
                "CASE WHEN s.hot_until>? THEN 0 ELSE 1 END, s.next_due, t.id) AS host_rank "
                "FROM remote_schedule s JOIN targets t ON t.id=s.target_id "
                "LEFT JOIN remote_host_backoff h ON h.host=s.host "
                "WHERE t.delisted=0 AND t.skip_reason IS NULL AND t.transport='streamable-http' "
                "AND s.next_due<=? AND (h.until_at IS NULL OR h.until_at<=?) "
                "AND (?=0 OR t.id IN (SELECT id FROM targets WHERE delisted=0 AND skip_reason IS NULL "
                "ORDER BY id LIMIT ?))) "
                "SELECT * FROM ready WHERE host_rank<=? "
                "ORDER BY CASE WHEN hot_until>? THEN 0 ELSE 1 END, next_due, id LIMIT ?",
                (now, now, now, self.settings.crawl_limit, self.settings.crawl_limit,
                 self.settings.crawl_per_host, now, limit),
            )
        return [Target(**{k: row.get(k) for k in (
            "id", "name", "endpoint", "transport", "registry", "title", "description", "version",
            "repository", "website", "skip_reason")}) for row in rows]

    def record(self, target_id: str, now: str, status: str) -> None:
        cfg = self.settings
        with self.store.db.transaction() as tx:
            row = tx.one("SELECT * FROM remote_schedule WHERE target_id=?", (target_id,))
            if not row:
                return
            changes = tx.one("SELECT COUNT(*) AS n, MAX(observed_at) AS latest FROM changes "
                             "WHERE target_id=? AND observed_at>=?", (target_id, after(now, -cfg.remote_change_window_s)))
            hot_until = after(changes["latest"], cfg.remote_change_window_s) if changes["n"] >= 2 else None
            interval = min(cfg.remote_interval_s, cfg.remote_changed_interval_s) if hot_until else cfg.remote_interval_s
            failures = 0 if status == "ok" else int(row["failures"]) + 1
            # A failing endpoint backs off to at most a day. A 429 also pauses its whole host.
            delay = max(60, interval)
            if failures:
                # A rate limit must never make us retry sooner than the normal cadence,
                # including endpoints that qualified for the faster change-watching interval.
                base = max(3600, cfg.remote_interval_s, delay) if status == "http-429" else delay
                delay = min(max(86400, base), base * 2 ** min(failures - 1, 8))
            if status in {"http-401", "http-403"}:
                delay = max(delay, 86400)
            gap = None
            if row["last_checked"]:
                gap = max(0, int((datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") -
                                  datetime.strptime(row["last_checked"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds()))
            next_due = after(now, delay)
            tx.execute("UPDATE remote_schedule SET next_due=?, hot_until=?, failures=?, last_checked=?, last_gap_s=? "
                       "WHERE target_id=?", (next_due, hot_until, failures, now, gap, target_id))
            if status == "http-429":
                tx.execute("INSERT INTO remote_host_backoff(host, until_at) VALUES(?, ?) "
                           "ON CONFLICT(host) DO UPDATE SET until_at=CASE WHEN excluded.until_at>remote_host_backoff.until_at "
                           "THEN excluded.until_at ELSE remote_host_backoff.until_at END", (row["host"], next_due))

    def stats(self, now: str) -> dict:
        with self.store.db.read() as tx:
            row = tx.one(
                "SELECT COUNT(*) AS scheduled, SUM(CASE WHEN s.next_due<=? THEN 1 ELSE 0 END) AS overdue, "
                "SUM(CASE WHEN s.hot_until>? THEN 1 ELSE 0 END) AS frequent, "
                "MAX(s.last_gap_s) AS max_gap, AVG(s.last_gap_s) AS mean_gap "
                "FROM remote_schedule s JOIN targets t ON t.id=s.target_id WHERE t.delisted=0 AND t.skip_reason IS NULL",
                (now, now),
            )
        return {"scheduled": int(row["scheduled"]), "overdue": int(row["overdue"] or 0),
                "frequent": int(row["frequent"] or 0), "lastGapMaxSeconds": row["max_gap"],
                "lastGapMeanSeconds": row["mean_gap"]}
