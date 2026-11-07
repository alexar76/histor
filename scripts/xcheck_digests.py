#!/usr/bin/env python3
"""Compare another observer's MCP tool-set digests with HISTOR's public record.

    python3 scripts/xcheck_digests.py THEIRS [--histor URL] [--window-hours 24] [--out report.json] [--md report.md]

THEIRS is a file path or an https URL holding JSON (a list, or an object with "servers"), JSON Lines or CSV, one row
per server read:

    name      registry name, e.g. io.github.owner/server     also: server, registry_name, serverName
    endpoint  the remote URL that was read                   also: url, remote
    digest    tool-set digest, SRI (sha256-<base64>) or hex   also: tool_set_digest, toolSetDigest,
                                                                    tools_jcs_sha256, jcs_tools_sha256
    read_at   when it was read, RFC 3339                     also: observed_at, checked_at, at

CrawlCheck's public change feed (https://crawlcheck.io/api/v1/mcp/changes, kind crawlcheck-mcp-tool-changes) is read
as it is: each event with an RFC 8785 digest gives a row for the list after the change (tools_jcs_sha256_after at
`at`) and, when present, one for the list before it (tools_jcs_sha256_before at previous_read_at).

The digest has to be the MTL/1 tool-set digest (histor/docs/labels.md), which CrawlCheck publishes as
jcs_v1.tools_sha256: name, description, inputSchema and outputSchema of every tool, sorted by name in UTF-16 code
units, RFC 8785, SHA-256. Any other digest of the list disagrees everywhere.

Each row is judged against HISTOR's timeline for that name and endpoint (GET /api/v1/servers/<id>, where <id> is the
first 16 hex of sha256(name + "\\n" + endpoint)). A server can change between two reads, so a different digest is a
disagreement only when HISTOR did not see the other one at all in the window around read_at:

    agree             HISTOR's digest in force at read_at is the same (between two of HISTOR's reads, either
                      neighbour counts)
    agree_nearby      not the one in force at read_at, but HISTOR saw that digest within the window
    disagree          HISTOR had a digest in the window and never this one
    histor_no_digest  HISTOR lists the endpoint but has no digest in the window (not read, or a list MTL/1 cannot
                      digest)
    not_listed        HISTOR does not list this name and endpoint
    bad_row           a field is missing or unreadable

Exit status: 0 with no disagree row, 1 with at least one, 2 when THEIRS cannot be read. Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

UA = "histor-xcheck/1 (+https://histor.modelmarket.dev/)"
VERDICTS = ("agree", "agree_nearby", "disagree", "histor_no_digest", "not_listed", "bad_row")
FIELDS = {
    "name": ("name", "server", "registry_name", "serverName"),
    "endpoint": ("endpoint", "url", "remote"),
    "digest": ("digest", "tool_set_digest", "toolSetDigest", "tools_jcs_sha256", "jcs_tools_sha256"),
    "read_at": ("read_at", "observed_at", "checked_at", "at"),
}
SRI = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
HEX = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


class NotListed(Exception):
    pass


def target_id(name: str, endpoint: str) -> str:
    return hashlib.sha256(f"{name}\n{endpoint}".encode()).hexdigest()[:16]


def normalise_digest(value: Any) -> str | None:
    """SRI as HISTOR writes it; a hex SHA-256 is converted. Anything else is None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if SRI.match(value):
        return value
    m = HEX.match(value)
    if m:
        return "sha256-" + base64.b64encode(bytes.fromhex(m.group(1))).decode("ascii")
    return None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _pick(row: dict, key: str) -> Any:
    for k in FIELDS[key]:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


def read_rows(text: str) -> list[dict]:
    """JSON list, {"servers": [...]}, JSON Lines, or CSV with a header row."""
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict) and data.get("kind") == "crawlcheck-mcp-tool-changes":
            return crawlcheck_feed_rows(data)
        if isinstance(data, dict):
            for key in ("servers", "rows", "items"):
                if isinstance(data.get(key), list):
                    return [r for r in data[key] if isinstance(r, dict)]
            return [data]
        rows = []
        for line in stripped.splitlines():
            line = line.strip()
            if line:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
        return rows
    return list(csv.DictReader(io.StringIO(text)))


def crawlcheck_feed_rows(feed: dict) -> list[dict]:
    """Rows from CrawlCheck's change feed: only events that carry the RFC 8785 digest (from 2026-11-07 on)."""
    rows = []
    seen = set()
    for ev in feed.get("events") or []:
        if not isinstance(ev, dict):
            continue
        for digest_key, time_key in (("tools_jcs_sha256_after", "at"), ("tools_jcs_sha256_before", "previous_read_at")):
            digest, at = ev.get(digest_key), ev.get(time_key)
            key = (ev.get("server"), ev.get("url"), digest, at)
            if digest and at and key not in seen:
                seen.add(key)
                rows.append({"name": ev.get("server"), "endpoint": ev.get("url"), "digest": digest, "read_at": at,
                             "source": "crawlcheck-change-feed", "event_id": ev.get("event_id")})
    return rows


def judge(digest: str, read_at: datetime, timeline: list[dict], window: timedelta) -> tuple[str, str | None, list[dict]]:
    """(verdict, HISTOR's digest at read_at or None, HISTOR's digests seen in the window)."""
    runs = []
    for row in timeline:
        d = row.get("toolSetDigest")
        start, end = parse_time(row.get("from")), parse_time(row.get("until"))
        if d and start and end:
            runs.append((start, end, d))
    runs.sort()
    lo, hi = read_at - window, read_at + window
    seen = [{"digest": d, "from": s.isoformat().replace("+00:00", "Z"), "until": e.isoformat().replace("+00:00", "Z")}
            for s, e, d in runs if s <= hi and e >= lo]
    covering = [d for s, e, d in runs if s <= read_at <= e]
    if covering:
        at_read = covering
    else:
        before = [d for s, e, d in runs if e < read_at]
        after = [d for s, e, d in runs if s > read_at]
        at_read = before[-1:] + after[:1]
    histor_at_read = at_read[0] if len(at_read) == 1 else (" or ".join(at_read) if at_read else None)
    if digest in at_read:
        return "agree", histor_at_read, seen
    if any(x["digest"] == digest for x in seen):
        return "agree_nearby", histor_at_read, seen
    if not seen and not at_read:
        return "histor_no_digest", None, seen
    if not seen:
        # HISTOR's nearest reads are outside the window: nothing to compare inside it.
        return "histor_no_digest", histor_at_read, seen
    return "disagree", histor_at_read, seen


def fetch_json(url: str, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotListed(url) from exc
        raise


def xcheck(rows: list[dict], fetch: Callable[[str], Any], histor: str, window_hours: float = 24,
           pause: float = 0.0) -> dict:
    window = timedelta(hours=window_hours)
    out = []
    records: dict[str, Any] = {}  # one HISTOR request per server, however many rows name it
    for raw in rows:
        name, endpoint = _pick(raw, "name"), _pick(raw, "endpoint")
        digest, read_at = normalise_digest(_pick(raw, "digest")), parse_time(_pick(raw, "read_at"))
        entry = {"name": name, "endpoint": endpoint, "digest": digest, "read_at": _pick(raw, "read_at")}
        if raw.get("event_id"):
            entry["event_id"] = raw["event_id"]
        missing = [k for k, v in (("name", name), ("endpoint", endpoint), ("digest", digest), ("read_at", read_at)) if not v]
        if missing:
            out.append({**entry, "verdict": "bad_row", "note": "missing or unreadable: " + ", ".join(missing)})
            continue
        tid = target_id(str(name), str(endpoint))
        if tid not in records:
            try:
                records[tid] = fetch(f"{histor.rstrip('/')}/api/v1/servers/{tid}")
            except NotListed:
                records[tid] = None
            if pause:
                time.sleep(pause)
        record = records[tid]
        if record is None:
            out.append({**entry, "id": tid, "verdict": "not_listed"})
            continue
        verdict, at_read, seen = judge(digest, read_at, record.get("timeline") or [], window)
        # Some servers change their list on every read (HISTOR reads a changing server about every 30 minutes),
        # so keep the run that matched and the runs nearest to read_at, and count the rest.
        near = sorted(seen, key=lambda s: abs((parse_time(s["from"]) - read_at).total_seconds()))
        kept = [s for s in seen if s["digest"] == digest][:1] + [s for s in near if s["digest"] != digest][:4]
        out.append({**entry, "id": tid, "verdict": verdict, "histor_at_read": at_read,
                    "histor_seen": sorted(kept, key=lambda s: s["from"]), "histor_digests_in_window": len({s["digest"] for s in seen})})
    counts = {v: sum(1 for r in out if r["verdict"] == v) for v in VERDICTS}
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "histor": histor, "window_hours": window_hours, "rows_in": len(rows), "counts": counts, "rows": out,
    }


def markdown(report: dict) -> str:
    c = report["counts"]
    lines = [f"# Tool-set digest cross-check with HISTOR, {report['generated_at']}", "",
             f"{report['rows_in']} servers; window ±{report['window_hours']:g} h around each read.", "",
             "| verdict | servers |", "|---|---|"]
    lines += [f"| {v} | {c[v]} |" for v in VERDICTS]
    odd = [r for r in report["rows"] if r["verdict"] not in ("agree", "agree_nearby")]
    if odd:
        lines += ["", "| verdict | server | endpoint | theirs | HISTOR at read |", "|---|---|---|---|---|"]
        for r in odd:
            lines.append(f"| {r['verdict']} | {r.get('name') or ''} | {r.get('endpoint') or ''} | {r.get('digest') or ''} "
                         f"| {r.get('histor_at_read') or r.get('note') or ''} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("theirs", help="file path or https URL")
    ap.add_argument("--histor", default="https://histor.modelmarket.dev")
    ap.add_argument("--window-hours", type=float, default=24)
    ap.add_argument("--out")
    ap.add_argument("--md")
    ap.add_argument("--pause", type=float, default=0.2, help="seconds between HISTOR requests")
    a = ap.parse_args(argv)
    try:
        if a.theirs.startswith("https://"):
            req = urllib.request.Request(a.theirs, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8")
        else:
            with open(a.theirs, encoding="utf-8") as fh:
                text = fh.read()
        rows = read_rows(text)
    except (OSError, ValueError) as exc:
        print(f"cannot read {a.theirs}: {exc}", file=sys.stderr)
        return 2
    report = xcheck(rows, fetch_json, a.histor, a.window_hours, a.pause)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    md = markdown(report)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as fh:
            fh.write(md)
    print(md, end="")
    return 1 if report["counts"]["disagree"] else 0


if __name__ == "__main__":
    sys.exit(main())
