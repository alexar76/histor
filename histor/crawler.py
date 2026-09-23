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
import logging
import threading
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from histor import diffing
from histor.classifier import Classifier, ClassifierError
from histor.config import Settings
from histor.labels import LabelIssuer
from histor.logbook import Logbook, utcnow
from histor.mcpclient import McpReader, Observation
from histor.registry import Target, harvest, load_opt_out
from histor.scanner import RuleSets, Scanner, ScannerError
from histor.store import Store, dumps
from histor.subject import MTL_SUBJ_001, MtlError, Subject, build_subject, fallback_subject
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


def _host_of(endpoint: str) -> str:
    try:
        return (urlsplit(endpoint).hostname or "").lower()
    except ValueError:  # an unparseable registry URL; netguard refuses it with a status later
        return ""


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


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


class Crawler:
    def __init__(self, settings: Settings, store: Store, issuer: LabelIssuer, logbook: Logbook,
                 scanner: Scanner, *, reader: McpReader | None = None,
                 harvest_fn: Callable[[str], list[Target]] | None = None,
                 classifier: Classifier | None = None,
                 clock: Callable[[], str] = utcnow) -> None:
        self.settings = settings
        self.store = store
        self.issuer = issuer
        self.logbook = logbook
        self.scanner = scanner
        self.classifier = classifier
        self.reader = reader or McpReader(timeout=settings.crawl_timeout_s,
                                          allow_private=settings.allow_private_targets)
        self.harvest_fn = harvest_fn or (
            lambda url: harvest(url, allow_cleartext=settings.allow_private_targets,
                                opt_out=load_opt_out(settings.opt_out, settings.opt_out_path))
        )
        self.clock = clock
        self._lock = threading.Lock()
        self.running_since: str | None = None
        self.progress: dict[str, int] | None = None

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
            self._lock.release()

    def _run(self) -> dict[str, Any]:
        started = self.clock()
        with self.store.db.transaction() as tx:
            run_id = int(tx.one("INSERT INTO runs(started_at) VALUES(?) RETURNING id", (started,))["id"])
        stats: dict[str, Any] = {"started_at": started}
        try:
            targets = self.harvest_fn(self.settings.registry_url)
            stats["registry_servers"] = len({t.name for t in targets})
            stats["registry_endpoints"] = len(targets)
            stats["not_attempted"] = dict(Counter(t.skip_reason for t in targets if t.skip_reason))
            self._sync_targets(targets, started)

            rulesets = self.scanner.rulesets()
            sets = self._register_rulesets(rulesets)

            dialable = [t for t in targets if not t.skip_reason]
            if self.settings.crawl_limit:
                dialable = dialable[: self.settings.crawl_limit]
            # Interleave once over the whole list, so every batch carries its share of each host
            # instead of one batch spending an hour on a single host's 2 000 endpoints.
            dialable = _interleave_by_host(dialable)
            stats["attempted"] = len(dialable)
            statuses: Counter[str] = Counter()
            issued: Counter[str] = Counter()
            changes = 0
            classified = 0
            classify_budget = self.settings.classifier_max_per_crawl if self.classifier else 0
            step = self.settings.crawl_batch
            internal: Counter[str] = Counter()
            for start in range(0, len(dialable), step):
                prepared = self._observe(dialable[start : start + step])
                statuses.update(p.obs.status for p in prepared)
                scans, scan_failed = self._scan(prepared, sets)
                classifications, classify_budget = self._classify(prepared, classify_budget)
                for item in prepared:
                    # One target must never take the crawl down: whatever a stranger's server made
                    # us do, it is recorded against that target and the crawl moves on.
                    if item.target.id in scan_failed:
                        internal["scan-failed"] += 1
                        self._commit_internal(item, run_id, "the WARDEN sidecar failed on this tool set")
                        continue
                    try:
                        result = self._commit(item, run_id, rulesets, scans, sets, classifications)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("commit failed for target %s", item.target.id)
                        internal[type(exc).__name__] += 1
                        self._commit_internal(item, run_id, f"internal error ({type(exc).__name__})")
                        continue
                    issued.update(result["labels"])
                    changes += result["changed"]
                    classified += result.get("classified", 0)
                self.progress = {"done": start + len(prepared), "of": len(dialable)}
            if internal:
                stats["internal_errors"] = dict(internal)
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

    def _sync_targets(self, targets: list[Target], now: str) -> None:
        with self.store.db.transaction() as tx:
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

    def _register_rulesets(self, rulesets: RuleSets) -> str:
        """Publish both sets by digest; return the pair every target's scan labels are compared with."""
        with self.store.db.transaction() as tx:
            Store.put_blob(tx, rulesets.pattern_set_digest, "pattern-set", rulesets.pattern_set)
            Store.put_blob(tx, rulesets.record_set_digest, "record-set", rulesets.record_set)
            Store.set_meta(tx, "pattern_set", rulesets.pattern_set_digest)
            Store.set_meta(tx, "record_set", rulesets.record_set_digest)
            Store.set_meta(tx, "warden_package", rulesets.warden_package)
        return f"{rulesets.pattern_set_digest}|{rulesets.record_set_digest}"

    def _observe(self, targets: list[Target]) -> list[Prepared]:
        per_host: dict[str, threading.Semaphore] = defaultdict(
            lambda: threading.Semaphore(self.settings.crawl_per_host)
        )
        guard = threading.Lock()

        def one(t: Target) -> Prepared:
            with guard:
                sem = per_host[_host_of(t.endpoint)]
            try:
                with sem:
                    obs = self.reader.list_tools(t.endpoint)
            except Exception as exc:  # noqa: BLE001 - the reader promises not to raise; belt and braces
                obs = Observation(status="client-error", detail=type(exc).__name__)
            item = Prepared(target=t, obs=obs, observed_at=self.clock())
            if obs.status == "ok":
                try:
                    item.subject = build_subject(server_name=t.name, registry=t.registry, tools=obs.tools or [],
                                                 transport="streamable-http", endpoint=t.endpoint)
                except MtlError as exc:
                    item.subject_error = exc
                except Exception as exc:  # noqa: BLE001 - hostile input the canonicalizer chokes on
                    item.subject_error = MtlError(MTL_SUBJ_001, f"the tool set cannot be processed ({type(exc).__name__})")
            return item

        with ThreadPoolExecutor(max_workers=self.settings.crawl_workers) as pool:
            return list(pool.map(one, targets))

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
                 clean_text(p.obs.detail if not p.subject_error else p.subject_error.code, 300),
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

            def append(doc: dict[str, Any]) -> dict[str, Any]:
                row = self.logbook.append(tx, doc, target_id=t.id)
                labels.append(row["method"].rsplit(":", 1)[-1])
                return row

            if p.obs.status != "ok":
                update.update(last_status=p.obs.status, last_detail=clean_text(p.obs.detail, 300))
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
                                            transport="streamable-http", endpoint=t.endpoint)
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

    @staticmethod
    def _record_change(tx, target_id: str, state: dict[str, Any], subject: Subject, observed_at: str,
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
