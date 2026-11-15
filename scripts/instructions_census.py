#!/usr/bin/env python3
"""How many remote MCP servers send server-level ``instructions``, and what they hold.

A one-off read with HISTOR's own client: the same User-Agent, the same discover-then-initialize sequence and
tools/list drain, the same address guard (no private addresses) and the same robots.txt policy as the crawler.
The only addition is that the ``instructions`` member of the ``server/discover`` or ``initialize`` result is
kept instead of dropped.

Input: JSON Lines on stdin, one target per line: {"id", "endpoint", "host", "description", "toolCount"} (the
caller picks which targets; the 2026-11-15 run took targets HISTOR currently reads with status ok, no skip
reason, listed, at most 3 per host). Endpoints of one host are read one after another, never in parallel.

Output: JSON Lines on stdout, one per target: status, era, protocol, tool count, and for ``instructions``:
present / type / length / sha256 / the text itself (public server output, kept for the analysis).
Stdlib + histor. Run with PYTHONPATH pointing at the histor package, e.g. in a throw-away container from the
service image:  python instructions_census.py --workers 24 < targets.jsonl > out.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from histor import mcpclient
from histor.mcpclient import USER_AGENT, McpReader
from histor.robots import Fetcher as RobotsFetcher
from histor.robots import Policy

_seen = threading.local()
_complete = mcpclient._complete


def _capturing_complete(message, method):
    result = _complete(message, method)
    if method in ("initialize", "server/discover"):
        _seen.results[method] = result
    return result


mcpclient._complete = _capturing_complete  # module global: list_tools looks it up at call time


def read(reader: McpReader, target: dict) -> dict:
    _seen.results = {}
    started = time.monotonic()
    obs = reader.list_tools(target["endpoint"])
    seconds = round(time.monotonic() - started, 2)
    results = _seen.results
    # The handshake that produced the observation: discover for a modern server, else initialize.
    method = "server/discover" if "server/discover" in results and "initialize" not in results else "initialize"
    result = results.get(method) or {}
    present = "instructions" in result
    value = result.get("instructions")
    out = {
        "id": target["id"], "host": target["host"], "endpoint": target["endpoint"],
        "status": obs.status, "detail": obs.detail[:200], "seconds": seconds,
        "era": "modern" if method == "server/discover" and results else ("legacy" if results else None),
        "protocol": obs.protocol_version, "server": obs.server_info.get("name") if obs.server_info else None,
        "toolCount": len(obs.tools) if obs.tools is not None else None,
        "registryToolCount": target.get("toolCount"), "registryDescription": target.get("description"),
        "handshake": bool(results),
        "instructionsPresent": present,
        "instructionsType": type(value).__name__ if present else None,
    }
    if isinstance(value, str):
        out.update(instructionsLength=len(value), instructionsSha256=hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest(),
                   instructions=value[:20000])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--timeout", type=float, default=20.0)
    a = ap.parse_args()
    by_host: dict[str, list[dict]] = defaultdict(list)
    for line in sys.stdin:
        if line.strip():
            t = json.loads(line)
            by_host[t["host"]].append(t)
    robots = Policy(fetch=RobotsFetcher(user_agent=USER_AGENT, timeout=10.0, allow_private=False))
    reader = McpReader(timeout=a.timeout, allow_private=False, robots=robots)
    lock = threading.Lock()
    done = [0]
    total = sum(len(v) for v in by_host.values())

    def host_job(targets: list[dict]) -> None:
        for t in targets:  # one host's endpoints in sequence
            try:
                row = read(reader, t)
            except Exception as exc:  # noqa: BLE001 - a census row, never a crash
                row = {"id": t["id"], "host": t["host"], "endpoint": t["endpoint"], "status": "census-error",
                       "detail": f"{type(exc).__name__}: {exc}"[:200]}
            with lock:
                sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                done[0] += 1
                if done[0] % 500 == 0:
                    print(f"{time.strftime('%H:%M:%S', time.gmtime())}Z {done[0]}/{total}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(host_job, by_host.values()))
    print(f"done {done[0]}/{total}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
