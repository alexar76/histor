"""Wiring: one place that builds every component from settings, for the app, the CLI and tests."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from awr import SigningKey

from histor.check import Checker
from histor.classifier import Classifier
from histor.config import Settings
from histor.crawler import Crawler, CrawlInProgress
from histor.db import Backend, open_backend
from histor.keys import bind_key_to_log, issuer_key
from histor.labels import LabelIssuer
from histor.logbook import Logbook
from histor.migrations import apply_migrations
from histor.scanner import Scanner
from histor.signing import ProviderSigner
from histor.store import Store

log = logging.getLogger("histor")


@dataclass
class Services:
    settings: Settings
    backend: Backend
    store: Store
    key: SigningKey
    issuer: LabelIssuer
    logbook: Logbook
    scanner: Scanner
    crawler: Crawler
    checker: Checker
    provider: ProviderSigner
    stop: threading.Event = field(default_factory=threading.Event)
    last_error: str | None = None

    def close(self) -> None:
        self.stop.set()
        self.backend.close()


def build(settings: Settings, *, crawler_kwargs: dict | None = None, backend: Backend | None = None,
          serving: bool = False) -> Services:
    """Wire everything. ``serving`` is for the one long-lived process: only it may declare an
    unfinished run dead — a CLI command next to a live service must not (audit F38)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backend = backend or open_backend(settings.db_path, settings.database_url)
    apply_migrations(backend)
    store = Store(backend)
    if serving:
        store.mark_interrupted_runs(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    # A missing key is created only for an empty log; otherwise it is an operator's mistake to fix.
    key = issuer_key(settings.issuer_key_path, create=store.tree_size() == 0)
    bind_key_to_log(store, key, settings.head_marker_path)
    issuer = LabelIssuer(key)
    logbook = Logbook(store, key, settings.head_marker_path)
    scanner = Scanner(settings.scanner_dir, settings.node_bin)
    kwargs = dict(crawler_kwargs or {})
    if settings.classifier_enabled and "classifier" not in kwargs:
        kwargs["classifier"] = Classifier(
            model=settings.classifier_model, api_key=settings.classifier_api_key,
            base_url=settings.classifier_base_url, timeout_s=settings.classifier_timeout_s,
            max_tools=settings.classifier_max_tools,
        )
    crawler = Crawler(settings, store, issuer, logbook, scanner, **kwargs)
    checker = Checker(store, key, scanner, settings.public_base,
                      classifier_model=settings.classifier_model if settings.classifier_enabled else "",
                      classifier_enabled=settings.classifier_enabled)
    provider = ProviderSigner(settings.provider_key_path, pqc=settings.pqc)
    return Services(settings, backend, store, key, issuer, logbook, scanner, crawler, checker, provider)


def crawl_in_background(services: Services) -> bool:
    """Start one crawl on a thread. False when one is already running."""
    if services.crawler.running_since is not None:
        return False

    def work() -> None:
        try:
            stats = services.crawler.run()
            services.last_error = None
            log.warning("crawl finished: %s", {k: stats.get(k) for k in ("attempted", "changes", "tree_size")})
        except CrawlInProgress:
            pass
        except Exception as exc:  # noqa: BLE001 - the scheduler must survive one bad crawl
            services.last_error = f"{type(exc).__name__}: {exc}"[:500]
            log.exception("crawl failed")

    threading.Thread(target=work, name="histor-crawl", daemon=True).start()
    return True


def scheduler(services: Services, *, first_wait: float = 15.0, tick: float = 300.0) -> None:
    """Crawl every ``crawl_interval_s``, counted from the start of the last SUCCESSFUL crawl.

    A crawl that failed (registry down, crash) or was interrupted by a restart is retried after at
    most an hour instead of waiting a full interval — otherwise one bad night costs a day of
    history. With ``HISTOR_CRAWL_ON_START=0`` a fresh instance waits one interval before its first
    automatic crawl; an operator can still trigger one by hand.
    """
    interval = services.settings.crawl_interval_s
    if interval <= 0:
        return
    retry = min(interval, 3600)
    booted = datetime.now(UTC)

    def parse(ts: str) -> datetime:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    def due() -> bool:
        now = datetime.now(UTC)
        ok = services.store.last_successful_run()
        last = services.store.last_run(finished_only=True)
        if last and last["stats"].get("error") and (ok is None or last["id"] > ok["id"]):
            return (now - parse(last["finished_at"])).total_seconds() >= retry
        if ok:
            return (now - parse(ok["started_at"])).total_seconds() >= interval
        if services.settings.crawl_on_start:
            return True
        return (now - booted).total_seconds() >= interval

    if services.stop.wait(first_wait):
        return
    while True:
        try:
            if due():
                crawl_in_background(services)
        except Exception:  # noqa: BLE001 - a transient DB error must not end the schedule
            log.exception("scheduler tick failed")
        if services.stop.wait(tick):
            return
