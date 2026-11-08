"""Cross-checks with other public observers of MCP tool lists, configured as one list.

Every observer HISTOR compares itself with is one entry in a JSON list (deploy/peers.json, installed as
/etc/histor/peers.json). Adding an observer is adding an entry; nothing else changes. An entry:

    slug       short lower-case name; the results go to /datasets/peers/<slug>/<YYYY-MM-DD>/
    name       display name;  operator, contact, page, since: optional, shown in the index
    method     "digest"     — compare the observer's tool-set digests with HISTOR's timeline
                              (histor/digest_xcheck.py), run by this module every day
               "field-diff" — compare the observer's field-level change log with HISTOR's records, both ways
                              (histor/peer_xcheck.py, ScraperWatch); it reads HISTOR's database
    sources    for "digest": [{"name": "feed", "url": "https://…"}, …], each a file the observer publishes in a
               format digest_xcheck reads (rows in JSON, JSON Lines or CSV, or CrawlCheck's change feed)
    window_hours  for "digest": how far from the observer's read HISTOR may have seen the same digest (24)
    changes, panel, agent, run_start, run_minutes, first_run, days
               for "field-diff": the observer's change log and endpoint panel (https), the user agent its robots.txt
               rules name, when its daily run starts (HH:MM:SS UTC) and how long it takes, its first run (two RFC 3339
               times, the baseline of its first diff) and how many of its newest days each run compares (1)
    aliases    older folder names under /datasets/ that should keep pointing at /datasets/peers/<slug>/

    python -m histor.peers --config peers.json --out DIR [--published DIR] [--histor URL] [--day YYYY-MM-DD]
                           [--peer SLUG] [--method digest|field-diff]

writes DIR/<slug>/ for every peer run (a "digest" peer: per source <name>.input.*, <name>.report.json,
<name>.report.md and SHA256SUMS; a "field-diff" peer: README.md, results.json, inputs/ and SHA256SUMS) and
DIR/index.json, which lists every peer; for those not run now it reads the newest day and its results from
--published (the live /datasets/peers/). deploy/peers_check.sh runs the two methods in separate containers (the
"digest" one has no database, the "field-diff" one has no host network) and moves the results into place. Exit
status: 0, or 1 when a source could not be fetched or read or a peer failed (the others are still written);
disagreements are a result, not a failure. "digest" needs the standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:  # inside the package, and when mounted next to it into an older image
    from histor import digest_xcheck as dx
except ImportError:  # pragma: no cover - a copy of both files run by path
    import digest_xcheck as dx  # type: ignore[no-redef]

METHODS = ("digest", "field-diff")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_SOURCE = 16 * 1024 * 1024
UA = "histor-peers/1 (+https://histor.modelmarket.dev/)"
SHOWN = ("name", "operator", "contact", "page", "since", "method", "schedule")


class ConfigError(ValueError):
    pass


def load_config(text: str) -> list[dict]:
    """The peer list, checked: a JSON list of entries with unique slugs, a known method and, for "digest", https
    sources with unique names."""
    try:
        peers = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"not JSON: {exc}") from exc
    if not isinstance(peers, list) or not peers:
        raise ConfigError("the config must be a non-empty JSON list")
    seen: set[str] = set()
    for i, p in enumerate(peers):
        where = f"entry {i}"
        if not isinstance(p, dict):
            raise ConfigError(f"{where} is not an object")
        slug = p.get("slug")
        if not isinstance(slug, str) or not SLUG.match(slug):
            raise ConfigError(f"{where}: slug must match {SLUG.pattern}")
        where = f"peer {slug}"
        if slug in seen:
            raise ConfigError(f"{where}: slug used twice")
        seen.add(slug)
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            raise ConfigError(f"{where}: name is required")
        if p.get("method") not in METHODS:
            raise ConfigError(f"{where}: method must be one of {', '.join(METHODS)}")
        for alias in p.get("aliases", []):
            if not isinstance(alias, str) or not SLUG.match(alias) or alias in ("peers", "latest"):
                raise ConfigError(f"{where}: bad alias {alias!r}")
        if p["method"] == "digest":
            sources = p.get("sources")
            if not isinstance(sources, list) or not sources:
                raise ConfigError(f"{where}: a digest peer needs sources")
            names: set[str] = set()
            for s in sources:
                if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not SLUG.match(s["name"]):
                    raise ConfigError(f"{where}: every source needs a name matching {SLUG.pattern}")
                if s["name"] in names:
                    raise ConfigError(f"{where}: source {s['name']} named twice")
                names.add(s["name"])
                if not isinstance(s.get("url"), str) or not s["url"].startswith("https://"):
                    raise ConfigError(f"{where}: source {s['name']} needs an https url")
            w = p.get("window_hours", 24)
            if not isinstance(w, (int, float)) or not 0 < w <= 24 * 14:
                raise ConfigError(f"{where}: window_hours must be between 0 and 336")
        else:
            for key in ("changes", "panel"):
                if not isinstance(p.get(key), str) or not p[key].startswith("https://"):
                    raise ConfigError(f"{where}: a field-diff peer needs an https {key} url")
            if not isinstance(p.get("agent"), str) or not p["agent"].strip():
                raise ConfigError(f"{where}: a field-diff peer needs the agent its robots.txt rules name")
            if not isinstance(p.get("run_start"), str) or not re.fullmatch(r"[0-2]\d:[0-5]\d:[0-5]\d", p["run_start"]):
                raise ConfigError(f"{where}: run_start must be HH:MM:SS")
            if not isinstance(p.get("run_minutes"), int) or not 0 < p["run_minutes"] <= 24 * 60:
                raise ConfigError(f"{where}: run_minutes must be a positive number of minutes")
            first = p.get("first_run")
            if not isinstance(first, list) or len(first) != 2 or not all(isinstance(x, str) for x in first):
                raise ConfigError(f"{where}: first_run must be two RFC 3339 times (the observer's first read)")
            for x in first:
                try:
                    dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ConfigError(f"{where}: first_run must be two RFC 3339 times (the observer's first read)") from exc
            d = p.get("days", 1)
            if not isinstance(d, int) or not 1 <= d <= 31:
                raise ConfigError(f"{where}: days must be between 1 and 31")
    return peers


def fetch_text(url: str, timeout: float = 120) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(MAX_SOURCE + 1)
    if len(body) > MAX_SOURCE:
        raise ValueError(f"larger than {MAX_SOURCE} bytes")
    return body.decode("utf-8")


def _input_name(source: dict) -> str:
    path = source["url"].split("?", 1)[0]
    ext = ".jsonl" if path.endswith(".jsonl") else ".csv" if path.endswith(".csv") else ".json"
    return f"{source['name']}.input{ext}"


def run_peer(peer: dict, out: Path, *, histor: str, get_text: Callable[[str], str] = fetch_text,
             get_json: Callable[[str], Any] = dx.fetch_json, pause: float = 0.2) -> dict:
    """Run one "digest" peer into OUT: each source fetched, kept as read, and compared. Returns the index entry's
    result part: per source the counts, or the error that stopped it."""
    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for source in peer["sources"]:
        name = source["name"]
        try:
            text = get_text(source["url"])
            (out / _input_name(source)).write_text(text, encoding="utf-8")
            rows = dx.read_rows(text)
            report = dx.xcheck(rows, get_json, histor, float(peer.get("window_hours", 24)), pause)
        except Exception as exc:  # noqa: BLE001 - one source failing must not stop the others
            results[name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            continue
        report["peer"] = peer["slug"]
        report["source"] = {"name": name, "url": source["url"]}
        (out / f"{name}.report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (out / f"{name}.report.md").write_text(dx.markdown(report), encoding="utf-8")
        results[name] = {"rows": report["rows_in"], "counts": report["counts"]}
    files = sorted(p for p in out.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files)
    (out / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return results


def field_diff_summary(result: dict) -> dict:
    """The part of a field-diff result (histor/peer_xcheck.py) the index shows."""
    return {"days": result.get("days"), "forward": (result.get("forward") or {}).get("verdicts"),
            "reverse": (result.get("reverse") or {}).get("why"),
            "likelyMisses": len((result.get("reverse") or {}).get("likelyMisses") or [])}


def run_field_diff(peer: dict, out: Path) -> dict:
    """Run one "field-diff" peer into OUT (which must not exist) against HISTOR's database, read-only."""
    from histor import peer_xcheck as px
    from histor.config import load_settings
    from histor.dataset import open_readonly

    spec = {"name": peer["name"], "slug": peer["slug"], "page": peer.get("page", ""), "changes": peer["changes"],
            "panel": peer["panel"], "agent": peer["agent"], "run_start": peer["run_start"],
            "run_minutes": peer["run_minutes"], "first_run": tuple(peer["first_run"])}
    store, _key, backend = open_readonly(load_settings())
    try:
        result = px.run(store, out, days=int(peer.get("days", 1)), peer=spec)
    finally:
        backend.close()
    return field_diff_summary(result)


def published_results(published: Path | None, peer: dict, day: str | None) -> dict | None:
    """The results of a peer's published day, read back for the index."""
    if published is None or not day:
        return None
    folder = published / peer["slug"] / day
    try:
        if peer["method"] == "digest":
            out = {}
            for f in sorted(folder.glob("*.report.json")):
                r = json.loads(f.read_text(encoding="utf-8"))
                out[f.name[: -len(".report.json")]] = {"rows": r.get("rows_in"), "counts": r.get("counts")}
            return out or None
        return field_diff_summary(json.loads((folder / "results.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def newest_day(published: Path | None, slug: str) -> str | None:
    """The newest dated folder of a peer already published (its `latest` link, else the highest date)."""
    if published is None:
        return None
    root = published / slug
    latest = root / "latest"
    if latest.is_symlink():
        return Path(latest.readlink()).name
    days = sorted(p.name for p in root.glob("20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]") if p.is_dir()) if root.is_dir() else []
    return days[-1] if days else None


def build_index(peers: list[dict], ran: dict[str, dict], day: str, published: Path | None, now: str) -> dict:
    entries = []
    for p in peers:
        e: dict[str, Any] = {"slug": p["slug"], **{k: p[k] for k in SHOWN if k in p}}
        e["path"] = f"/datasets/peers/{p['slug']}/"
        if p["method"] == "digest":
            e["sources"] = [{"name": s["name"], "url": s["url"]} for s in p["sources"]]
            e["window_hours"] = p.get("window_hours", 24)
        if p.get("aliases"):
            e["aliases"] = [f"/datasets/{a}/" for a in p["aliases"]]
        if p["slug"] in ran:
            e["last_day"] = day
            e["last_results"] = ran[p["slug"]]
        else:
            e["last_day"] = newest_day(published, p["slug"])
            e["last_results"] = published_results(published, p, e["last_day"])
        entries.append(e)
    return {"kind": "histor-peer-cross-checks", "v": 1, "generated_at": now,
            "about": "Observers HISTOR compares its records with, and where each comparison is published. "
                     "Method and verdicts: https://github.com/alexar76/histor/blob/main/docs/cross-check.md",
            "peers": entries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True, help="directory for this run's results and index.json")
    ap.add_argument("--published", help="the live peers directory, to list the newest day of peers not run now")
    ap.add_argument("--histor", default="https://histor.modelmarket.dev")
    ap.add_argument("--day", default=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d"))
    ap.add_argument("--peer", action="append", help="run only this slug (repeatable); the index still lists all")
    ap.add_argument("--method", choices=METHODS, action="append", help="run only peers with this method (repeatable)")
    ap.add_argument("--pause", type=float, default=0.2)
    a = ap.parse_args(argv)
    try:
        peers = load_config(Path(a.config).read_text(encoding="utf-8"))
    except (OSError, ConfigError) as exc:
        print(f"peers config: {exc}", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ran: dict[str, dict] = {}
    failed = False
    for p in peers:
        if (a.peer and p["slug"] not in a.peer) or (a.method and p["method"] not in a.method):
            continue
        if p["method"] == "field-diff":
            try:
                ran[p["slug"]] = run_field_diff(p, out / p["slug"])
            except Exception as exc:  # noqa: BLE001 - one peer failing must not stop the others
                failed = True
                print(f"{p['slug']}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
                continue
            r = ran[p["slug"]]
            print(f"{p['slug']}: days {r['days']}, forward {r['forward']}, reverse {r['reverse']}, "
                  f"likely misses {r['likelyMisses']}")
            continue
        ran[p["slug"]] = run_peer(p, out / p["slug"], histor=a.histor, pause=a.pause)
        for src, res in ran[p["slug"]].items():
            if "error" in res:
                failed = True
                print(f"{p['slug']}/{src}: {res['error']}", file=sys.stderr)
            else:
                c = res["counts"]
                print(f"{p['slug']}/{src}: {res['rows']} rows, {c['agree']} agree, {c['agree_nearby']} nearby, "
                      f"{c['disagree']} disagree")
    now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    index = build_index(peers, ran, a.day, Path(a.published) if a.published else None, now)
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
