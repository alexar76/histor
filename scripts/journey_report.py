"""Count accepted HISTOR browser events in operator-provided nginx access logs."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from histor.journey import EVENTS, SOURCES

_ROW = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "POST /api/v1/journey/'
    r'(?P<source>[a-z]+?)/(?P<event>[a-z_]+) HTTP/[0-9.]+" 204 \d+ "[^"]*" "(?P<ua>[^"]*)"'
)
_BOT = re.compile(r"bot|crawl|spider|slurp|preview|headless|lighthouse|curl|wget|python|go-http", re.IGNORECASE)


def summarize(lines: Iterable[str], *, start: datetime, end: datetime, own_ips: set[str]) -> dict:
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    qa: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    for line in lines:
        if "/api/v1/journey/" not in line:
            continue
        hit = _ROW.match(line)
        if hit is None:
            continue
        try:
            at = datetime.strptime(hit["time"], "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            continue
        source, event = hit["source"], hit["event"]
        if not start <= at < end or source not in SOURCES or event not in EVENTS:
            continue
        if source == "qa":
            qa[event] += 1
        elif hit["ip"] in own_ips:
            excluded["own"] += 1
        elif "mozilla" not in hit["ua"].lower() or _BOT.search(hit["ua"]):
            excluded["non_browser_or_known_bot"] += 1
        else:
            by_source[source][event] += 1

    totals = Counter({event: 0 for event in EVENTS})
    for counts in by_source.values():
        totals.update(counts)
    return {
        "fromInclusive": start.isoformat(), "untilExclusive": end.isoformat(),
        "externalBrowserEvents": dict(totals), "bySource": dict(by_source),
        "qaEvents": dict(qa), "excludedEvents": dict(excluded),
    }


def _utc(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            raise ValueError
        return dt.astimezone(UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use an ISO timestamp with timezone, e.g. 2026-11-11T12:00:00Z") from exc


def _lines(files: list[str]) -> Iterable[str]:
    for name in files:
        if name == "-":
            yield from sys.stdin
        elif name.endswith(".gz"):
            with gzip.open(name, "rt", encoding="utf-8", errors="replace") as f:
                yield from f
        else:
            with Path(name).open(encoding="utf-8", errors="replace") as f:
                yield from f


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", help="Non-overlapping combined logs, .gz logs, or - for stdin")
    parser.add_argument("--from", required=True, dest="start", type=_utc)
    parser.add_argument("--until", required=True, dest="end", type=_utc)
    args = parser.parse_args()
    if args.start >= args.end:
        parser.error("--until must be after --from")
    own = set(os.environ.get("AICOM_OWN_IPS", "").replace(",", " ").split()) | {"127.0.0.1", "::1"}
    print(json.dumps(summarize(_lines(args.logs), start=args.start, end=args.end, own_ips=own), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
