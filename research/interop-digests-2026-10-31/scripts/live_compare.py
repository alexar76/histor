"""For each server classed 'edited between the two reads', read it live now (all pages, 2025-06-18 with
the version header) and say whose 26 Oct copy today's definitions match, by heldfast fingerprint."""

import gzip
import json
import os
import pathlib

from mcp_probe import list_tools
from peer_digests import heldfast_digest

here = pathlib.Path(__file__).parent


def jsonl(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            yield json.loads(line)


state = {}
for name in os.listdir(here / "heldfast/feed/state"):
    d = json.load(open(here / "heldfast/feed/state" / name))
    if d.get("tools") and d.get("url"):
        state[d["url"]] = d["tools"]
targets = [r for r in jsonl(here / "out" / "reasons.jsonl") if "edited" in r["reason"]]
want = {(t["server"], t["endpoint"]) for t in targets}
servers = {(s["name"], s["endpoint"]): s for s in jsonl(here / "histor-2026-10-26/servers.jsonl.gz")
           if (s["name"], s["endpoint"]) in want}
need = {s["toolSetDigest"] for s in servers.values()}
need_x = {s["toolExtrasDigest"] for s in servers.values() if s.get("toolExtrasDigest")}
toolsets = {r["digest"]: json.loads(r["toolsJson"]) for r in jsonl(here / "histor-2026-10-26/toolsets.jsonl.gz")
            if r["digest"] in need}
extras = {r["digest"]: json.loads(r["extrasJson"]) for r in jsonl(here / "histor-2026-10-26/tool_extras.jsonl.gz")
          if r["digest"] in need_x}


def share(copy, live):
    common = set(copy) & set(live)
    return f"{sum(copy[n] == live[n] for n in common)}/{len(set(copy) | set(live))}"


out = []
for key, s in servers.items():
    ex = extras.get(s.get("toolExtrasDigest"), {}).get("tools", {})
    ours = {t["name"]: heldfast_digest({**t, **ex.get(t["name"], {})}) for t in toolsets[s["toolSetDigest"]]}
    theirs = state[key[1]]
    try:
        live = {t["name"]: heldfast_digest(t) for t in list_tools(key[1])[2]}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        out.append({"endpoint": key[1], "live": f"error {exc}"[:80]})
        continue
    verdict = "live = heldfast" if live == theirs else "live = HISTOR" if live == ours else "neither (edited again)"
    out.append({"endpoint": key[1], "verdict": verdict,
                "liveMatchesHeldfast": share(theirs, live), "liveMatchesHistor": share(ours, live)})
    print(out[-1])
json.dump(out, open(here / "out" / "edited-live.json", "w"), indent=1)
