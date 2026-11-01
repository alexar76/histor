"""Cross-check HISTOR against another public record of MCP tool-definition changes.

The peer is ScraperWatch's MCP tool-change log (https://scraperwatch.com/mcp-changes/). It reads a fixed panel of
remote endpoints once a day (from 02:50Z, about ten minutes per run) and publishes daily diffs: tools added and
removed and, per changed tool, field-level differences as JSON paths with old and new values. HISTOR reads the same
endpoints far more often and keeps every definition it was served. Two directions:

* **theirs against HISTOR** — for every tool change the peer lists, did HISTOR see the old value and then the new
  one between the peer's two reads, path by path?
* **HISTOR against theirs** — for every HISTOR change on a panel endpoint that the peer does not list, why not?
  (Members the peer does not hash, robots.txt, a change undone between two reads, timing, or a miss.)

``python -m histor.peer_xcheck OUTDIR [--days N]`` writes ``README.md``, ``results.json``, the two peer files it
read (``inputs/``) and ``SHA256SUMS`` into OUTDIR, which must not exist. It only reads the database; over the
network it fetches the peer's two public files and, for the few hosts that need it, robots.txt. The host's weekly
dataset export runs it after each snapshot (deploy/export_dataset.sh) and publishes the result at
``/datasets/peers/scraperwatch/``. First run by hand on 2026-11-01; that comparison found a robots.txt bug on the
peer's side (fixed the same day) and a fallback bug in HISTOR's own reader (fixed in 2cf275902).
"""

from __future__ import annotations

import collections
import datetime as dt
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

PEER = {
    "name": "ScraperWatch",
    "slug": "scraperwatch",
    "page": "https://scraperwatch.com/mcp-changes/",
    "changes": "https://scraperwatch.com/mcp-changes/changes.json",
    "panel": "https://scraperwatch.com/mcp-changes/panel.txt",
    "agent": "scraperwatch-mcp-observatory",
    "run_start": "02:50:00",
    "run_minutes": 10,
    # Their first run, the baseline of the first diff (ops@scraperwatch.com, 2026-11-01).
    "first_run": ("2026-10-30T10:49:43Z", "2026-10-30T10:59:39Z"),
}
# Fields the peer hashes per tool; it leaves out rolling dates, version strings, small counters, whitespace,
# reordered lists and renumbered schema definitions.
PEER_FIELDS = ("name", "description", "inputSchema", "outputSchema", "title", "annotations")
SLACK = dt.timedelta(hours=7)  # HISTOR reads most endpoints every 30 minutes, some every 6 hours
MAX_FILE = 64 * 1024 * 1024
MISSING = object()


def ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- comparing values the way the peer does ---------------------------------------------------------------

def at_path(doc: Any, path: str) -> Any:
    cur = doc
    for key, idx in re.findall(r"\.([^.\[]+)|\[(\d+)\]", path[1:]):
        if key:
            if not isinstance(cur, dict) or key not in cur:
                return MISSING
            cur = cur[key]
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                return MISSING
            cur = cur[i]
    return cur


def unordered(v: Any) -> Any:
    """The peer publishes lists sorted, at any depth (an enum comes alphabetical)."""
    if isinstance(v, dict):
        return {k: unordered(x) for k, x in v.items()}
    if isinstance(v, list):
        return sorted((unordered(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True))
    return v


def inline_refs(schema: Any, root: Any = None, depth: int = 0, *, drop_defs: bool = False) -> Any:
    """The peer walks schemas with local $ref resolved; HISTOR keeps them as served."""
    root = schema if root is None else root
    if depth > 30:
        return schema
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and len(schema) == 1:
            target = root
            for part in ref[2:].split("/"):
                target = target.get(part) if isinstance(target, dict) else None
            if target is not None:
                return inline_refs(target, root, depth + 1, drop_defs=drop_defs)
        return {k: inline_refs(v, root, depth + 1, drop_defs=drop_defs) for k, v in schema.items()
                if not (drop_defs and k in ("$defs", "definitions"))}
    if isinstance(schema, list):
        return [inline_refs(v, root, depth + 1, drop_defs=drop_defs) for v in schema]
    return schema


def same(a: Any, b: Any) -> bool:
    return a == b or unordered(a) == unordered(b)


def canon(v: Any, noise: bool = False) -> Any:
    """Lists unordered, whitespace collapsed; with ``noise`` also dates, version strings and numbers blanked."""
    if isinstance(v, dict):
        return {k: canon(x, noise) for k, x in v.items()}
    if isinstance(v, list):
        return sorted((canon(x, noise) for x in v), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(v, str):
        s = " ".join(v.split())
        if noise:
            s = re.sub(r"\d{4}-\d{2}-\d{2}([T ][\d:.]+Z?)?", "<date>", s)
            s = re.sub(r"\bv?\d+(\.\d+)+([-+][\w.]+)?\b", "<ver>", s)
            s = re.sub(r"\d+", "<n>", s)
        return s
    if noise and isinstance(v, (int, float)) and not isinstance(v, bool):
        return "<n>"
    return v


def robots_allows(text: str | None, url: str, agent: str) -> bool:
    """RFC 9309: the group for ``agent`` (else ``*``), the longest matching rule wins, Allow on a tie, ``*`` and a
    trailing ``$`` as wildcards. No robots.txt, or one we could not read, allows everything."""
    if not text:
        return True
    u = urlsplit(url)
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    groups: dict[str, list[tuple[str, str]]] = {}
    current: list[str] = []
    seen_rule = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if seen_rule:
                current, seen_rule = [], False
            current.append(val.lower())
            groups.setdefault(val.lower(), [])
        elif key in ("allow", "disallow") and current:
            seen_rule = True
            for a in current:
                groups[a].append((key, val))
    rules = groups.get(agent.lower()) or groups.get("*") or []

    def length(rule: str) -> int:
        if not rule:
            return -1
        pattern = re.escape(rule).replace(r"\*", ".*")
        if pattern.endswith(r"\$"):
            pattern = pattern[:-2] + "$"
        return len(rule) if re.match(pattern, path) else -1

    best = max(((length(v), k == "allow") for k, v in rules), default=(-1, True))
    return best[0] < 0 or best[1]


# --- the data -----------------------------------------------------------------------------------------------

class Data:
    """What both checks read: targets by endpoint, ok observations, changes and blobs.

    ``rows`` loads lazily from the store (``from_store``) or from exported JSON lines in tests.
    """

    def __init__(self, targets: list[dict[str, Any]], fetch_obs: Callable[[list[str], str, str], list[dict]],
                 fetch_changes: Callable[[list[str], str, str], list[dict]],
                 fetch_blobs: Callable[[list[str]], dict[str, Any]]) -> None:
        # One endpoint can have several registry entries (a delisted one beside the current one): every one counts
        # for HISTOR's changes, and the listed one stands for the endpoint.
        self.by_id = {t["id"]: t for t in targets}
        self.ids_by_endpoint: dict[str, list[str]] = collections.defaultdict(list)
        for t in sorted(targets, key=lambda t: (bool(t.get("delisted")), t["id"])):
            self.ids_by_endpoint[t["endpoint"].rstrip("/")].append(t["id"])
        self.by_endpoint = {e: self.by_id[ids[0]] for e, ids in self.ids_by_endpoint.items()}
        self._fetch_obs, self._fetch_changes, self._fetch_blobs = fetch_obs, fetch_changes, fetch_blobs
        self.obs: dict[str, list[dict]] = {}
        self.blobs: dict[str, Any] = {}

    def target(self, endpoint: str) -> dict[str, Any] | None:
        return self.by_endpoint.get(endpoint.rstrip("/"))

    def load_obs(self, ids: Iterable[str], start: str, end: str) -> None:
        want = sorted({i for i in ids if i not in self.obs})
        for i in want:
            self.obs[i] = []
        for r in self._fetch_obs(want, start, end):
            self.obs[r["target_id"]].append(r)
        for i in want:
            self.obs[i].sort(key=lambda r: r["observed_at"])
        need = {o[k] for i in want for o in self.obs[i] for k in ("toolset", "extras") if o.get(k)}
        missing = sorted(d for d in need if d not in self.blobs)
        if missing:
            self.blobs.update(self._fetch_blobs(missing))

    def changes(self, ids: list[str], start: str, end: str) -> list[dict]:
        return self._fetch_changes(ids, start, end)


def _chunks(items: list[str], n: int = 500) -> Iterable[list[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def from_store(store: Any, endpoints: Iterable[str]) -> Data:
    eps = sorted({v for e in endpoints for v in (e, e.rstrip("/"), e.rstrip("/") + "/")})
    targets: list[dict] = []
    with store.db.read() as tx:
        for part in _chunks(eps):
            targets += tx.execute(f"SELECT id, name, endpoint, transport, delisted FROM targets WHERE endpoint IN "
                                  f"({','.join('?' * len(part))})", part)

    def fetch_obs(ids: list[str], start: str, end: str) -> list[dict]:
        out: list[dict] = []
        with store.db.read() as tx:
            for part in _chunks(ids):
                out += tx.execute(
                    "SELECT target_id, observed_at, status, toolset, extras, protocol FROM observations "
                    f"WHERE target_id IN ({','.join('?' * len(part))}) AND observed_at >= ? AND observed_at <= ? "
                    "AND status = 'ok'", (*part, start, end))
        return out

    def fetch_changes(ids: list[str], start: str, end: str) -> list[dict]:
        out: list[dict] = []
        with store.db.read() as tx:
            for part in _chunks(ids):
                marks = ",".join("?" * len(part))
                for kind, table in (("tools", "changes"), ("extras", "extras_changes")):
                    for r in tx.execute(f"SELECT target_id, observed_at FROM {table} WHERE target_id IN ({marks}) "
                                        "AND observed_at > ? AND observed_at <= ?", (*part, start, end)):
                        out.append({**r, "kind": kind})
        return out

    def fetch_blobs(digests: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        with store.db.read() as tx:
            for part in _chunks(digests, 200):
                for r in tx.execute(f"SELECT digest, body FROM blobs WHERE digest IN ({','.join('?' * len(part))})",
                                    part):
                    out[r["digest"]] = json.loads(r["body"])
        return out

    return Data(targets, fetch_obs, fetch_changes, fetch_blobs)


def tools_of(data: Data, o: dict | None, *, extras_only: bool = False) -> dict[str, dict] | None:
    """HISTOR's view of one read: the four digested fields plus the members kept beside them."""
    if not o:
        return None
    tools: dict[str, dict] = {}
    if not extras_only:
        if not o.get("toolset") or o["toolset"] not in data.blobs:
            return None
        tools = {t["name"]: dict(t) for t in data.blobs[o["toolset"]]}
    ex = data.blobs.get(o.get("extras")) if o.get("extras") else None
    for name, e in ((ex or {}).get("tools") or {}).items():
        if extras_only:
            tools[name] = {k: v for k, v in e.items() if k != "members"}
        elif name in tools:
            tools[name].update({k: v for k, v in e.items() if k != "members"})
    return tools


# --- the peer's read times ----------------------------------------------------------------------------------

def reads(day: str, peer: dict = PEER) -> tuple[tuple[dt.datetime, dt.datetime], tuple[dt.datetime, dt.datetime]]:
    """(start, end) of the peer's read before ``day`` and of its read on ``day``."""
    d = dt.date.fromisoformat(day)
    run = dt.timedelta(minutes=peer["run_minutes"])
    start = dt.datetime.combine(d, dt.time.fromisoformat(peer["run_start"]), dt.timezone.utc)
    prev = start - dt.timedelta(days=1)
    first = tuple(ts(x) for x in peer["first_run"])
    if prev.date() <= first[0].date():
        return (first[0], first[1]), (start, start + run)
    return (prev, prev + run), (start, start + run)


# --- theirs against HISTOR ----------------------------------------------------------------------------------

def check_forward(data: Data, day: str, rec: dict, peer: dict = PEER) -> dict:
    (p0, _), (c0, _) = reads(day, peer)
    out = {"day": day, "endpoint": rec["endpoint"], "server": (rec.get("servers") or [""])[0]}
    tgt = data.target(rec["endpoint"])
    if not tgt:
        return out | {"verdict": "not in HISTOR"}
    lo, hi = p0 - SLACK, c0 + SLACK
    seq = [(ts(o["observed_at"]), v) for o in data.obs.get(tgt["id"], [])
           if lo <= ts(o["observed_at"]) <= hi and (v := tools_of(data, o)) is not None]
    out["historReads"] = len(seq)
    if not seq:
        return out | {"verdict": "HISTOR has no definitions in the window"}
    if seq[0][0] > c0 + dt.timedelta(minutes=peer["run_minutes"]):
        # HISTOR's first copy came after their read (a server HISTOR could not read until then): it cannot have
        # seen the old side, which is not the same as having missed the change.
        return out | {"verdict": "HISTOR has no definitions before their read"}
    items: list[tuple[str, Callable, Callable]] = []
    for name in rec.get("added") or []:
        items.append((f"tool added: {name}", lambda v, n=name: n not in v, lambda v, n=name: n in v))
    for name in rec.get("removed") or []:
        items.append((f"tool removed: {name}", lambda v, n=name: n in v, lambda v, n=name: n not in v))
    for ch in rec.get("changed") or []:
        for field, diffs in ch["fields"].items():
            for x in diffs:
                old = x.get("old") if x["op"] != "added" else None
                new = x.get("new") if x["op"] != "removed" else None
                old = MISSING if old is None else old  # the peer writes an absent member as null
                new = MISSING if new is None else new

                def val(v, n=ch["name"], p=x["path"], f=field):
                    if n not in v:
                        return None
                    if f in ("inputSchema", "outputSchema") and isinstance(v[n].get(f), dict):
                        return at_path({f: unordered(inline_refs(v[n][f]))}, p)
                    return at_path(v[n], p)

                def is_old(v, o=old, val=val):
                    got = val(v)
                    return got is not None and ((got is MISSING) if o is MISSING else (got is not MISSING and same(got, o)))

                def is_new(v, nw=new, val=val):
                    got = val(v)
                    return got is not None and ((got is MISSING) if nw is MISSING else (got is not MISSING and same(got, nw)))
                items.append((f"{field} {x['op']}: {ch['name']} {x['path']}", is_old, is_new))
    seen = 0
    for _, is_old, is_new in items:
        had_old = False
        for _, v in seq:
            if is_old(v):
                had_old = True
            elif had_old and is_new(v):
                seen += 1
                break
    out["items"], out["itemsSeenByHistor"] = len(items), seen
    out["verdict"] = ("server instructions only (HISTOR does not record them)" if not items else
                      "same change in HISTOR" if seen == len(items) else
                      "partly in HISTOR" if seen else "not seen as a change in HISTOR")
    return out


# --- HISTOR against theirs ----------------------------------------------------------------------------------

def _peer_view(tools: dict[str, dict], noise: bool = False) -> dict:
    out = {}
    for name, t in tools.items():
        v = {k: t[k] for k in PEER_FIELDS if k in t}
        for k in ("inputSchema", "outputSchema"):
            if isinstance(v.get(k), dict):
                v[k] = inline_refs(v[k], drop_defs=True)
        out[name] = canon(v, noise)
    return out


def _describe(a: dict, b: dict) -> dict:
    fields: collections.Counter = collections.Counter()
    for n in set(a) & set(b):
        for k in set(a[n]) | set(b[n]):
            if a[n].get(k) != b[n].get(k):
                fields[k] += 1
    return {"added": sorted(set(b) - set(a))[:8], "removed": sorted(set(a) - set(b))[:8], "changedFields": dict(fields)}


def check_reverse(data: Data, tid: str, day: str, events: list[dict], listed_days: set[str],
                  robots: Callable[[str], bool], peer: dict = PEER) -> dict:
    t = data.by_id[tid]
    (p0, p1), (c0, c1) = reads(day, peer)
    obs = data.obs.get(tid, [])

    def last_ok(at: dt.datetime) -> dict | None:
        before = [o for o in obs if ts(o["observed_at"]) <= at]
        return before[-1] if before else None

    oa, ob = last_ok(p1), last_ok(c1)
    row: dict[str, Any] = {"day": day, "endpoint": t["endpoint"], "server": t["name"],
                           "histor": [{"at": e["observed_at"], "kind": e["kind"]} for e in events]}
    if not oa or not ob:
        return row | {"why": "HISTOR has no successful read before one of the two peer reads"}
    row["protocol"] = sorted({oa.get("protocol") or "", ob.get("protocol") or ""})

    def allowed() -> bool:  # robots.txt is fetched only where it decides the answer
        row["robotsAllowsPeer"] = robots(t["endpoint"])
        return row["robotsAllowsPeer"]
    if not oa.get("toolset") or not ob.get("toolset"):
        # No four-field copy on one side: compare the members kept beside the digest only.
        xa, xb = tools_of(data, oa, extras_only=True) or {}, tools_of(data, ob, extras_only=True) or {}
        if not xa or not xb:
            return row | {"why": "HISTOR held no copy at one of the two reads to compare"}
        va, vb = _peer_view(xa), _peer_view(xb)
        if va == vb:
            extra = sorted({k for n in set(xa) & set(xb) for k in set(xa[n]) | set(xb[n])
                            if xa[n].get(k) != xb[n].get(k)} - set(PEER_FIELDS))
            return row | {"why": "only members the peer does not hash changed" if extra else
                          "back to the same definitions by the second read", "members": extra}
        why = "a change in the fields the peer hashes" if allowed() else "robots.txt disallows the endpoint"
        return row | {"why": why, "detail": {"titleOrAnnotations": sorted(n for n in set(va) | set(vb)
                                                                           if va.get(n) != vb.get(n))[:8]}}
    fa, fb = tools_of(data, oa) or {}, tools_of(data, ob) or {}
    a, b = _peer_view(fa), _peer_view(fb)
    first = min(e["observed_at"] for e in events)
    before = [o["observed_at"] for o in obs if o["observed_at"] < first]
    early = not before or ts(before[-1]) <= p1
    in_run = [e["observed_at"] for e in events if p0 < ts(e["observed_at"]) <= p1 or c0 < ts(e["observed_at"]) <= c1]
    first_day = reads(day, peer)[0][0] == ts(peer["first_run"][0])
    if fa == fb:
        return row | {"why": "back to the same definitions by the second read"}
    if a == b:
        extra = sorted({k for n in set(fa) & set(fb) for k in set(fa[n]) | set(fb[n])
                        if fa[n].get(k) != fb[n].get(k)} - set(PEER_FIELDS))
        return row | {"why": "only members the peer does not hash changed", "members": extra}
    row["detail"] = _describe(a, b)
    if _peer_view(fa, True) == _peer_view(fb, True):
        return row | {"why": "only dates, version strings, numbers or whitespace changed"}
    if not allowed():
        return row | {"why": "robots.txt disallows the endpoint"}
    if early and first_day:
        return row | {"why": "may predate the peer's first read (HISTOR's reads were hours apart)"}
    if early:
        prev_day = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        return row | {"why": "listed by the peer the day before" if prev_day in listed_days else
                      "a change in the fields the peer hashes, this day or the day before (HISTOR's reads were hours apart)"}
    if in_run:
        return row | {"why": "changed during a peer run"}
    return row | {"why": "a change in the fields the peer hashes"}


# --- running both -------------------------------------------------------------------------------------------

LIKELY_MISS = ("a change in the fields the peer hashes",
               "a change in the fields the peer hashes, this day or the day before (HISTOR's reads were hours apart)")


def compare(data: Data, doc: dict, panel: list[str], days: list[str], robots: Callable[[str], bool],
            peer: dict = PEER) -> dict:
    listed = collections.defaultdict(set)
    for d in doc["days"]:
        for c in d["changes"]:
            if c["op"] == "tools_changed":
                listed[c["endpoint"].rstrip("/")].add(d["day"])
    first = min(reads(d, peer)[0][0] for d in days) - SLACK
    last = max(reads(d, peer)[1][1] for d in days) + SLACK
    by_day = {d["day"]: d for d in doc["days"]}

    fwd_ids = [t["id"] for d in days for c in by_day.get(d, {}).get("changes", [])
               if c["op"] == "tools_changed" and (t := data.target(c["endpoint"]))]
    data.load_obs(fwd_ids, iso(first), iso(last))
    forward = [check_forward(data, d, c, peer) for d in days for c in by_day.get(d, {}).get("changes", [])
               if c["op"] == "tools_changed"]

    panel_ids = sorted({i for e in panel for i in data.ids_by_endpoint.get(e.rstrip("/"), [])})
    events: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in data.changes(panel_ids, iso(min(reads(d, peer)[0][1] for d in days)), iso(max(reads(d, peer)[1][0] for d in days))):
        for d in days:
            (_, p1), (c0, _) = reads(d, peer)
            if p1 < ts(r["observed_at"]) <= c0:
                events[(r["target_id"], d)].append(r)
    endpoint_of = {i: t["endpoint"].rstrip("/") for i, t in data.by_id.items()}
    todo = {k: v for k, v in events.items() if k[1] not in listed[endpoint_of[k[0]]]}
    data.load_obs({tid for tid, _ in todo}, iso(first), iso(last))
    reverse = [check_reverse(data, tid, d, ev, listed[endpoint_of[tid]], robots, peer)
               for (tid, d), ev in sorted(todo.items())]

    fv = collections.Counter(r["verdict"] for r in forward)
    rv = collections.Counter(r["why"] for r in reverse)
    return {
        "peer": peer["name"], "days": days, "panel": len(panel),
        "panelInHistor": sum(1 for e in panel if data.target(e)),
        "forward": {"records": len(forward), "verdicts": dict(fv.most_common()),
                    "items": sum(r.get("items", 0) for r in forward),
                    "itemsSeenByHistor": sum(r.get("itemsSeenByHistor", 0) for r in forward), "rows": forward},
        "reverse": {"endpointDaysWithHistorChange": len(events), "alsoInPeerLog": len(events) - len(todo),
                    "notInPeerLog": len(todo), "why": dict(rv.most_common()),
                    "membersNotHashed": dict(collections.Counter(m for r in reverse for m in r.get("members", [])).most_common()),
                    "likelyMisses": [r for r in reverse if r["why"] in LIKELY_MISS], "rows": reverse},
    }


def readme(result: dict, inputs: dict[str, str], now: str, peer: dict = PEER) -> str:
    f, r = result["forward"], result["reverse"]
    lines = [f"# HISTOR and the {peer['name']} MCP tool-change log, {result['days'][0]} to {result['days'][-1]}", "",
             f"Generated {now} by `python -m histor.peer_xcheck`, which HISTOR runs after each weekly snapshot. "
             "The classification is automatic and has not been reviewed by a person; read a likely miss as a lead.", "",
             f"{peer['name']} ({peer['page']}) reads a fixed panel of {result['panel']:,} endpoints once a day from "
             f"{peer['run_start'][:5]}Z and publishes daily diffs. HISTOR reads the same endpoints every 30 minutes "
             f"to 6 hours and keeps every definition. {result['panelInHistor']:,} of the panel endpoints are HISTOR "
             "targets.", "",
             "## Their log against HISTOR", "",
             f"For each of their {f['records']} tool-change records, did HISTOR see the old value and then the new one "
             "between their two reads, path by path? (Local `$ref` resolved and lists sorted, as their diffs do.)", "",
             "| Records | Verdict |", "|---:|---|"]
    lines += [f"| {n} | {v} |" for v, n in f["verdicts"].items()]
    lines += ["", f"Field-level items: {f['itemsSeenByHistor']:,} of {f['items']:,} seen by HISTOR.", "",
              "## HISTOR against their panel", "",
              f"HISTOR recorded a change on {r['endpointDaysWithHistorChange']} endpoint-days of the panel; "
              f"{r['alsoInPeerLog']} are in their log. For the other {r['notInPeerLog']}, HISTOR's definitions at "
              "the times of their two reads were compared:", "", "| Endpoint-days | Why their daily diff does not show it |",
              "|---:|---|"]
    lines += [f"| {n} | {w} |" for w, n in r["why"].items()]
    if r["membersNotHashed"]:
        lines += ["", "Members they do not hash that changed: " +
                  ", ".join(f"`{m}` ({n})" for m, n in r["membersNotHashed"].items()) + "."]
    lines += ["", "### Likely misses on their side", ""]
    if r["likelyMisses"]:
        for m in r["likelyMisses"]:
            when = ", ".join(e["at"] for e in m["histor"][:3])
            lines.append(f"- {m['endpoint']} ({m['day']}): {m['why']}; HISTOR recorded it at {when}; "
                         f"{json.dumps(m.get('detail'), ensure_ascii=False)[:300]}")
    else:
        lines.append("None.")
    lines += ["", "## Files", "", "- `results.json` — every record and endpoint-day with its verdict.",
              "- `inputs/` — the two files of theirs that were read: " +
              ", ".join(f"`{k}` (sha256 {v[:16]}…)" for k, v in inputs.items()) + ".",
              "- `SHA256SUMS`.", "", "## Method and limits", "",
              "- Their read times: daily runs from 02:50Z of about ten minutes; their first run 2026-10-30 "
              "10:49:43–10:59:39Z.",
              "- HISTOR's state at a read is its newest successful read at or before it. Where HISTOR's reads were "
              "hours apart around theirs, the order of the change and their read is unknown; such cases are named so.",
              "- robots.txt is read now, not as it was on the day, and matched as RFC 9309 says (longest match, Allow on "
              "a tie, `*` and `$`), for their agent's group or `*`.",
              "- HISTOR did not digest tool lists with fractional numbers before 2026-10-31 16:42Z; for those, only the "
              "members it keeps beside the digest (title, annotations, `_meta`, …) are compared.",
              "- HISTOR does not record server `instructions`; records that change only those are named so.", "",
              "License: CC BY 4.0. Code: `histor/peer_xcheck.py` in HISTOR's source."]
    return "\n".join(lines) + "\n"


def run(store: Any, out: Path, *, days: int = 7, http: Any = None, peer: dict = PEER) -> dict:
    """Fetch the peer's files, compare, and write OUTDIR. ``http`` is an httpx-like client (tests pass a mock)."""
    import httpx

    from histor import __version__

    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists")
    agent = f"histor/{__version__} (+https://histor.modelmarket.dev/#crawler; peer cross-check)"
    client = http or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": agent})

    def get(url: str, limit: int = MAX_FILE) -> bytes:
        resp = client.get(url)
        resp.raise_for_status()
        body = resp.content
        if len(body) > limit:
            raise ValueError(f"{url} is larger than {limit} bytes")
        return body

    raw_changes, raw_panel = get(peer["changes"]), get(peer["panel"])
    doc = json.loads(raw_changes)
    panel = [l.strip() for l in raw_panel.decode("utf-8", "replace").splitlines() if l.strip() and not l.startswith("#")]
    all_days = sorted(d["day"] for d in doc["days"])
    chosen = all_days[-days:]
    endpoints = set(panel) | {c["endpoint"] for d in doc["days"] for c in d["changes"]}
    data = from_store(store, endpoints)

    texts: dict[str, str | None] = {}

    def robots(url: str) -> bool:
        from histor.netguard import BlockedAddress, resolve_public
        host = urlsplit(url).hostname or ""
        if host not in texts:
            texts[host] = None
            try:
                resolve_public(f"https://{host}/robots.txt")
                resp = client.get(f"https://{host}/robots.txt")
                if resp.status_code == 200 and len(resp.content) <= 512 * 1024:
                    texts[host] = resp.text
            except (BlockedAddress, httpx.HTTPError, ValueError):
                pass
        return robots_allows(texts[host], url, peer["agent"])

    result = compare(data, doc, panel, chosen, robots, peer)
    result["robotsRead"] = {h: (hashlib.sha256(t.encode()).hexdigest() if t else None) for h, t in sorted(texts.items())}
    now = iso(dt.datetime.now(dt.timezone.utc))
    result["generatedAt"] = now
    inputs = {"changes.json.gz": hashlib.sha256(raw_changes).hexdigest(), "panel.txt.gz": hashlib.sha256(raw_panel).hexdigest()}

    out.mkdir(parents=True)
    (out / "inputs").mkdir()
    (out / "inputs" / "changes.json.gz").write_bytes(gzip.compress(raw_changes, mtime=0))
    (out / "inputs" / "panel.txt.gz").write_bytes(gzip.compress(raw_panel, mtime=0))
    (out / "results.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")
    (out / "README.md").write_text(readme(result, inputs, now, peer))
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out).as_posix()}"
            for p in sorted(out.rglob("*")) if p.is_file()]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return result


def main(args: list[str]) -> int:
    import sys

    paths = [a for a in args if not a.startswith("-")]
    days = 7
    for a in args:
        if a.startswith("--days="):
            days = int(a.split("=", 1)[1])
    if len(paths) != 1:
        print("usage: python -m histor.peer_xcheck OUTDIR [--days=N]   (OUTDIR must not exist)", file=sys.stderr)
        return 2
    from histor.config import load_settings
    from histor.dataset import open_readonly

    store, _key, backend = open_readonly(load_settings())
    try:
        result = run(store, Path(paths[0]), days=days)
    finally:
        backend.close()
    print(json.dumps({"dir": paths[0], "days": result["days"], "forward": result["forward"]["verdicts"],
                      "reverse": result["reverse"]["why"], "likelyMisses": len(result["reverse"]["likelyMisses"])}))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
