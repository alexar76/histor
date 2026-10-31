"""Every differing tool on every non-identical server: all field paths that differ, with word-level hunks.

Reads out/heldfast-pairs.jsonl (full lists), fetches heldfast's stored definition of each differing
tool from the pinned feed commit, and writes out/fullfield.jsonl (one row per server).
"""

import concurrent.futures
import difflib
import gzip
import json
import os
import pathlib
import subprocess

from peer_digests import heldfast_body, heldfast_digest

here = pathlib.Path(__file__).parent
commit = (here / "heldfast" / "feed-commit.txt").read_text().strip()
cache = here / "heldfast" / "catalogues"
cache.mkdir(exist_ok=True)
H = here / "histor-2026-10-26"

rows = [
    r
    for r in map(json.loads, open(here / "out" / "heldfast-pairs.jsonl"))
    if r["extrasKnown"] and r["verdict"] != "identical"
]
state = {}
for name in os.listdir(here / "heldfast" / "feed" / "state"):
    d = json.loads((here / "heldfast" / "feed" / "state" / name).read_text())
    if d.get("tools"):
        state[d["url"] if d["package"].startswith("remote/") else "npm:" + d["package"]] = d

want = {r["endpoint"] for r in rows}
servers = {}
with gzip.open(H / "servers.jsonl.gz", "rt") as fh:
    for line in fh:
        s = json.loads(line)
        if s["endpoint"] in want and s.get("toolSetDigest"):
            servers[(s["name"], s["endpoint"])] = s
need = {s["toolSetDigest"] for s in servers.values()}
need_x = {s["toolExtrasDigest"] for s in servers.values() if s.get("toolExtrasDigest")}
toolsets, extras = {}, {}
with gzip.open(H / "toolsets.jsonl.gz", "rt") as fh:
    for line in fh:
        r = json.loads(line)
        if r["digest"] in need:
            toolsets[r["digest"]] = json.loads(r["toolsJson"])
with gzip.open(H / "tool_extras.jsonl.gz", "rt") as fh:
    for line in fh:
        r = json.loads(line)
        if r["digest"] in need_x:
            extras[r["digest"]] = json.loads(r["extrasJson"])


def definition(fp):
    local = cache / f"{fp}.json"
    if not local.exists():
        raw = subprocess.run(
            [
                "curl",
                "-s",
                "--fail",
                "--retry",
                "3",
                f"https://raw.githubusercontent.com/rufat325/heldfast-feed/{commit}/tools/{fp[:2]}/{fp}.json",
            ],
            capture_output=True,
        ).stdout
        if not raw:
            return None
        local.write_bytes(raw)
    return json.loads(local.read_text())


def hunks(a: str, b: str, limit=4):
    wa, wb = a.split(" "), b.split(" ")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append([" ".join(wa[i1:i2])[:160], " ".join(wb[j1:j2])[:160]])
        if len(out) >= limit:
            break
    return out


def diffs(a, b, path="$", out=None):
    out = [] if out is None else out
    if type(a) is not type(b):
        out.append(
            {
                "path": path,
                "heldfast": json.dumps(a, ensure_ascii=False)[:160],
                "histor": json.dumps(b, ensure_ascii=False)[:160],
            }
        )
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append({"path": f"{path}.{k}", "only": "histor"})
            elif k not in b:
                out.append({"path": f"{path}.{k}", "only": "heldfast"})
            else:
                diffs(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(
                {
                    "path": path,
                    "heldfast": json.dumps(a, ensure_ascii=False)[:160],
                    "histor": json.dumps(b, ensure_ascii=False)[:160],
                }
            )
        else:
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                diffs(x, y, f"{path}[{i}]", out)
    elif a != b:
        if isinstance(a, str):
            out.append({"path": path, "hunks": hunks(a, b)})
        else:
            out.append({"path": path, "heldfast": a, "histor": b})
    return out


jobs = []
for r in rows:
    hf = state[r["endpoint"]]
    for name in r["differing"]:
        jobs.append(hf["tools"][name])
with concurrent.futures.ThreadPoolExecutor(16) as pool:
    list(pool.map(definition, sorted(set(jobs))))

with open(here / "out" / "fullfield.jsonl", "w") as fh:
    for r in rows:
        s = servers[(r["server"], r["endpoint"])]
        hf = state[r["endpoint"]]
        ex = extras.get(s.get("toolExtrasDigest"), {}).get("tools", {}) if s.get("toolExtrasDigest") else {}
        ours = {}
        for t in toolsets[s["toolSetDigest"]]:
            full = dict(t)
            full.update(ex.get(t["name"], {}))
            ours[t["name"]] = full
        tools = []
        for name in r["differing"]:
            theirs = definition(hf["tools"][name])
            if theirs is None:
                tools.append({"tool": name, "missing": True})
                continue
            theirs.setdefault("name", name)
            tools.append(
                {
                    "tool": name,
                    "storedMatches": heldfast_digest(theirs) == hf["tools"][name],
                    "diffs": diffs(heldfast_body(theirs), heldfast_body(ours[name])),
                }
            )
        fh.write(
            json.dumps(
                {
                    **{
                        k: r[k]
                        for k in (
                            "server",
                            "endpoint",
                            "kind",
                            "historObserved",
                            "historVersion",
                            "heldfastRead",
                            "heldfastVersion",
                            "ourTools",
                            "theirTools",
                            "common",
                            "identical",
                            "onlyOurs",
                            "onlyTheirs",
                            "ourSeenByHeldfast",
                        )
                    },
                    "attempted": hf.get("attempted"),
                    "tools": tools,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
print("servers", len(rows), "differing tools", len(jobs))
