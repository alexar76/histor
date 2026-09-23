"""HTTP surface: the desk, the public API, the log, /check, badges, and the federation peer.

Everything here is read-only except three doors: ``/api/v1/check`` (rate-limited; writes a
digest count only when the caller opts in), the operator's ``/api/v1/admin/*`` (token), and
the hub's ``/ai-market/v2/invoke`` (the same read-only lookups as capabilities).
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from histor import __version__, badge
from histor.check import MAX_MATCHES, CheckError
from histor.labels import SHORT
from histor.service import Services, crawl_in_background, scheduler
from histor.untrusted import TooDeep, loads_limited

PRODUCT_ID = "histor"
MAX_CHECK_BYTES = 2 * 1024 * 1024
SPA_ROUTES = ("/", "/servers", "/changes", "/log", "/check", "/about", "/stats")
VC_MEDIA = "application/vc"

CAPS = (
    # Order matters to a Hub's admission assay, which probes the first free capabilities with an
    # input built from their schemas: `changes` answers any input, so it goes first.
    {
        "capability_id": "histor.changes@v1",
        "name": "Recent MCP tool-definition changes",
        "description": (
            "The newest tool-definition changes HISTOR observed at remote MCP endpoints in the official registry: "
            "which tools were added, removed or modified, any outside address (host, e-mail, IP) that newly appeared, "
            "and a link to each full diff."
        ),
        "price_per_call_usd": 0.0,
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
    },
    {
        "capability_id": "histor.check@v1",
        "name": "Check an MCP tool set against the log",
        "description": (
            "Send an MCP endpoint (or registry name) and the tools/list you received, or its MTL/1 digest. "
            "Returns, signed: whether HISTOR observed the same set there, since when, earlier sets, the "
            "WARDEN pattern matches for it, and the stored advisory classifier verdict if HISTOR has one "
            "(any language; one model's opinion, not a log label). Says what was advertised, never that a "
            "server is safe."
        ),
        "price_per_call_usd": 0.0,
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "the MCP endpoint URL"},
                "name": {"type": "string", "description": "or the server's registry name"},
                "tools": {"type": "array", "description": "the tools array of the tools/list result you received"},
                "toolSetDigest": {"type": "string", "description": "or its MTL/1 tool-set digest (sha256-…)"},
                "contribute": {"type": "boolean", "description": "add (endpoint, digest, day) to a public count"},
            },
        },
    },
    {
        "capability_id": "histor.server@v1",
        "name": "MCP server observation record",
        "description": (
            "HISTOR's observation record for one MCP endpoint (or every endpoint of a registry name): tool-set "
            "digest, tool count, first pinned, last observed, unchanged since, change count and last status, "
            "with links to the full record and its labels."
        ),
        "price_per_call_usd": 0.0,
        "input_schema": {"type": "object", "properties": {"endpoint": {"type": "string"}, "name": {"type": "string"}}},
    },
)


def _ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class RateLimiter:
    """Sliding window per key. In memory: one instance serves the site, so that is enough.

    Memory is bounded for real: once more than ``max_keys`` keys exist, keys whose newest hit is
    older than the longest window are dropped — at most once a minute, so a spray of addresses
    costs one sweep per minute rather than one per request.
    """

    def __init__(self, max_keys: int = 50000, longest_window_s: float = 86400) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self.max_keys = max_keys
        self.longest_window_s = longest_window_s
        self._swept = 0.0

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self.max_keys and now - self._swept > 60:
                self._swept = now
                stale = [k for k, v in self._hits.items() if not v or now - v[-1] > self.longest_window_s]
                for k in stale:
                    del self._hits[k]
            hits = self._hits[key]
            while hits and now - hits[0] > window_s:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True


def _xml_text(value: Any) -> str:
    """Escaped, and stripped of the characters XML 1.0 cannot carry at all (a tool name may hold them)."""
    text = "".join(ch for ch in str(value) if ch in "\t\n\r" or ("\x20" <= ch <= "\ud7ff")
                   or ("\ue000" <= ch <= "\ufffd") or ch >= "\U00010000")
    return xml_escape(text)


def create_app(services: Services) -> FastAPI:
    settings = services.settings
    store = services.store
    limiter = RateLimiter()
    proxies = []
    for entry in settings.trusted_proxies:
        try:
            proxies.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        thread = threading.Thread(target=scheduler, args=(services,), name="histor-scheduler", daemon=True)
        thread.start()
        yield
        services.stop.set()

    # No Swagger page: it loads its script from a CDN with inline boot code, which the production
    # CSP (script-src 'self') rightly blocks — it rendered blank. The schema itself is served.
    app = FastAPI(title="HISTOR", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        path = request.url.path
        if path.startswith("/assets/"):
            # Page code must revalidate (a 304 is cheap) or a returning visitor keeps running last
            # week's desk against this week's API; fonts and the pinned three.js never change.
            immutable = path.startswith(("/assets/fonts/", "/assets/vendor/"))
            response.headers["Cache-Control"] = "public, max-age=604800" if immutable else "no-cache"
        if path.startswith(("/api/", "/badge/", "/.well-known/", "/ai-market/", "/feed.xml")):
            # Public, unauthenticated, read-only data: any origin may read it (the GitHub Pages
            # landing reads the live counters from here).
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response

    @app.options("/api/v1/check")
    @app.options("/ai-market/v2/invoke")
    def preflight() -> Response:
        # A JSON POST from another origin (the GitHub Pages desk) is preceded by this. No
        # credentials are ever accepted, so the wildcard origin is safe.
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "86400",
        })

    def client_ip(request: Request) -> str:
        peer = request.client.host if request.client else ""
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return peer or "unknown"
        if any(peer_ip in net for net in proxies):
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                return forwarded.split(",")[-1].strip() or peer
            real = request.headers.get("x-real-ip", "").strip()
            if real:
                return real
        return peer

    def last_crawl_error() -> str | None:
        """The newest crawl's error, from memory or — after a restart — from the runs table.

        In memory only, a failed crawl vanished from /health on the next restart, and a monitor
        polling /health never saw it (audit F24).
        """
        if services.last_error:
            return services.last_error
        last = store.last_run(finished_only=True)
        return (last or {}).get("stats", {}).get("error") if last else None

    def operator(request: Request) -> None:
        token = settings.operator_token
        given = request.headers.get("x-histor-operator", "")
        if not token:
            raise HTTPException(status_code=503, detail="operator token not configured")
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="operator token required")

    # -- desk -----------------------------------------------------------------------

    index_file = settings.landing_dir / "index.html"

    def spa() -> Response:
        """The desk. One file serves both the service and GitHub Pages: relative asset paths
        work under /histor/ on Pages, and here a <base href="/"> makes them resolve from /s/<id>."""
        if not index_file.is_file():
            raise HTTPException(status_code=404, detail="landing missing")
        page = index_file.read_text(encoding="utf-8").replace("<!--HISTOR:BASE-->", '<base href="/">', 1)
        return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})

    for route in SPA_ROUTES:
        app.add_api_route(route, spa, methods=["GET"], include_in_schema=False)

    @app.get("/s/{target_id}", include_in_schema=False)
    def server_page(target_id: str) -> Response:
        return spa()

    if (settings.landing_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=settings.landing_dir / "assets"), name="assets")

    # -- health + identity ----------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, Any]:
        sth = store.latest_sth()
        return {
            "ok": True,
            "service": "histor",
            "version": __version__,
            "profile": settings.profile,
            "store": store.backend_type,
            "tree_size": sth["treeSize"] if sth else 0,
            "crawl_running": services.crawler.running_since is not None,
            "last_crawl_error": last_crawl_error(),
        }

    @app.get("/api/v1/issuer")
    def issuer() -> dict[str, Any]:
        from awr import parse_did_key

        return {
            "did": services.key.did,
            "publicKeyHex": parse_did_key(services.key.did).hex(),
            "name": "HISTOR",
            "profile": "MTL/1",
            "profileUrl": "https://github.com/alexar76/aicom/blob/main/awr/adoption/mcp-trust-label/PROFILE.md",
            "warden": store.get_meta("warden_package"),
            "patternSet": store.get_meta("pattern_set"),
            "recordSet": store.get_meta("record_set"),
            "logType": "RFC 9162 Merkle tree over JCS(label); STH type histor.sth/v1",
            "source": "https://github.com/alexar76/histor",
        }

    @app.get("/api/v1/stats")
    def stats() -> dict[str, Any]:
        run = store.last_run(finished_only=True)
        sth = store.latest_sth()
        counts = store.target_counts()
        out: dict[str, Any] = {
            "generatedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "targets": counts,
            "changes": {
                "last24h": store.count_changes_since(_ago(1)),
                "last7d": store.count_changes_since(_ago(7)),
                "last30d": store.count_changes_since(_ago(30)),
            },
            "log": {"treeSize": sth["treeSize"], "rootHash": sth["rootHash"], "sthTimestamp": sth["timestamp"]} if sth else
                   {"treeSize": 0},
            "clientReports": {"last7d": store.client_report_total(_ago(7)[:10])},
            "crawl": {
                "running": services.crawler.running_since is not None,
                "runningSince": services.crawler.running_since,
                "progress": services.crawler.progress,
                "intervalSeconds": settings.crawl_interval_s,
                "lastError": last_crawl_error(),
            },
        }
        if run:
            s = run["stats"]
            out["lastRun"] = {
                "id": run["id"],
                "startedAt": run["started_at"],
                "finishedAt": run["finished_at"],
                "registryServers": s.get("registry_servers"),
                "registryEndpoints": s.get("registry_endpoints"),
                "notAttempted": s.get("not_attempted", {}),
                "attempted": s.get("attempted"),
                "statuses": s.get("statuses", {}),
                "labelsIssued": s.get("labels_issued", {}),
                "changes": s.get("changes"),
                "error": s.get("error"),
            }
        return out

    hosts_cache: dict[str, Any] = {"at": 0.0, "body": None}

    @app.get("/api/v1/stats/hosts")
    def stats_hosts(limit: int = Query(15, ge=1, le=50)) -> dict[str, Any]:
        """Endpoints per host among dialled targets. Cached: it walks every target row."""
        if hosts_cache["body"] is None or time.monotonic() - hosts_cache["at"] > 600:
            from collections import Counter
            from urllib.parse import urlsplit

            counts = Counter(
                (urlsplit(t["endpoint"]).hostname or "?")
                for t in store.all_targets() if not t["delisted"] and not t["skip_reason"]
            )
            hosts_cache.update(at=time.monotonic(), body={
                "hosts": [{"host": h, "endpoints": n} for h, n in counts.most_common(50)],
                "distinctHosts": len(counts),
            })
        body = hosts_cache["body"]
        return {"hosts": body["hosts"][:limit], "distinctHosts": body["distinctHosts"]}

    # -- servers --------------------------------------------------------------------

    def target_summary(t: dict[str, Any]) -> dict[str, Any]:
        message, state = badge.badge_state(t)
        return {
            "id": t["id"], "name": t["name"], "title": t["title"], "endpoint": t["endpoint"],
            "transport": t["transport"], "version": t["version"], "repository": t["repository"],
            "website": t["website"], "delisted": bool(t["delisted"]), "skipReason": t["skip_reason"],
            "lastStatus": t["last_status"], "lastDetail": t["last_detail"], "lastAttempt": t["last_attempt"],
            "lastObserved": t["last_ok"], "toolCount": t["current_count"], "subjectDigest": t["current_subject"],
            "toolSetDigest": t["current_toolset"], "firstPinned": t["first_pinned"],
            "unchangedSince": t["unchanged_since"], "observations": t["ok_observations"],
            "attempts": t["observations"], "changes": t["changes"],
            "blockMatches": t["block_matches"], "adviseMatches": t["advise_matches"],
            "recordMatches": t["record_matches"], "state": state, "badgeText": message,
            "classifierModel": t["classifier_model"], "classifierFlags": t["classifier_flags"],
        }

    @app.get("/api/v1/servers")
    def servers(q: str = Query("", max_length=200), state: str = Query("all", max_length=20),
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=10_000_000)) -> dict[str, Any]:
        total, rows = store.search_targets(q.replace("\x00", "").strip(), state=state, limit=limit, offset=offset)
        return {"total": total, "offset": offset, "limit": limit, "servers": [target_summary(r) for r in rows]}

    @app.get("/api/v1/servers/{target_id}")
    def server(target_id: str) -> Response:
        t = store.target(target_id)
        if t is None:
            raise HTTPException(status_code=404, detail="unknown server")
        history = store.history(target_id)
        # Runs of the same outcome collapse into one row: 300 identical "ok" days are one fact.
        runs: list[dict[str, Any]] = []
        for h in reversed(history):
            key = (h["status"], h["toolset"], h["subject"])
            if runs and runs[-1]["key"] == key:
                runs[-1]["until"] = h["observed_at"]
                runs[-1]["count"] += 1
            else:
                runs.append({"key": key, "from": h["observed_at"], "until": h["observed_at"], "count": 1,
                             "status": h["status"], "detail": h["detail"], "toolSetDigest": h["toolset"],
                             "subjectDigest": h["subject"], "toolCount": h["tool_count"],
                             "serverName": h["server_name"], "serverVersion": h["server_version"]})
        for r in runs:
            r.pop("key")
        matches = None
        pattern_set = store.get_meta("pattern_set")
        if t["current_toolset"] and pattern_set:
            with store.db.read() as tx:
                row = tx.one("SELECT matches FROM scans WHERE toolset=? AND pattern_set=?", (t["current_toolset"], pattern_set))
            matches = json.loads(row["matches"])[:MAX_MATCHES] if row else None
        descriptor = store.get_blob(t["current_subject"], "descriptor") if t["current_subject"] else None
        classifier = None
        if t["current_toolset"] and t["classifier_model"]:
            classifier = store.classification(t["current_toolset"], t["classifier_model"])
        page = {
            "server": target_summary(t),
            "description": t["description"],
            "descriptor": descriptor,
            "patternMatches": matches,
            "classifier": classifier,
            "labels": [{**lbl, "methodShort": SHORT.get(lbl["method"], lbl["method"])} for lbl in store.labels_for(target_id)],
            "timeline": list(reversed(runs))[:100],
            "changes": store.changes(limit=20, target_id=target_id),
            "clientReports": store.client_reports(target_id, _ago(30)[:10]),
        }
        # The tool set can be megabytes: spliced in as the stored text instead of parsed and
        # re-serialised on every request.
        tools = store.blob_text(t["current_toolset"], "toolset") if t["current_toolset"] else None
        body = json.dumps(page, ensure_ascii=False)[:-1] + ',"tools":' + (tools or "null") + "}"
        return Response(body, media_type="application/json")

    # -- labels, blobs --------------------------------------------------------------

    @app.get("/api/v1/labels/{label_id}")
    def label(label_id: str) -> Response:
        row = store.label(label_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown label")
        return Response(row["body"], media_type=VC_MEDIA, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/v1/labels/{label_id}/proof")
    def label_proof(label_id: str, tree_size: int | None = Query(None, ge=1)) -> dict[str, Any]:
        row = store.label(label_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown label")
        sth = store.sth(tree_size) if tree_size else store.latest_sth()
        if sth is None or sth["treeSize"] <= row["leaf_index"]:
            raise HTTPException(status_code=409, detail="no signed tree head covers this label yet")
        return {
            "labelId": label_id,
            "leafIndex": row["leaf_index"],
            "leafInput": "JCS(label document)",
            "sth": sth,
            "inclusionProof": services.logbook.inclusion(row["leaf_index"], sth["treeSize"]),
        }

    def blob_route(kind: str):
        def handler(digest: str) -> Response:
            body = store.blob_text(digest, kind)
            if body is None:
                raise HTTPException(status_code=404, detail=f"unknown {kind}")
            return Response(body, media_type="application/json",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})
        return handler

    for kind, path in (("descriptor", "descriptors"), ("toolset", "toolsets"), ("pattern-set", "pattern-sets"),
                       ("record-set", "record-sets")):
        app.add_api_route(f"/api/v1/{path}/{{digest:path}}", blob_route(kind), methods=["GET"])

    # -- changes --------------------------------------------------------------------

    @app.get("/api/v1/changes")
    def changes(limit: int = Query(50, ge=1, le=200), before: int | None = Query(None, ge=1)) -> dict[str, Any]:
        return {"changes": store.changes(limit=limit, before_id=before)}

    @app.get("/api/v1/changes/{change_id}")
    def change(change_id: int) -> dict[str, Any]:
        row = store.change(change_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown change")
        return row

    @app.get("/feed.xml")
    def feed() -> Response:
        items = store.changes(limit=50)
        updated = items[0]["observed_at"] if items else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries = []
        for c in items:
            s = c["summary"]
            parts = []
            if s.get("newAddresses"):
                total = s.get("newAddressesTotal", len(s["newAddresses"]))
                more = f" (+{total - 10} more)" if total > 10 else ""
                parts.append(f"NEW OUTSIDE ADDRESSES: {', '.join(s['newAddresses'][:10])}{more}")
            if s["added"]:
                parts.append(f"added: {', '.join(s['added'][:10])}")
            if s["removed"]:
                parts.append(f"removed: {', '.join(s['removed'][:10])}")
            if s["modified"]:
                parts.append(f"modified: {', '.join(m['tool'] for m in s['modified'][:10])}")
            link = f"{settings.public_base}/s/{c['target_id']}"
            entries.append(
                "<entry>"
                f"<id>{_xml_text(settings.public_base)}/changes/{c['id']}</id>"
                f"<title>{_xml_text(c['name'])}: tool definitions changed</title>"
                f"<updated>{c['observed_at']}</updated>"
                f'<link href="{_xml_text(link)}"/>'
                f"<summary>{_xml_text('; '.join(parts) or 'definitions changed')} — {_xml_text(c['endpoint'])}</summary>"
                "</entry>"
            )
        body = (
            '<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>{xml_escape(settings.public_base)}/feed.xml</id><title>HISTOR — MCP tool-definition changes</title>"
            f"<updated>{updated}</updated><author><name>HISTOR</name></author>"
            f'<link rel="self" href="{xml_escape(settings.public_base)}/feed.xml"/>' + "".join(entries) + "</feed>"
        )
        return Response(body, media_type="application/atom+xml")

    # -- log ------------------------------------------------------------------------

    @app.get("/api/v1/log/sth")
    def latest_sth() -> dict[str, Any]:
        sth = store.latest_sth()
        if sth is None:
            raise HTTPException(status_code=404, detail="the log is empty")
        return sth

    @app.get("/api/v1/log/sth/{tree_size}")
    def sth_at(tree_size: int) -> dict[str, Any]:
        sth = store.sth(tree_size)
        if sth is None:
            raise HTTPException(status_code=404, detail="no signed tree head at that size")
        return sth

    @app.get("/api/v1/log/proof/inclusion")
    def inclusion(leaf_index: int = Query(..., ge=0), tree_size: int = Query(..., ge=1)) -> dict[str, Any]:
        if leaf_index >= tree_size:
            raise HTTPException(status_code=400, detail="leaf_index must be below tree_size")
        try:
            return {"leafIndex": leaf_index, "treeSize": tree_size, "proof": services.logbook.inclusion(leaf_index, tree_size)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/log/proof/consistency")
    def consistency(first: int = Query(..., ge=1), second: int = Query(..., ge=1)) -> dict[str, Any]:
        if first > second:
            raise HTTPException(status_code=400, detail="first must not exceed second")
        try:
            return {"first": first, "second": second, "proof": services.logbook.consistency(first, second)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/log/entries")
    def entries(start: int = Query(0, ge=0), end: int | None = Query(None, ge=1)) -> dict[str, Any]:
        stop = min(end if end is not None else start + 100, start + 1000)
        return {"start": start, "entries": [
            {**e, "methodShort": SHORT.get(e["method"], e["method"])} for e in store.entries(start, stop)
        ]}

    # -- check ----------------------------------------------------------------------

    async def read_json(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > MAX_CHECK_BYTES:
            raise HTTPException(status_code=413, detail=f"body larger than {MAX_CHECK_BYTES} bytes")
        try:
            body = await run_in_threadpool(loads_limited, raw or b"{}")
        except TooDeep as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="body is not JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        return body

    def limit_key(request: Request) -> list[tuple[str, int]]:
        """The buckets a request counts against, each with its limit a minute.

        A hub routes every buyer's call from one address. Since the hubs name the buyer with an
        opaque ``X-AIMarket-Buyer`` id, each buyer gets the normal per-caller bucket, and all of
        one hub's traffic from one address shares a ceiling of ten of those.

        Both headers can be forged, so the FIRST bucket is always the bare address and is charged
        whatever the headers say. Rotating the hub or buyer value on every request mints a fresh
        sub-bucket each time — that used to mean no limit at all — but every one of those requests
        still lands in the same per-address bucket, capped at the hub ceiling.
        """
        ip = client_ip(request)
        hub = request.headers.get("x-aimarket-routing-hub", "").strip()[:200]
        if not hub:
            return [(ip, settings.check_rate_per_min)]
        buyer = "".join(c for c in request.headers.get("x-aimarket-buyer", "") if c.isalnum() or c == "-")[:64]
        buckets = [(ip, settings.check_rate_per_min * 10), (f"{ip}|{hub}", settings.check_rate_per_min * 10)]
        if buyer:
            buckets.append((f"{ip}|{hub}|{buyer}", settings.check_rate_per_min))
        return buckets

    def allow_all(kind: str, buckets: list[tuple[str, int]]) -> bool:
        # Every bucket must have room; checked narrowest-last so a refused buyer does not also
        # spend the shared ceiling for the others.
        return all(limiter.allow(f"{kind}:{key}", per_min, 60) for key, per_min in reversed(buckets))

    def run_check(body: dict[str, Any], who: list[tuple[str, int]]) -> dict[str, Any]:
        """Synchronous on purpose: callers run it in the threadpool, never on the event loop."""
        if not allow_all("check", who):
            raise HTTPException(status_code=429, detail="too many checks; slow down")
        addr = who[0][0]  # the bare address — always first, and not forgeable by a header
        key = who[-1][0]  # the narrowest identity: the buyer when a hub names one
        scan_rate = settings.check_scan_rate_per_hour

        def may_scan() -> bool:  # asked only when a fresh scan would actually run (audit F45)
            # The per-buyer budget, AND a per-address ceiling a rotated buyer id cannot escape: fresh
            # scans hold the site's only scan slot, so an unbounded caller would starve everyone.
            if scan_rate <= 0:
                return False
            if len(who) > 1 and not limiter.allow(f"scan:{key}", scan_rate, 3600):
                return False
            return limiter.allow(f"scan:{addr}", scan_rate * (10 if len(who) > 1 else 1), 3600)

        def may_contribute(target_id: str) -> bool:
            # One report per ADDRESS, target and day. The public count is a cross-client signal
            # ("some clients see a different set"); keyed on a forgeable buyer id, one caller could
            # mint any number of "clients" and fake or bury that signal for any server.
            return limiter.allow(f"report:{addr}:{target_id}", 1, 86400)

        try:
            return services.checker.check(body, allow_scan=may_scan, may_contribute=may_contribute)
        except CheckError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/check")
    async def check(request: Request) -> dict[str, Any]:
        body = await read_json(request)
        return await run_in_threadpool(run_check, body, limit_key(request))

    # -- badge ----------------------------------------------------------------------

    @app.get("/badge/{target_id}.svg")
    def badge_svg(target_id: str) -> Response:
        return Response(badge.render(store.target(target_id)), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    # -- live README badges (shields.io endpoint schema) -----------------------------

    def _shield(label: str, message: str, color: str) -> JSONResponse:
        return JSONResponse({"schemaVersion": 1, "label": label, "message": message, "color": color},
                            headers={"Cache-Control": "public, max-age=300"})

    @app.get("/api/v1/badges/{name}")
    def shield(name: str) -> JSONResponse:
        """Numbers for README badges, read live: https://img.shields.io/endpoint?url=<this>."""
        if name == "log":
            sth = store.latest_sth()
            return _shield("labels in log", f"{sth['treeSize']:,}" if sth else "0", "5fe3ff")
        if name == "pinned":
            return _shield("tool sets pinned", f"{store.target_counts()['pinned']:,}", "3a7bd5")
        if name == "changes":
            return _shield("changes · 7d", f"{store.count_changes_since(_ago(7)):,}", "c98a1b")
        if name == "sth":
            sth = store.latest_sth()
            if not sth:
                return _shield("signed tree head", "none yet", "6b7280")
            then = datetime.strptime(sth["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            hours = (datetime.now(UTC) - then).total_seconds() / 3600
            age = f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f}d ago"
            # A head older than two crawl intervals means the crawler stopped: say so in the colour.
            stale = settings.crawl_interval_s and hours * 3600 > 2 * settings.crawl_interval_s
            return _shield("signed tree head", age, "c98a1b" if stale else "2f9e77")
        raise HTTPException(status_code=404, detail="unknown badge")

    # -- operator -------------------------------------------------------------------

    @app.post("/api/v1/admin/crawl")
    def admin_crawl(request: Request) -> JSONResponse:
        operator(request)
        started = crawl_in_background(services)
        return JSONResponse({"started": started, "runningSince": services.crawler.running_since},
                            status_code=202 if started else 409)

    # -- federation peer -------------------------------------------------------------

    signed_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    signed_lock = threading.Lock()

    def cached_signed(name: str, max_age_s: float, build) -> dict[str, Any]:
        """ML-DSA-65 is pure Python and holds the GIL for ~0.1-0.3 s: signing the discovery documents
        on every anonymous GET let one client starve the process, crawler included (audit F06)."""
        now = time.monotonic()
        with signed_lock:
            hit = signed_cache.get(name)
            if hit and now - hit[0] < max_age_s:
                return hit[1]
            document = build()
            signed_cache[name] = (now, document)
            return document

    @app.get("/.well-known/ai-market.json")
    def well_known() -> Response:
        # Static for the life of the process: signed once.
        return JSONResponse(cached_signed("well-known", float("inf"), build_well_known),
                            headers={"Cache-Control": "public, max-age=300"})

    def build_well_known() -> dict[str, Any]:
        document: dict[str, Any] = {
            "protocol": "aimarket/2",
            "protocol_versions": ["v2"],
            "name": "HISTOR",
            "hub_url": settings.public_base,
            "signer_public_key": services.provider.public_key_b64,
            "manifest_url": f"{settings.public_base}/ai-market/v2/manifest",
            "mcp_endpoint": f"{settings.public_base}/ai-market/v2/invoke",
            "provider_pubkey": services.provider.public_key_b64,
            "capabilities": [c["capability_id"] for c in CAPS],
            "capabilities_count": len(CAPS),
            "categories": ["mcp-security", "transparency-log", "tool-definitions", "awr", "mtl"],
            "issuer_did": services.key.did,
        }
        document["signature"] = services.provider.sign_object(document)
        return document

    def manifest_tools() -> list[dict[str, Any]]:
        return [{
            **cap,
            "product_id": PRODUCT_ID,
            "invoke_url": f"{settings.public_base}/ai-market/v2/invoke",
            "publisher_id": "histor",
            "provider_pubkey": services.provider.public_key_b64,
            "category": "mcp-security",
            "output_schema": {"type": "object"},
        } for cap in CAPS]

    @app.get("/ai-market/v2/manifest")
    def manifest() -> Response:
        # Re-signed at most every ten minutes; a hub accepts a manifest for a week.
        return JSONResponse(cached_signed("manifest", 600, build_manifest), headers={"Cache-Control": "public, max-age=300"})

    def build_manifest() -> dict[str, Any]:
        tools = manifest_tools()
        document: dict[str, Any] = {
            "protocol": "aimarket/2",
            "protocol_version": "v2",
            "name": "HISTOR",
            "hub_url": settings.public_base,
            "total_capabilities": len(tools),
            "capabilities_count": len(tools),
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tools": tools,
            "by_hub": {},
        }
        document["signature"] = services.provider.sign_manifest(document)
        return document

    @app.get("/ai-market/v2/search")
    def search(q: str = "") -> dict[str, Any]:
        needle = q.lower().strip()
        tools = [t for t in manifest_tools() if not needle or needle in t["capability_id"] or needle in t["description"].lower()]
        return {"total": len(tools), "tools": tools}

    def receipt(result: dict[str, Any], capability_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        rec = {
            "nonce": "0x" + secrets.token_hex(16),
            "product_id": PRODUCT_ID,
            "capability_id": capability_id,
            "price_usd": 0.0,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "success": 1,
            "latency_ms": 0,
        }
        rec["signature"] = services.provider.sign_hub_receipt(rec)
        return {
            "ok": True,
            "result": result,
            "provider_pubkey": services.provider.public_key_b64,
            "receipt": rec,
            "signature": services.provider.sign_result(result, capability_id=capability_id, product_id=PRODUCT_ID,
                                                       input_payload=payload),
        }

    def compact_change(c: dict[str, Any]) -> dict[str, Any]:
        """A change as a hub buyer needs it: bounded size, the full diff one link away."""
        s = c["summary"]
        return {
            "id": c["id"], "server": c["name"], "endpoint": c["endpoint"], "observedAt": c["observed_at"],
            "added": s["added"][:20], "removed": s["removed"][:20],
            "modified": [m["tool"] for m in s["modified"][:20]],
            "newAddresses": s.get("newAddresses", [])[:20],
            "newAddressesTotal": s.get("newAddressesTotal", len(s.get("newAddresses", []))),
            "detail": s.get("detail"), "label": c.get("label_id"),
            "page": f"{settings.public_base}/s/{c['target_id']}",
        }

    def do_invoke(payload: dict[str, Any], who: list[tuple[str, int]]) -> dict[str, Any]:
        cap = str(payload.get("capability_id") or "")
        inner = payload.get("input") if isinstance(payload.get("input"), dict) else payload
        if cap == "histor.check@v1":
            return receipt(run_check(inner, who), cap, inner)
        if not allow_all("invoke", who):
            raise HTTPException(status_code=429, detail="too many calls; slow down")
        if cap == "histor.server@v1":
            endpoint, name = inner.get("endpoint"), inner.get("name")
            for value in (endpoint, name):
                if value is not None and (not isinstance(value, str) or "\x00" in value or len(value) > 2000):
                    raise HTTPException(status_code=400, detail="endpoint and name must be strings")
            candidates = store.targets_by(endpoint=endpoint) if endpoint else store.targets_by(name=name or "")
            if not candidates:
                raise HTTPException(status_code=404, detail="unknown server")
            return receipt({"servers": [target_summary(t) for t in candidates[:10]]}, cap, inner)
        if cap == "histor.changes@v1":
            limit = inner.get("limit") if isinstance(inner.get("limit"), int) else 20
            return receipt({"changes": [compact_change(c) for c in store.changes(limit=max(1, min(limit, 50)))]},
                           cap, inner)
        raise HTTPException(status_code=404, detail="unknown capability")

    @app.post("/ai-market/v2/invoke")
    async def invoke(request: Request) -> dict[str, Any]:
        payload = await read_json(request)
        # Digesting, the WARDEN subprocess and the ML-DSA receipt signature all block: threadpool.
        return await run_in_threadpool(do_invoke, payload, limit_key(request))

    return app
