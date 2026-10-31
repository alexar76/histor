"""HISTOR snapshot 2026-10-26 vs heldfast feed checkpoint 2026-10-26, server by server, tool by tool.

For every server both records read with tools, recompute heldfast's fingerprint from the definition
HISTOR stored (tool set + extras) and compare it with the fingerprint heldfast recorded.
"""

import collections
import gzip
import json
import os
import pathlib

from peer_digests import heldfast_digest

here = pathlib.Path(__file__).parent
H = here / "histor-2026-10-26"
F = here / "heldfast"
EXTRAS_SINCE = "2026-10-22"

# heldfast: current fingerprints per server
hf_remote: dict[str, list[dict]] = collections.defaultdict(list)
hf_npm: dict[str, dict] = {}
for name in os.listdir(F / "feed" / "state"):
    d = json.loads((F / "feed" / "state" / name).read_text())
    if not d.get("tools"):
        continue
    if d["package"].startswith("remote/"):
        hf_remote[d["url"]].append(d)
    else:
        hf_npm[d["package"]] = d
lookup = json.load(gzip.open(F / "lookup-all.json.gz"))["tools"]

# HISTOR: servers with a current tool set
pairs = []
with gzip.open(H / "servers.jsonl.gz", "rt") as fh:
    for line in fh:
        s = json.loads(line)
        if not s.get("toolSetDigest"):
            continue
        ep = s["endpoint"] or ""
        if ep.startswith("npm:"):
            hf = hf_npm.get(ep[4:])
            if hf:
                pairs.append((s, hf, "npm"))
        elif ep.startswith("http"):
            cands = hf_remote.get(ep) or hf_remote.get(ep.rstrip("/")) or hf_remote.get(ep + "/")
            if cands:
                # one URL listed under several registry names: they read the same server
                pairs.append((s, cands[0], "remote"))

need_sets = {s["toolSetDigest"] for s, _, _ in pairs}
need_extras = {s["toolExtrasDigest"] for s, _, _ in pairs if s.get("toolExtrasDigest")}
toolsets, extras = {}, {}
with gzip.open(H / "toolsets.jsonl.gz", "rt") as fh:
    for line in fh:
        r = json.loads(line)
        if r["digest"] in need_sets:
            toolsets[r["digest"]] = json.loads(r["toolsJson"])
with gzip.open(H / "tool_extras.jsonl.gz", "rt") as fh:
    for line in fh:
        r = json.loads(line)
        if r["digest"] in need_extras:
            extras[r["digest"]] = json.loads(r["extrasJson"])

stats = collections.Counter()
tool_stats = collections.Counter()
rows = []
for s, hf, kind in pairs:
    tools = toolsets.get(s["toolSetDigest"])
    if tools is None:
        stats["toolset missing from snapshot"] += 1
        continue
    ex_known = bool(s.get("toolExtrasDigest")) or (s.get("lastObserved") or "") >= EXTRAS_SINCE
    ex = extras.get(s.get("toolExtrasDigest"), {}) if s.get("toolExtrasDigest") else {}
    partial = bool(ex.get("omitted") or ex.get("oversized"))
    ours = {}
    legacy = {}
    for t in tools:
        full = dict(t)
        full.update(ex.get("tools", {}).get(t["name"], {}))
        ours[t["name"]] = heldfast_digest(full)
        # what a server that drops 2025-06-18 members serves a 2024-11-05 client
        legacy[t["name"]] = heldfast_digest(
            {k: v for k, v in full.items() if k not in ("outputSchema", "title")}
        )
    theirs = hf["tools"]
    same_names = set(ours) == set(theirs)
    common = set(ours) & set(theirs)
    matched = sum(ours[n] == theirs[n] for n in common)
    legacy_matched = sum(ours[n] != theirs[n] and legacy[n] == theirs[n] for n in common)
    seen_anytime = sum(ours[n] in lookup for n in ours)
    group = (kind, "extras known" if ex_known and not partial else "extras unknown")
    if same_names and matched == len(common):
        verdict = "identical"
    elif same_names:
        verdict = "same names, some definitions differ"
    else:
        verdict = "different tool names"
    stats[(group, verdict)] += 1
    if ex_known and not partial:
        tool_stats[(kind, "tools compared")] += len(common)
        tool_stats[(kind, "tools identical")] += matched
        tool_stats[(kind, "tools identical once outputSchema+title dropped")] += legacy_matched
        tool_stats[(kind, "our definition seen by heldfast at some time")] += seen_anytime
        tool_stats[(kind, "our tools")] += len(ours)
    rows.append(
        {
            "server": s["name"],
            "endpoint": s["endpoint"],
            "kind": kind,
            "extrasKnown": ex_known and not partial,
            "historObserved": s.get("lastObserved"),
            "historVersion": s.get("packageVersion"),
            "heldfastRead": hf.get("checked_at") or hf.get("published"),
            "heldfastVersion": hf.get("version"),
            "verdict": verdict,
            "ourTools": len(ours),
            "theirTools": len(theirs),
            "common": len(common),
            "identical": matched,
            "legacyOnly": legacy_matched,
            "ourSeenByHeldfast": seen_anytime,
            "onlyOurs": sorted(set(ours) - set(theirs)),
            "onlyTheirs": sorted(set(theirs) - set(ours)),
            "differing": sorted(n for n in common if ours[n] != theirs[n]),
        }
    )

print(
    "pairs joined:",
    len(pairs),
    "(npm",
    sum(k == "npm" for *_, k in pairs),
    "remote",
    sum(k == "remote" for *_, k in pairs),
    ")",
)
for k, v in sorted(stats.items(), key=str):
    print(v, k)
for k, v in sorted(tool_stats.items()):
    print(v, k)
(here / "out").mkdir(exist_ok=True)
with open(here / "out" / "heldfast-pairs.jsonl", "w") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
