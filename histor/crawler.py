"""One crawl: harvest the registry, observe every endpoint, issue labels, sign a tree head.

What gets a label, and when — chosen so the log records facts, not noise:

* **observation, pattern scan, name threat** — once per *new subject*, i.e. the first time a
  target advertises a given tool set. Re-issuing identical deterministic results daily would
  only bury the changes. A new WARDEN pattern set or record set re-issues the scan labels,
  because two scan labels are comparable only under the same set (MTL/1 section 7.3) — once per
  target, tracked in ``targets.scan_sets``, so an interrupted crawl neither skips nor repeats one.
* **continuity** — every crawl (at most once per ``CONTINUITY_MIN_INTERVAL_S``) for a target
  with a prior label. ``pass`` is the heartbeat behind "unchanged since"; ``fail`` is a change.
* **an unreachable server** gets one ``inconclusive`` continuity label when it stops answering,
  not one a day — and never a ``fail``: a server we cannot read has not been shown to change.

Endpoints are observed, scanned and committed in batches of ``crawl_batch``: a full crawl takes
one to two hours, and holding every observation until the end would lose all of it to one
restart and show no progress while it ran. The tree head is signed once, at the end.

The subject descriptor omits ``server.version`` on purpose (it is OPTIONAL in section 4.2): a
registry version bump with identical tools would otherwise change the subject digest, and the
continuity label would say "definitions changed" about definitions that did not.
"""

from __future__ import annotations

import json
import re
import logging
import threading
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from histor import diffing
from histor.classifier import Classifier, ClassifierError
from histor.config import Settings
from histor.declared import DeclarationError, DeclarationFetcher
from histor.exit_reasons import NODE_RUNTIMES, classify, excerpt
from histor.labels import LabelIssuer
from histor.logbook import Logbook, utcnow
from histor.mcpclient import McpReader, Observation
from histor.packages import (
    SANDBOX_ERROR,
    PackageReader,
    SandboxPool,
    parse_package,
    sandbox_readers,
    version_signals,
)
from histor.registry import Target, curated_rank, curated_targets, harvest, load_opt_out, with_curated
from histor.scanner import RuleSets, Scanner, ScannerError
from histor.store import Store, dumps
from histor.subject import MTL_SUBJ_001, REGISTRY_CURATED, MtlError, Subject, build_subject, fallback_subject
from histor.untrusted import clean_text

CONTINUITY_MIN_INTERVAL_S = 20 * 3600
log = logging.getLogger("histor.crawler")


class CrawlInProgress(RuntimeError):
    pass


@dataclass
class Prepared:
    target: Target
    obs: Observation
    observed_at: str
    subject: Subject | None = None
    subject_error: MtlError | None = None
    # A package observation: the registry's facts about the version and what changed since the last one.
    signals: dict[str, Any] | None = None


PACKAGE_SANDBOX = "package-sandbox"


def _host_of(endpoint: str) -> str:
    if parse_package(endpoint):
        # Every package goes through the sandbox pool, bounded by its total slots.
        return PACKAGE_SANDBOX
    try:
        return (urlsplit(endpoint).hostname or "").lower()
    except ValueError:  # an unparseable registry URL; netguard refuses it with a status later
        return ""


def _observer_of(record: str | None) -> str:
    try:
        return str(json.loads(record).get("observer") or "") if record else ""
    except (ValueError, AttributeError):
        return ""


def _artifact(t: Target) -> dict[str, str]:
    """The subject descriptor's artifact members: what HISTOR connected to."""
    if parse_package(t.endpoint):
        return {"transport": "stdio", "package": t.endpoint}
    return {"transport": "streamable-http", "endpoint": t.endpoint}


def _interleave_by_host(targets: list[Target]) -> list[Target]:
    """Round-robin across hosts, so one host with 300 endpoints is not hit 12 at a time."""
    queues: dict[str, deque[Target]] = defaultdict(deque)
    for t in targets:
        queues[_host_of(t.endpoint)].append(t)
    out: list[Target] = []
    while queues:
        for host in list(queues):
            out.append(queues[host].popleft())
            if not queues[host]:
                del queues[host]
    return out


# A run that ended this way ends the same way next time unless something about it changes: a new
# version, a new observer, or configuration the package asked for that it was not given yet.
SETTLED = frozenset({"exited", "no-entry-point", "no-wheel", "not-found", "protocol"})
# Failures a newer observer may start where an older one could not (observer 4: the Python a
# package requires, ``python -m``, the mcp-named command, a passwd entry, a writable package):
# run again ahead of the never-observed queue, with at least half of what is left of a crawl.
RENEWABLE = frozenset({"exited", "no-entry-point", "install-failed", "timeout", "protocol"})
# The sandbox's trace reader (behaviour.observer). gvisor-trace/2 listed the byte asyncio writes
# to its own socketpair as a send it could not place, which read as "reached out at startup" for
# nearly every Python server; a package it left with such sends is traced again, once.
TRACE_OBSERVER = "gvisor-trace/8"
UNPLACED_SEND = ("sendto(", "sendmsg(", "sendmmsg(")
# Evidence that cannot be this run's: another run's canary recipient, or a name only MOMUS's
# red-team fixtures use. gvisor-trace/3 and older matched trap records by address from before
# the container started, and once took in the previous holder's requests.
FOREIGN_CANARY = re.compile(r"^canary-[0-9a-f]{12}@histor-trap\.invalid$")


def _unplaced_sends(behaviour: Any) -> bool:
    """An older reader's trace that holds what gvisor-trace/3 reads differently: a send it could
    not place (asyncio's self-pipe), or a write it could not resolve — a relative path it
    counted as outside the sandbox, which WARDEN shows as persistence (node-gyp's
    buildcheck.gypi, a package's own cache)."""
    if not isinstance(behaviour, dict) or behaviour.get("observer") == TRACE_OBSERVER:
        return False
    phases = [behaviour[p] for p in ("installScripts", "startup", "calls") if isinstance(behaviour.get(p), dict)]
    foreign = any(isinstance(e, str) and FOREIGN_CANARY.match(e) for ph in phases for e in ph.get("unexpectedRecipients") or [])
    if behaviour.get("observer") == "gvisor-trace/7":
        # 8 reads only one thing differently: a trap record carrying another run's tag (a connection the
        # address's previous holder queued). A 7 trace is traced again only when it shows one.
        return foreign
    return (foreign or any(isinstance(e, str) and e.startswith(UNPLACED_SEND) for ph in phases for e in ph.get("network") or [])
            or any(isinstance(e, str) and not e.startswith("/") for ph in phases for e in ph.get("writes") or [])
            # gvisor-trace/4 read the container's start after removing it, so every run said so.
            or any("start-time-unknown" in (ph.get("incompleteReasons") or []) for ph in phases)
            or any(isinstance(e, str) and "momus-trap.invalid" in e for ph in phases
                   for e in [*(ph.get("lookups") or []), *(str(r.get("destination")) for r in ph.get("requests") or [] if isinstance(r, dict))]))
# A PyPI server written for MCP SDK 1.x crashes on import once pip resolves mcp 2.x. Its status
# stays what a fresh install gets; a second run with this pin shows what it was before 2.0, kept
# beside the record (``legacy``) and never in the label chain.
LEGACY_PINS = {"sdk-incompatible": ["mcp<2"]}


def _config_for(t: Target, record: dict[str, Any]) -> dict[str, Any]:
    """Variable names and start arguments to start a package with: what its registry entry
    declares, plus what it named when it last exited without them."""
    declared = t.needs or {}
    asked = record.get("exit") if isinstance(record.get("exit"), dict) else {}
    # What the last run was given goes on: a new version of a package that started with a
    # placeholder key needs it too, and a name asked for later adds to the ones already given.
    given = record.get("supplied") if isinstance(record.get("supplied"), dict) else {}
    env = sorted({str(n) for n in [*(declared.get("env") or []), *(asked.get("env") or []), *(given.get("env") or [])]})[:8]
    args = list(declared.get("args") or asked.get("args") or given.get("args") or [])[:6]
    plan: dict[str, Any] = {k: v for k, v in (("env", env), ("args", args)) if v}
    if given.get("runtime") in NODE_RUNTIMES.values() and t.endpoint.startswith("npm:"):
        plan["runtime"] = given["runtime"]
    # "Node 24+ required": the current LTS image, the one such a package's users run it on.
    major = asked.get("node") if asked.get("kind") == "needs-newer-node" else None
    if isinstance(major, int) and t.endpoint.startswith("npm:"):
        runtime = next((name for m, name in sorted(NODE_RUNTIMES.items()) if m >= major), None)
        if runtime:
            plan["runtime"] = runtime
    return plan


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass
class _Tally:
    """A crawl's counts, shared by the endpoint batches and the package lane."""

    classify_budget: int
    statuses: Counter[str] = field(default_factory=Counter)
    issued: Counter[str] = field(default_factory=Counter)
    internal: Counter[str] = field(default_factory=Counter)
    legacy_statuses: Counter[str] = field(default_factory=Counter)
    changes: int = 0
    classified: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    classify_lock: threading.Lock = field(default_factory=threading.Lock)


class Crawler:
    def __init__(self, settings: Settings, store: Store, issuer: LabelIssuer, logbook: Logbook,
                 scanner: Scanner, *, reader: McpReader | None = None,
                 harvest_fn: Callable[[str], list[Target]] | None = None,
                 classifier: Classifier | None = None,
                 packages: PackageReader | SandboxPool | None = None,
                 declarations: DeclarationFetcher | None = None,
                 clock: Callable[[], str] = utcnow) -> None:
        self.settings = settings
        self.store = store
        self.issuer = issuer
        self.logbook = logbook
        self.scanner = scanner
        self.classifier = classifier
        self.reader = reader or McpReader(timeout=settings.crawl_timeout_s,
                                          allow_private=settings.allow_private_targets)
        if packages is None and settings.sandbox_enabled:
            readers = sandbox_readers(settings)
            packages = SandboxPool(readers, max_slots=settings.sandbox_max_slots) if len(readers) > 1 else readers[0][0]
        self.packages = packages
        self.declarations = declarations or DeclarationFetcher(allow_private=settings.allow_private_targets)
        # The crawl's sync and the hourly refresh both write the declared sets: one at a time.
        self._declarations_lock = threading.Lock()
        self._due_versions: dict[str, str] = {}
        self._due_config: dict[str, dict[str, list[str]]] = {}
        self._due_legacy: set[str] = set()
        self._seen_versions: dict[str, str] = {}
        self.harvest_fn = harvest_fn or self._harvest_with_curated
        self.clock = clock
        self._lock = threading.Lock()
        self.running_since: str | None = None
        self.progress: dict[str, int] | None = None

    def _harvest_with_curated(self, url: str) -> list[Target]:
        opt_out = load_opt_out(self.settings.opt_out, self.settings.opt_out_path)
        listed = harvest(url, allow_cleartext=self.settings.allow_private_targets, opt_out=opt_out,
                         observe_packages=self.packages is not None)
        return with_curated(listed, curated_targets(opt_out=opt_out, observe_packages=self.packages is not None))

    # -- orchestration ----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise CrawlInProgress("a crawl is already running")
        try:
            self.running_since = self.clock()
            self.progress = None
            return self._run()
        finally:
            self.running_since = None
            self.progress = None
            # The registry ETags this crawl learned, so the next one (or the next process) asks
            # with If-None-Match instead of downloading every unchanged package again.
            save = getattr(self.packages, "save_versions", None)
            if save is not None:
                save()
            self._lock.release()

    def _run(self) -> dict[str, Any]:
        started = self.clock()
        with self.store.db.transaction() as tx:
            run_id = int(tx.one("INSERT INTO runs(started_at) VALUES(?) RETURNING id", (started,))["id"])
        stats: dict[str, Any] = {"started_at": started}
        try:
            targets = self.harvest_fn(self.settings.registry_url)
            official = [t for t in targets if t.registry != REGISTRY_CURATED]
            curated = [t for t in targets if t.registry == REGISTRY_CURATED]
            is_package = lambda t: bool(parse_package(t.endpoint)) or t.transport == "stdio"  # noqa: E731
            stats["registry_servers"] = len({t.name for t in official})
            stats["registry_endpoints"] = sum(1 for t in official if not is_package(t))
            stats["registry_packages"] = sum(1 for t in official if is_package(t))
            stats["curated_endpoints"] = sum(1 for t in curated if not is_package(t))
            stats["curated_packages"] = sum(1 for t in curated if is_package(t))
            stats["not_attempted"] = dict(Counter(t.skip_reason for t in targets if t.skip_reason))
            files = self._read_declaration_files(
                {t.declared_url for t in targets if t.declared_url and t.registry != REGISTRY_CURATED})
            declared = self._sync_targets(targets, started, files)
            if declared["servers"] or declared["withdrawn"] or declared.get("ignored"):
                stats["declared_tool_sets"] = declared

            rulesets = self.scanner.rulesets()
            sets = self._register_rulesets(rulesets)

            dialable = [t for t in targets if not t.skip_reason]
            if self.settings.crawl_limit:
                dialable = dialable[: self.settings.crawl_limit]
            packages = [t for t in dialable if parse_package(t.endpoint)]
            if packages:
                due = self._packages_due(packages, stats)
                dialable = [t for t in dialable if not parse_package(t.endpoint)] + due
            # Packages run in their own lane, started now: the sandboxes work from the first minute
            # instead of after a round over 15 000 endpoint hosts, and each result is committed as
            # soon as it is in, not when the slowest member of a 400-target batch is.
            lane_targets = [t for t in dialable if parse_package(t.endpoint)]
            endpoints = [t for t in dialable if not parse_package(t.endpoint)]
            # Interleave once over the whole list, so every batch carries its share of each host
            # instead of one batch spending an hour on a single host's 2 000 endpoints.
            endpoints = _interleave_by_host(endpoints)
            stats["attempted"] = len(endpoints) + len(lane_targets)
            tally = _Tally(self.settings.classifier_max_per_crawl if self.classifier else 0)
            step = self.settings.crawl_batch
            take_counts = getattr(self.packages, "take_counts", None)
            if take_counts:
                take_counts()  # count this crawl only
            self._lane_done = 0
            self._endpoints_done = 0
            total = len(endpoints) + len(lane_targets)
            lane_error: list[BaseException] = []
            lane = None
            if lane_targets:
                def run_lane() -> None:
                    try:
                        self._package_lane(lane_targets, run_id, rulesets, sets, tally, total)
                    except BaseException as exc:  # noqa: BLE001 - surfaced by the crawl below
                        lane_error.append(exc)
                lane = threading.Thread(target=run_lane, name="histor-package-lane", daemon=True)
                lane.start()
            for start in range(0, len(endpoints), step):
                prepared = self._observe(endpoints[start : start + step])
                scans, scan_failed = self._scan(prepared, sets)
                with tally.classify_lock:
                    classifications, tally.classify_budget = self._classify(prepared, tally.classify_budget)
                for item in prepared:
                    self._settle(item, run_id, rulesets, scans, scan_failed, sets, classifications, tally)
                with tally.lock:
                    self._endpoints_done = start + len(prepared)
                    self.progress = {"done": self._endpoints_done + self._lane_done, "of": total,
                                     **({"packages": {"done": self._lane_done, "of": len(lane_targets)}} if lane_targets else {})}
            if lane is not None:
                lane.join()
            if lane_error:
                raise lane_error[0]
            self.progress = {"done": total, "of": total}
            statuses, issued, internal, legacy_statuses = tally.statuses, tally.issued, tally.internal, tally.legacy_statuses
            changes, classified = tally.changes, tally.classified
            if internal:
                stats["internal_errors"] = dict(internal)
            if legacy_statuses:
                stats["legacy_statuses"] = dict(legacy_statuses)
            if take_counts:
                stats["sandbox_hosts"] = take_counts()
            stats["statuses"] = dict(statuses)
            stats["labels_issued"] = dict(issued)
            stats["changes"] = changes
            if self.classifier:
                stats["classified"] = classified
                stats["classifier_model"] = self.classifier.model
            sth = self.logbook.publish_sth()
            cutoff = (datetime.now(UTC) - timedelta(days=90)).strftime("%Y-%m-%d")
            stats["client_reports_pruned"] = self.store.prune_client_reports(cutoff)
            stats["tree_size"] = sth["treeSize"] if sth else 0
        except Exception as exc:
            stats["error"] = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            stats["finished_at"] = self.clock()
            with self.store.db.transaction() as tx:
                tx.execute("UPDATE runs SET finished_at=?, stats=? WHERE id=?", (stats["finished_at"], dumps(stats), run_id))
            self.store.db.maintenance()
        return stats

    def _read_declaration_files(self, urls: set[str]) -> dict[str, tuple[list[dict[str, Any]] | None, str | None]]:
        """url → (sets, None) or (None, why it could not be read). Read before any transaction opens:
        a slow publisher site must never hold the database."""
        def one(url: str) -> tuple[str, tuple[list[dict[str, Any]] | None, str | None]]:
            try:
                sets, _ignored = self.declarations.fetch(url)
                return url, (sets, None)
            except DeclarationError as exc:
                return url, (None, str(exc)[:300])
            except Exception as exc:  # noqa: BLE001 - one publisher's file must not stop a crawl
                return url, (None, f"internal: {type(exc).__name__}")
        if not urls:
            return {}
        with ThreadPoolExecutor(max_workers=min(8, len(urls)), thread_name_prefix="histor-declared") as pool:
            return dict(pool.map(one, sorted(urls)))

    def refresh_declarations(self) -> dict[str, Any]:
        """Read every declaration file again, between crawls: a publisher who ships a runner adds its
        set to the file, and HISTOR should know within the hour, not the next day."""
        rows = self.store.declaration_files()
        files = self._read_declaration_files({r["url"] for r in rows})
        with self._declarations_lock:
            return self.store.refresh_declarations(rows, files, self.clock())

    def _sync_targets(self, targets: list[Target], now: str,
                      files: dict[str, tuple[list[dict[str, Any]] | None, str | None]] | None = None) -> dict[str, Any]:
        with self._declarations_lock, self.store.db.transaction() as tx:
            for t in targets:
                tx.execute(
                    "INSERT INTO targets(id, name, endpoint, transport, registry, title, description, version, "
                    "repository, website, first_listed, last_listed, delisted, skip_reason) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
                    "ON CONFLICT(id) DO UPDATE SET transport=excluded.transport, title=excluded.title, "
                    "description=excluded.description, version=excluded.version, repository=excluded.repository, "
                    "website=excluded.website, last_listed=excluded.last_listed, delisted=0, "
                    "skip_reason=excluded.skip_reason",
                    (t.id, t.name, t.endpoint, t.transport, t.registry, t.title, t.description, t.version,
                     t.repository, t.website, now, now, t.skip_reason),
                )
            # Only after a successful harvest: a registry outage must not delist everything.
            tx.execute("UPDATE targets SET delisted=1 WHERE last_listed < ?", (now,))
            # Curated targets have no registry entry, so nothing of theirs is declared.
            return Store.sync_declarations(tx, [t for t in targets if t.registry != REGISTRY_CURATED], now, files)

    def reobserve(self, keys: list[str]) -> list[dict[str, Any]]:
        """Observe the named targets again now (``histor reobserve``), by endpoint or target id,
        through the path a crawl takes: the sandbox (or the MCP client), WARDEN, the commit, the
        label chain and a security event. Nothing is edited by hand, and the old observation
        stays in the history beneath the new one.

        It belongs to the crawl in progress, or else to the last one: a run row of its own would
        become the public "last run" with one target in it. A package is given what its record
        says it asked for (placeholders, start argument, runtime); its registry entry is not
        re-read. Written after a red-team request was credited to a real package on 2026-10-13
        and the only fix to hand was waiting for a crawl to reach it."""
        rows: list[dict[str, Any]] = []
        for key in keys:
            found = self.store.targets_by(endpoint=key) or [r for r in [self.store.target(key)] if r]
            if not found:
                raise KeyError(f"no target with endpoint or id {key!r}")
            rows.extend(r for r in found if r["id"] not in {x["id"] for x in rows})
        run = self.store.last_run(finished_only=False)
        if run is None:
            raise RuntimeError("no crawl has run yet: there is no run to attach an observation to")
        targets = [Target(id=r["id"], name=r["name"], endpoint=r["endpoint"], transport=r["transport"],
                          registry=r["registry"], title=r.get("title"), description=r.get("description"),
                          version=r.get("version"), repository=r.get("repository"), website=r.get("website"),
                          skip_reason=None) for r in rows]
        before = {r["id"]: (r.get("last_status"), r.get("last_attempt")) for r in rows}
        rulesets = self.scanner.rulesets()
        sets = self._register_rulesets(rulesets)
        tally = _Tally(0)
        self._endpoints_done = self._lane_done = 0
        packages = [t for t in targets if parse_package(t.endpoint)]
        endpoints = [t for t in targets if not parse_package(t.endpoint)]
        if packages:
            if self.packages is None:
                raise RuntimeError("no package sandbox is configured")
            states = self.store.package_states()
            self._due_versions, self._due_config, self._due_legacy, self._seen_versions = {}, {}, set(), {}
            for t in packages:
                registry, name = parse_package(t.endpoint) or ("", "")
                self._due_versions[t.id] = self.packages.latest_version(registry, name)
                state = states.get(t.id) or {}
                signals = state.get("package_signals") or {}
                if isinstance(signals, str):
                    try:
                        signals = json.loads(signals)
                    except ValueError:
                        signals = {}
                plan = _config_for(t, signals if isinstance(signals, dict) else {})
                if plan:
                    self._due_config[t.id] = plan
                if state.get("package_version"):
                    self._seen_versions[t.id] = state["package_version"]
            self._package_lane(packages, int(run["id"]), rulesets, sets, tally, len(packages))
        if endpoints:
            prepared = self._observe(endpoints)
            scans, scan_failed = self._scan(prepared, sets)
            for item in prepared:
                self._settle(item, int(run["id"]), rulesets, scans, scan_failed, sets, {}, tally)
        out = []
        for t in targets:
            after = self.store.target(t.id) or {}
            signals = after.get("package_signals")
            try:
                behaviour = (json.loads(signals) if isinstance(signals, str) else signals or {}).get("behaviour") or {}
            except ValueError:
                behaviour = {}
            out.append({"endpoint": t.endpoint, "id": t.id, "run": int(run["id"]),
                        "before": {"status": before[t.id][0], "at": before[t.id][1]},
                        "after": {"status": after.get("last_status"), "at": after.get("last_attempt"),
                                  "tracer": behaviour.get("observer")}})
        return out

    def _register_rulesets(self, rulesets: RuleSets) -> str:
        """Publish both sets by digest; return the pair every target's scan labels are compared with."""
        with self.store.db.transaction() as tx:
            Store.put_blob(tx, rulesets.pattern_set_digest, "pattern-set", rulesets.pattern_set)
            Store.put_blob(tx, rulesets.record_set_digest, "record-set", rulesets.record_set)
            Store.set_meta(tx, "pattern_set", rulesets.pattern_set_digest)
            Store.set_meta(tx, "record_set", rulesets.record_set_digest)
            Store.set_meta(tx, "warden_package", rulesets.warden_package)
        return f"{rulesets.pattern_set_digest}|{rulesets.record_set_digest}"

    def _packages_due(self, targets: list[Target], stats: dict[str, Any]) -> list[Target]:
        """The packages to run in the sandbox this crawl: a version not observed yet.

        A published npm or PyPI version cannot change, so an observed version is not run again
        (unless its run timed out). New versions of packages HISTOR already knows go first — that
        is where a changed tool description reaches everyone running the package unpinned — then
        packages never observed, up to ``sandbox_max_per_crawl``; the rest wait for the next crawl.
        """
        assert self.packages is not None
        reader = self.packages
        cap = self.settings.sandbox_max_per_crawl
        unknown_by: Counter[str] = Counter()

        def latest(t: Target) -> tuple[Target, str | None]:
            registry, name = parse_package(t.endpoint) or ("", "")
            try:
                version = reader.latest_version(registry, name)
            except Exception:  # noqa: BLE001 - a registry hiccup skips this package for one crawl
                version = None
            if version is None:
                unknown_by[registry] += 1
            return t, version

        def lookup(batch: list[Target]) -> list[tuple[Target, str | None]]:
            with ThreadPoolExecutor(max_workers=16) as pool:
                return list(pool.map(latest, batch))

        clock = time.monotonic()
        known_states = self.store.package_states()
        states = {t.id: known_states.get(t.id) or {} for t in targets}
        state_seconds = round(time.monotonic() - clock)
        attempted_at = {t.id: str(states[t.id].get("last_attempt") or "") for t in targets}
        known = [t for t in targets if states[t.id].get("package_version")]
        never = [t for t in targets if not states[t.id].get("package_version")]
        updates, first = [], []
        # The same version again with what it asked for (a placeholder key, a start argument, mcp<2):
        # a known, finite set, run right after new versions — behind 40 000 never-observed
        # packages it would wait for days.
        configured: list[tuple[Target, str]] = []
        seen_by_id: dict[str, str] = {}
        config: dict[str, dict[str, list[str]]] = {}
        unchanged = refresh = outdated = incomplete_retries = with_config = settled = with_pins = retraced = 0
        current = self.settings.sandbox_observer
        # An ok, complete observation by an observer listed here stands: what changed since is how a
        # failing package is started, not what a running one reports.
        still_valid = {current, *self.settings.sandbox_observers_valid}
        renewed: list[tuple[Target, str]] = []
        # A record an older trace reader got wrong is a public mistake about someone else's
        # package: it goes ahead of everything but new versions.
        retrace: list[tuple[Target, str]] = []
        legacy_ids: set[str] = set()
        # A package observed before is looked up every crawl: a new version of it goes first.
        clock = time.monotonic()
        known_versions = lookup(known)
        known_seconds = round(time.monotonic() - clock)
        clock = time.monotonic()
        for t, version in known_versions:
            if version is None:
                continue
            state = states[t.id]
            seen = state.get("package_version")
            seen_by_id[t.id] = seen
            observer = _observer_of(state.get("package_signals"))
            signals = state.get("package_signals") or {}
            if isinstance(signals, str):
                try:
                    signals = json.loads(signals)
                except ValueError:
                    signals = {}
            if not isinstance(signals, dict):
                signals = {}
            if not isinstance(signals.get("exit"), dict) and state.get("last_status") == "exited":
                # Recorded before exits were read: read the stored text now, so a package that has been
                # asking for a key (or for mcp<2, or Node 24) since then is rerun with it, not settled.
                reason = classify("exited", state.get("last_detail"))
                if reason:
                    signals = {**signals, "exit": reason}
            complete = (signals.get("behaviour") or {}).get("complete") is True
            if seen == version and _unplaced_sends(signals.get("behaviour")):
                # Traced again with what started it the last time (a placeholder key, a start
                # argument): without it a server that needs a key exits, and a page that was right
                # about its tools turns into a failure. Whatever the run's status: a package that
                # times out or exits still shows what it did before, and until 10-14 only "ok" pages
                # were re-read — 31 failing ones kept 170 sends gvisor-trace/2 could not place.
                plan = _config_for(t, signals)
                if plan:
                    config[t.id] = plan
                retraced += 1
                retrace.append((t, version))
                continue
            if seen == version and state.get("last_status") == "ok" and complete and observer in still_valid:
                unchanged += 1
                continue
            exit_kind = (signals.get("exit") or {}).get("kind") if isinstance(signals.get("exit"), dict) else None
            if (seen == version and state.get("last_status") == "exited" and exit_kind in LEGACY_PINS
                    and observer in still_valid and t.endpoint.startswith("pypi:")):
                legacy = signals.get("legacy") if isinstance(signals.get("legacy"), dict) else {}
                lplan = {"pins": LEGACY_PINS[exit_kind], **_config_for(t, legacy if legacy.get("version") == version else {})}
                if legacy.get("version") == version and lplan == (legacy.get("supplied") or {}):
                    settled += 1  # the pinned run happened with everything it asked for
                    continue
                config[t.id] = lplan
                legacy_ids.add(t.id)
                with_pins += 1
                configured.append((t, version))
                continue
            plan = _config_for(t, signals)
            if plan:
                config[t.id] = plan
            if seen == version and state.get("last_status") in SETTLED and observer == current:
                if not plan or plan == (signals.get("supplied") or {}):
                    settled += 1  # nothing new to try until the package or the observer changes
                    continue
                with_config += 1
                configured.append((t, version))
                continue
            if seen == version:
                refresh += 1
                if observer != current:
                    outdated += 1
                    if state.get("last_status") in RENEWABLE:
                        renewed.append((t, version))
                        continue
                else:
                    incomplete_retries += 1
                first.append((t, version))
            else:
                updates.append((t, version))
        # Never-observed packages in curated order (most downloaded first), wherever they are listed:
        # what people actually run is covered before the long tail of the registry.
        try:
            rank = curated_rank()
        except (OSError, ValueError):
            rank = {}
        # Never let a permanently incomplete popular package occupy the budget every crawl.
        # Unobserved entries come first; retries rotate by oldest attempt, with popularity as a tie-breaker.
        order = sorted([(t, v) for t, v in first] + [(t, None) for t in never],
                       key=lambda tv: (attempted_at[tv[0].id], rank.get(tv[0].endpoint, len(rank))))
        # A never-observed package is not asked about at all: the sandbox installs the latest version
        # and reports which one it ran. Asking first cost ~16 minutes of npm's pace per crawl, every
        # crawl, while the sandboxes waited (and a burst of it once got npm to refuse us).
        configured.sort(key=lambda tv: rank.get(tv[0].endpoint, len(rank)))
        retrace.sort(key=lambda tv: rank.get(tv[0].endpoint, len(rank)))
        retrace = retrace[: max(0, cap - len(updates))]
        configured = configured[: max(0, cap - len(updates) - len(retrace))]
        left = max(0, cap - len(updates) - len(retrace) - len(configured))
        renewable = len(renewed)
        renewed.sort(key=lambda tv: rank.get(tv[0].endpoint, len(rank)))
        renewed = renewed[: min(renewable, max(left // 2, left - len(order)))]
        room = left - len(renewed)
        chosen: list[tuple[Target, str | None]] = [(t, v) for t, v in order[:room]]
        looked_up = 0
        never_seconds = round(time.monotonic() - clock)
        for t in never:
            if t.needs:
                config[t.id] = _config_for(t, {})
        due = (updates + retrace + configured + renewed + chosen)[:cap]
        unknown = sum(unknown_by.values())
        if unknown:
            log.warning("package versions unknown this crawl: %s", dict(unknown_by))
        self._due_versions = {t.id: v for t, v in due}
        self._due_config = {t.id: config[t.id] for t, _ in due if config.get(t.id)}
        self._due_legacy = {t.id for t, _ in due if t.id in legacy_ids}
        self._seen_versions = {t.id: seen_by_id[t.id] for t, _ in due if seen_by_id.get(t.id)}
        stats["packages"] = {"dialable": len(targets), "due": len(due), "newVersions": len(updates),
                             "neverObserved": len(never), "olderObserver": outdated, "incompleteRetries": incomplete_retries, "unchanged": unchanged,
                             "withConfig": with_config, "withPins": with_pins, "settled": settled,
                             "renewed": len(renewed), "renewable": renewable, "retraced": len(retrace), "retraceable": retraced,
                             "versionUnknown": unknown, "versionUnknownBy": dict(unknown_by),
                             "versionsAsked": len(known) + looked_up,
                             "lookupSeconds": {"state": state_seconds, "known": known_seconds, "neverObserved": never_seconds}}
        log.warning("package versions: state %d s, %d known in %d s, %d more in %d s; %d due", state_seconds,
                    len(known), known_seconds, looked_up, never_seconds, len(due))
        return [t for t, _ in due]

    def _signals(self, package: tuple[str, str], version: str, previous: str | None) -> dict[str, Any] | None:
        """Registry facts for the observed version and the one observed before it; None if the
        registry does not answer — the observation stands without them."""
        reader = self.packages
        facts = getattr(reader, "version_facts", None)
        if facts is None:
            return None
        try:
            current = facts(*package, version)
            prior = facts(*package, previous) if previous and previous != version else None
            return version_signals(current, prior)
        except Exception:  # noqa: BLE001 - metadata is a bonus; a registry hiccup must not cost the observation
            log.warning("registry facts unavailable for %s:%s@%s", package[0], package[1], version)
            return None

    def _observe(self, targets: list[Target]) -> list[Prepared]:
        per_host: dict[str, threading.Semaphore] = defaultdict(
            lambda: threading.Semaphore(self.settings.crawl_per_host)
        )
        per_host[PACKAGE_SANDBOX] = threading.Semaphore(getattr(self.packages, "capacity", self.settings.sandbox_concurrency))
        guard = threading.Lock()

        def one(t: Target) -> Prepared:
            return self._observe_one(t, per_host, guard)

        with ThreadPoolExecutor(max_workers=self.settings.crawl_workers) as pool:
            return list(pool.map(one, targets))

    def _observe_one(self, t: Target, per_host: Any, guard: threading.Lock) -> Prepared:
        with guard:
            sem = per_host[_host_of(t.endpoint)]
        package = parse_package(t.endpoint)
        try:
            with sem:
                if package and self.packages is not None:
                    obs = self.packages.observe(*package, self._due_versions.get(t.id), **self._due_config.get(t.id, {}))
                else:
                    obs = self.reader.list_tools(t.endpoint)
        except Exception as exc:  # noqa: BLE001 - the reader promises not to raise; belt and braces
            obs = Observation(status=SANDBOX_ERROR if package else "client-error", detail=type(exc).__name__)
        item = Prepared(target=t, obs=obs, observed_at=self.clock())
        if package and self.packages is not None and obs.status != SANDBOX_ERROR and obs.package_version:
            item.signals = self._signals(package, obs.package_version, self._seen_versions.get(t.id))
        if obs.status == "ok":
            try:
                item.subject = build_subject(server_name=t.name, registry=t.registry, tools=obs.tools or [],
                                             **_artifact(t))
            except MtlError as exc:
                item.subject_error = exc
            except Exception as exc:  # noqa: BLE001 - hostile input the canonicalizer chokes on
                item.subject_error = MtlError(MTL_SUBJ_001, f"the tool set cannot be processed ({type(exc).__name__})")
        return item

    def _settle(self, item: Prepared, run_id: int, rulesets: RuleSets, scans: dict[str, dict[str, Any]],
                scan_failed: set[str], sets: str, classifications: dict[str, dict[str, Any]], tally: _Tally) -> None:
        """Record one observation. One target must never take the crawl down: whatever a stranger's
        server made us do, it is recorded against that target and the crawl moves on."""
        with tally.lock:
            tally.statuses[item.obs.status] += 1
        if item.obs.status == SANDBOX_ERROR:
            # Our sandbox failed, not the package: no label may say the package did.
            with tally.lock:
                tally.internal["sandbox"] += 1
            self._commit_internal(item, run_id, item.obs.detail or "the package sandbox failed")
            return
        if item.target.id in self._due_legacy:
            with tally.lock:
                tally.legacy_statuses[item.obs.status] += 1
            self._commit_legacy(item, scans)
            return
        if item.target.id in scan_failed:
            with tally.lock:
                tally.internal["scan-failed"] += 1
            self._commit_internal(item, run_id, "the WARDEN sidecar failed on this tool set")
            return
        try:
            result = self._commit(item, run_id, rulesets, scans, sets, classifications)
        except Exception as exc:  # noqa: BLE001
            log.exception("commit failed for target %s", item.target.id)
            with tally.lock:
                tally.internal[type(exc).__name__] += 1
            self._commit_internal(item, run_id, f"internal error ({type(exc).__name__})")
            return
        with tally.lock:
            tally.issued.update(result["labels"])
            tally.changes += result["changed"]
            tally.classified += result.get("classified", 0)

    def _package_lane(self, targets: list[Target], run_id: int, rulesets: RuleSets, sets: str,
                      tally: _Tally, total: int) -> None:
        """Every due package, as many at once as the sandboxes have slots, each committed when done."""
        capacity = max(1, int(getattr(self.packages, "capacity", self.settings.sandbox_concurrency)))
        per_host: dict[str, threading.Semaphore] = {PACKAGE_SANDBOX: threading.Semaphore(capacity)}
        guard = threading.Lock()
        with ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="histor-sandbox") as pool:
            # A set we discard from, not a list we keep: every finished Future holds its result —
            # the tool set, the sandbox trace, the detail — and a list of all 8 000 kept every one
            # of them alive until the lane ended. The process grew to its 1 GiB limit and was
            # OOM-killed 26 h into crawl 33, near the end, losing the crawl (2026-10-12).
            pending = {pool.submit(self._observe_one, t, per_host, guard) for t in targets}
            for future in as_completed(pending):
                pending.discard(future)
                item = future.result()
                del future
                scans, scan_failed = self._scan([item], sets)
                with tally.classify_lock:
                    classifications, tally.classify_budget = self._classify([item], tally.classify_budget)
                self._settle(item, run_id, rulesets, scans, scan_failed, sets, classifications, tally)
                with tally.lock:
                    self._lane_done += 1
                    self.progress = {"done": self._endpoints_done + self._lane_done, "of": total,
                                     "packages": {"done": self._lane_done, "of": len(targets)}}

    def _scan(self, prepared: list[Prepared], sets: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Scan results by target id, and the targets whose scan failed.

        A sidecar failure on one tool set used to raise out of the batch and end the crawl. The
        batch is retried one set at a time, so only the sets the sidecar cannot handle are held
        back — committed as an internal error, and rescanned by the next crawl.
        """
        jobs = []
        for p in prepared:
            if p.subject is None or p.subject.tool_set_digest is None:
                continue
            state = self.store.target(p.target.id) or {}
            if p.subject.digest != state.get("current_subject") or state.get("scan_sets") != sets:
                jobs.append({
                    "id": p.target.id,
                    "server": {"id": p.target.name, "name": p.target.name, "url": p.target.endpoint},
                    "tools": p.subject.entries,
                })
        if not jobs:
            return {}, set()
        try:
            scans = self.scanner.scan(jobs)
        except ScannerError as exc:
            log.warning("WARDEN scan of a batch of %d failed (%s); retrying one by one", len(jobs), exc)
            scans = {}
            for job in jobs:
                try:
                    scans.update(self.scanner.scan([job]))
                except ScannerError:
                    pass
        return scans, {job["id"] for job in jobs if job["id"] not in scans}

    def _classify(self, prepared: list[Prepared], budget: int) -> tuple[dict[str, dict[str, Any]], int]:
        """Advisory classifier verdicts by target id, and the budget left.

        Runs OUTSIDE the write transaction (a network call must never hold the DB open) and only on a
        new/changed subject this model has not judged. A verdict already stored for the exact tool set
        and model — from another target sharing it, or an earlier crawl — is reused without a call, so
        the budget buys distinct tool sets, not duplicate work. Any failure skips that target.
        """
        # Note: an exhausted budget does NOT stop the pass — reuse of an already-stored verdict is
        # free and must keep working, or targets sharing a judged tool set would render "not
        # classified" forever. Only the paid classify() call below is gated on the budget.
        if not self.classifier:
            return {}, budget
        model = self.classifier.model
        out: dict[str, dict[str, Any]] = {}
        # Tool sets already judged in THIS pass: commits happen after the whole batch, so a fresh
        # verdict's stored row is not visible yet. A same-batch reuse carries no `stored` flag — its
        # row only exists once the holder commits, so the sibling waits for the next crawl to claim
        # the badge rather than pointing at a row that a failed commit never wrote.
        seen: dict[str, int] = {}
        for p in prepared:
            if p.subject is None or p.subject.tool_set_digest is None or p.subject.entries is None:
                continue
            state = self.store.target(p.target.id) or {}
            new_subject = p.subject.digest != state.get("current_subject")
            if not new_subject and state.get("classifier_model") == model:
                continue
            digest = p.subject.tool_set_digest
            existing = self.store.classification(digest, model)
            if existing is not None:
                out[p.target.id] = {"flags": len(existing.get("findings", []))}
                continue
            if digest in seen:
                out[p.target.id] = {"flags": seen[digest]}  # same-batch reuse; row committed by the holder
                continue
            if budget <= 0:
                continue
            try:
                verdict = self.classifier.classify(p.subject.entries)
            except ClassifierError as exc:
                log.warning("classifier skipped target %s: %s", p.target.id, exc)
                continue
            except Exception:  # noqa: BLE001 — belt and braces: nothing a model answers may end the crawl
                log.exception("classifier failed unexpectedly on target %s; skipped", p.target.id)
                continue
            budget -= 1
            flags = len(verdict.get("findings", []))
            seen[digest] = flags
            out[p.target.id] = {"verdict": verdict, "flags": flags}
        return out, budget

    def _commit_internal(self, p: Prepared, run_id: int, why: str) -> None:
        """Record an observation OUR side could not process, without touching the target's chain."""
        try:
            with self.store.db.transaction() as tx:
                tx.execute(
                    "INSERT INTO observations(target_id, run_id, observed_at, status, detail, pages) VALUES(?, ?, ?, ?, ?, ?)",
                    (p.target.id, run_id, p.observed_at, "internal-error", clean_text(why, 300), p.obs.pages),
                )
                tx.execute("UPDATE targets SET last_attempt=? WHERE id=?", (p.observed_at, p.target.id))
        except Exception:  # noqa: BLE001 - even the record of a failure must not end the crawl
            log.exception("could not record the internal error for target %s", p.target.id)

    def _commit_legacy(self, p: Prepared, scans: dict[str, dict[str, Any]]) -> None:
        """A run with a legacy pin (mcp<2): kept beside the record, never a label or the target's status.

        What a fresh install gets stays the observation; this says what the same version did when
        it could still start — its tools, what WARDEN matched in them, and what it did in the sandbox.
        """
        t = p.target
        try:
            with self.store.db.transaction() as tx:
                state = tx.one("SELECT package_signals, last_status, last_detail FROM targets WHERE id=?", (t.id,)) or {}
                try:
                    previous = json.loads(state.get("package_signals") or "{}")
                except ValueError:
                    previous = {}
                record = dict(previous) if isinstance(previous, dict) else {}
                if not isinstance(record.get("exit"), dict) and state.get("last_status") == "exited":
                    # The fresh install's reason is what the page and WARDEN show this pinned run under.
                    reason = classify("exited", state.get("last_detail"))
                    if reason:
                        record["exit"] = reason
                legacy: dict[str, Any] = {"version": p.obs.package_version or None, "observedAt": p.observed_at,
                                          "status": p.obs.status, "supplied": p.obs.package_supplied or {}}
                if p.obs.package_conditions:
                    legacy["conditions"] = p.obs.package_conditions
                if p.obs.status != "ok":
                    legacy["detail"] = clean_text(excerpt(p.obs.detail, 300))
                    reason = classify(p.obs.status, p.obs.detail)
                    if reason:
                        legacy["exit"] = reason
                subject = p.subject
                if subject is not None and subject.tool_set_digest:
                    Store.put_blob(tx, subject.tool_set_digest, "toolset", subject.entries)
                    legacy.update(toolSetDigest=subject.tool_set_digest, toolCount=len(subject.entries or []),
                                  tools=[clean_text(str(e.get("name", "")), 100) for e in (subject.entries or [])][:200])
                    scan = scans.get(t.id)
                    if scan is not None:
                        matches = scan.get("patternMatches") or []
                        legacy["patternMatches"] = {"block": sum(1 for m in matches if m.get("tier") == "block"),
                                                    "advise": sum(1 for m in matches if m.get("tier") == "advise"),
                                                    "matches": matches[:20]}
                elif p.obs.status == "ok":
                    # A run that started but has no subject still has a count. An empty tool set is
                    # refused as a subject (MTL-SUBJ-002), and without this the record said nothing
                    # where "0 tools" was the finding; the code says which case it was.
                    listed = [e for e in (p.obs.tools or []) if isinstance(e, dict)]
                    legacy.update(toolCount=len(listed),
                                  tools=[clean_text(str(e.get("name", "")), 100) for e in listed][:200])
                    if p.subject_error is not None:
                        legacy["subjectError"] = p.subject_error.code
                if p.obs.behaviour is not None:
                    legacy["behaviour"] = p.obs.behaviour
                record["legacy"] = legacy
                tx.execute("UPDATE targets SET package_signals=?, last_attempt=? WHERE id=?", (dumps(record), p.observed_at, t.id))
                from histor.security_events import append_event
                append_event(tx, self.logbook.key, t.id, p.observed_at, previous, record)
        except Exception:  # noqa: BLE001 - a side observation must not end the crawl
            log.exception("could not record the pinned run for target %s", t.id)

    # -- per-target commit -----------------------------------------------------------

    def _commit(self, p: Prepared, run_id: int, rulesets: RuleSets, scans: dict[str, dict[str, Any]],
                sets: str, classifications: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        issued_at = self.clock()
        labels: list[str] = []
        changed = 0
        classified = 0
        t = p.target
        with self.store.db.transaction() as tx:
            state = tx.one("SELECT * FROM targets WHERE id=?", (t.id,)) or {}
            subject = p.subject
            tx.execute(
                "INSERT INTO observations(target_id, run_id, observed_at, status, detail, subject, toolset, "
                "tool_count, pages, protocol, server_name, server_version) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t.id, run_id, p.observed_at, p.obs.status if not p.subject_error else "undigestible",
                 clean_text(excerpt(p.obs.detail, 1000) if not p.subject_error else p.subject_error.code, 1000),
                 subject.digest if subject else None, subject.tool_set_digest if subject else None,
                 len(p.obs.tools) if p.obs.tools is not None else None, p.obs.pages,
                 clean_text(p.obs.protocol_version, 40),
                 clean_text(p.obs.server_info.get("name") or "", 200) or None,
                 clean_text(p.obs.server_info.get("version") or "", 100) or None),
            )
            update: dict[str, Any] = {
                "last_attempt": p.observed_at,
                "observations": int(state.get("observations") or 0) + 1,
            }
            if p.obs.package_version:
                # The version this observation is about; the next crawl reruns the sandbox only
                # when the registry publishes another.
                update["package_version"] = clean_text(p.obs.package_version, 64)
            if p.obs.package_version:
                # Facts about the version, the observer that ran it, and what the package did.
                record = dict(p.signals or {})
                record["observer"] = p.obs.package_observer
                if p.obs.behaviour is not None:
                    record["behaviour"] = p.obs.behaviour
                if p.obs.package_conditions:
                    record["conditions"] = p.obs.package_conditions
                if p.obs.package_supplied:
                    record["supplied"] = p.obs.package_supplied
                reason = classify(p.obs.status, p.obs.detail)
                if reason:
                    record["exit"] = reason
                update["package_signals"] = dumps(record)
                from histor.security_events import append_event
                append_event(tx, self.logbook.key, t.id, p.observed_at,
                             json.loads(state.get("package_signals") or "{}"), record)

            def append(doc: dict[str, Any]) -> dict[str, Any]:
                row = self.logbook.append(tx, doc, target_id=t.id)
                labels.append(row["method"].rsplit(":", 1)[-1])
                return row

            if p.obs.status != "ok":
                update.update(last_status=p.obs.status, last_detail=clean_text(excerpt(p.obs.detail, 300)))
                if state.get("chain_label") and not state.get("failure_reported"):
                    prior = self._label_doc(tx, state["chain_label"])
                    append(self.issuer.continuity(
                        current=None, prior_label=prior,
                        prior_subject_ref=prior["credentialSubject"]["verifiedWork"],
                        prior_tool_digest=None, unchanged_since=None,
                        observed_at=p.observed_at, issued_at=issued_at,
                        failure={"status": p.obs.status, "detail": p.obs.detail[:200]},
                    ))
                    update["failure_reported"] = 1
                self._update_target(tx, t.id, update)
                return {"labels": labels, "changed": 0}

            if p.subject_error is not None:
                code = p.subject_error.code
                update.update(last_status="undigestible",
                              last_detail=clean_text(f"{code}: {p.subject_error.detail}", 300))
                fallback = fallback_subject(server_name=t.name, registry=t.registry, tools=p.obs.tools or [],
                                            **_artifact(t))
                if fallback.digest != state.get("current_subject"):
                    # A set we cannot digest is still a set the server showed, and not the one it
                    # showed before. It breaks the chain: no badge, /check answer or later
                    # continuity label may vouch for "unchanged" across it (MTL/1 7.2, 7.4).
                    Store.put_blob(tx, fallback.digest, "descriptor", fallback.descriptor)
                    link = append(self.issuer.undigestible(
                        subject_ref=fallback.reference(),
                        server={"name": t.name, "registry": t.registry},
                        count=len(p.obs.tools or []), code=code, message=p.subject_error.detail,
                        observed_at=p.observed_at, issued_at=issued_at,
                    ))
                    prior_id = state.get("chain_label")
                    if prior_id:
                        prior = self._label_doc(tx, prior_id)
                        link = append(self.issuer.continuity(
                            current=fallback, prior_label=prior,
                            prior_subject_ref=prior["credentialSubject"]["verifiedWork"],
                            prior_tool_digest=prior["credentialSubject"]["mcpTrustLabel"].get("toolSet", {}).get("digestSRI"),
                            unchanged_since=None, observed_at=p.observed_at, issued_at=issued_at, no_digest_code=code,
                        ))
                    if state.get("current_subject"):
                        changed = 1
                        self._record_change(tx, t.id, state, fallback, p.observed_at, link["id"])
                        update["changes"] = int(state.get("changes") or 0) + 1
                    update.update(
                        chain_label=link["id"], last_continuity_at=p.observed_at, current_subject=fallback.digest,
                        current_toolset=None, current_count=fallback.count, unchanged_since=p.observed_at,
                        failure_reported=0, block_matches=None, advise_matches=None, record_matches=None,
                        scan_sets=None,
                    )
                self._update_target(tx, t.id, update)
                return {"labels": labels, "changed": changed, "classified": 0}

            assert subject is not None
            new_subject = subject.digest != state.get("current_subject")
            scan = scans.get(t.id)
            obs_row = None
            if new_subject:
                Store.put_blob(tx, subject.digest, "descriptor", subject.descriptor)
                if subject.entries is not None and subject.tool_set_digest:
                    Store.put_blob(tx, subject.tool_set_digest, "toolset", subject.entries)
                obs_row = append(self.issuer.observation(subject, observed_at=p.observed_at, issued_at=issued_at))
            if scan is not None and (new_subject or state.get("scan_sets") != sets):
                matches = scan["patternMatches"]
                tx.execute(
                    "INSERT INTO scans(toolset, pattern_set, matches) VALUES(?, ?, ?) "
                    "ON CONFLICT(toolset, pattern_set) DO UPDATE SET matches=excluded.matches",
                    (subject.tool_set_digest, rulesets.pattern_set_digest, dumps(matches)),
                )
                append(self.issuer.pattern_scan(
                    subject, observed_at=p.observed_at, issued_at=issued_at,
                    pattern_set_id=rulesets.pattern_set_id, pattern_set_digest=rulesets.pattern_set_digest,
                    matches=matches,
                ))
                append(self.issuer.name_threat(
                    subject, observed_at=p.observed_at, issued_at=issued_at,
                    record_set_id=rulesets.record_set_id, record_set_digest=rulesets.record_set_digest,
                    matches=scan["recordMatches"],
                ))
                update.update(
                    scan_sets=sets,
                    block_matches=sum(1 for m in matches if m["tier"] == "block"),
                    advise_matches=sum(1 for m in matches if m["tier"] == "advise"),
                    record_matches=len(scan["recordMatches"]),
                )

            # Advisory classifier verdict — stored beside the log, never a label in it.
            cls = (classifications or {}).get(t.id)
            if cls is not None and self.classifier is not None and subject.tool_set_digest:
                model = self.classifier.model
                if "verdict" in cls:
                    Store.put_classification(tx, subject.tool_set_digest, model, cls["verdict"], p.observed_at)
                    classified = 1
                    update.update(classifier_model=model, classifier_flags=cls.get("flags", 0))
                # A reuse points the target at an existing verdict row. Set the columns only when the
                # row is really there — a prior crawl's, or this batch's holder, which commits earlier
                # in the loop. If the holder's commit failed, the row is absent and this target waits
                # for the next crawl rather than claiming a verdict that was never written.
                elif tx.one("SELECT 1 AS x FROM classifications WHERE toolset=? AND model=?",
                            (subject.tool_set_digest, model)) is not None:
                    update.update(classifier_model=model, classifier_flags=cls.get("flags", 0))

            prior_id = state.get("chain_label")
            if prior_id and (new_subject or self._continuity_due(state.get("last_continuity_at"), p.observed_at)):
                prior = self._label_doc(tx, prior_id)
                prior_detail = prior["credentialSubject"]["mcpTrustLabel"]
                cont = self.issuer.continuity(
                    current=subject, prior_label=prior,
                    prior_subject_ref=prior["credentialSubject"]["verifiedWork"],
                    prior_tool_digest=prior_detail.get("toolSet", {}).get("digestSRI"),
                    unchanged_since=None if new_subject else state.get("unchanged_since"),
                    observed_at=p.observed_at, issued_at=issued_at,
                )
                row = append(cont)
                update.update(chain_label=row["id"], last_continuity_at=p.observed_at)
                # A new subject after an old one is a change whatever the verdict: `fail` for two
                # digests that differ, `inconclusive` when one side had none (NUM-001, or the
                # undigestible set this one replaces) — the diff then compares names only.
                if new_subject and state.get("current_subject"):
                    changed = 1
                    self._record_change(tx, t.id, state, subject, p.observed_at, row["id"])
                    update["changes"] = int(state.get("changes") or 0) + 1
            elif not prior_id and obs_row is not None:
                # The observation starts the chain and counts as its first link: the next
                # continuity label is due one interval after it, not on the next crawl.
                update.update(chain_label=obs_row["id"], last_continuity_at=p.observed_at)

            update.update(
                last_status="ok", last_detail="", last_ok=p.observed_at,
                current_subject=subject.digest, current_toolset=subject.tool_set_digest,
                current_count=subject.count, ok_observations=int(state.get("ok_observations") or 0) + 1,
                failure_reported=0,
            )
            if not state.get("first_pinned"):
                update["first_pinned"] = p.observed_at
            if new_subject:
                update["unchanged_since"] = p.observed_at
            self._update_target(tx, t.id, update)
        return {"labels": labels, "changed": changed, "classified": classified}

    @staticmethod
    def _continuity_due(last: str | None, now: str) -> bool:
        last_dt, now_dt = _parse_ts(last), _parse_ts(now)
        return last_dt is None or now_dt is None or now_dt - last_dt >= timedelta(seconds=CONTINUITY_MIN_INTERVAL_S)

    @staticmethod
    def _label_doc(tx, label_id: str) -> dict[str, Any]:
        row = tx.one("SELECT body FROM labels WHERE id=?", (label_id,))
        if row is None:
            raise RuntimeError(f"chain label {label_id} is missing from the log")
        return json.loads(row["body"])

    def _record_change(self, tx, target_id: str, state: dict[str, Any], subject: Subject, observed_at: str,
                       label_id: str) -> None:
        old_toolset = state.get("current_toolset")
        old_entries = None
        if old_toolset:
            row = tx.one("SELECT body FROM blobs WHERE digest=?", (old_toolset,))
            old_entries = json.loads(row["body"]) if row else None
        old_descriptor = None
        if state.get("current_subject"):
            row = tx.one("SELECT body FROM blobs WHERE digest=?", (state["current_subject"],))
            old_descriptor = json.loads(row["body"]) if row else None
        old_names = old_descriptor["toolSet"]["names"] if old_descriptor else []
        from histor.security_events import append_event
        append_event(tx, self.logbook.key, target_id, observed_at, {"toolSetDigest": old_toolset},
                     {"toolSetDigest": subject.tool_set_digest, "subjectDigest": subject.digest}, kind="definitions")
        summary = diffing.tool_set_diff(old_entries, subject.entries, old_names, subject.names)
        tx.execute(
            "INSERT INTO changes(target_id, observed_at, from_subject, to_subject, from_toolset, to_toolset, summary, label_id) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (target_id, observed_at, state.get("current_subject"), subject.digest, old_toolset,
             subject.tool_set_digest, dumps(summary), label_id),
        )

    @staticmethod
    def _update_target(tx, target_id: str, fields: dict[str, Any]) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        tx.execute(f"UPDATE targets SET {cols} WHERE id=?", (*fields.values(), target_id))
