"""rugsnare drift-watch pins vs HISTOR snapshot 2026-10-26, same npm package at the same version.

rugsnare keeps description, inputSchema and annotations per tool; those three are compared as JSON
values (no hashing, so neither side's canonicalization is involved).
"""

import collections
import glob
import gzip
import json
import pathlib

here = pathlib.Path(__file__).parent
H = here / "histor-2026-10-26"

pins = {}
for path in sorted(glob.glob(str(here / "rugsnare" / "pins" / "*.json"))):
    doc = json.load(open(path))
    for server in doc.get("servers", {}).values():
        ident = server.get("identity") or {}
        pkg, ver = ident.get("package"), ident.get("resolvedVersion")
        if not pkg or not ver:
            scanned = server.get("scannedAgainst") or ""
            if "@" in scanned[1:]:
                pkg, ver = scanned.rsplit("@", 1)
        if pkg and ver and server.get("tools"):
            pins[(pkg, ver)] = (pathlib.Path(path).name, server["tools"])
print("rugsnare pins with package@version and tools:", len(pins))

ids = {}
by_version = {}
with gzip.open(H / "servers.jsonl.gz", "rt") as fh:
    for line in fh:
        s = json.loads(line)
        if s["endpoint"].startswith("npm:"):
            ids[s["id"]] = s["endpoint"][4:]
            if s.get("packageVersion") and s.get("toolSetDigest"):
                by_version[(s["endpoint"][4:], s["packageVersion"])] = (
                    s["toolSetDigest"],
                    s.get("toolExtrasDigest"),
                )
with gzip.open(H / "package_observations.jsonl.gz", "rt") as fh:
    for line in fh:
        o = json.loads(line)
        pkg = ids.get(o["serverId"])
        if pkg and o.get("packageVersion") and o.get("toolSetDigest"):
            by_version.setdefault((pkg, o["packageVersion"]), (o["toolSetDigest"], o.get("toolExtrasDigest")))

pkgs_in_histor = {p for p in ids.values()}
pinned_pkgs = {p for p, _ in pins}
print("rugsnare packages:", len(pinned_pkgs), "of them listed in HISTOR:", len(pinned_pkgs & pkgs_in_histor))
common = sorted(k for k in pins if k in by_version)
print("same package@version in both:", len(common), common)

need = {by_version[k][0] for k in common}
need_x = {by_version[k][1] for k in common if by_version[k][1]}
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

totals = collections.Counter()
for key in common:
    fname, theirs = pins[key]
    ts, xd = by_version[key]
    ours = {t["name"]: t for t in toolsets[ts]}
    ex = extras.get(xd, {}).get("tools", {}) if xd else None
    names_same = set(ours) == set(theirs)
    diffs = collections.Counter()
    for name in set(ours) & set(theirs):
        a, b = ours[name], theirs[name]
        if a.get("description", "") != (b.get("description") or ""):
            diffs["description"] += 1
        if a.get("inputSchema", {}) != (b.get("inputSchema") or {}):
            diffs["inputSchema"] += 1
        if ex is not None and (ex.get(name, {}).get("annotations") or {}) != (b.get("annotations") or {}):
            diffs["annotations"] += 1
    n = len(set(ours) & set(theirs))
    totals["tools compared"] += n
    totals["tools with identical description+inputSchema"] += n - sum(
        1
        for name in set(ours) & set(theirs)
        if ours[name].get("description", "") != (theirs[name].get("description") or "")
        or ours[name].get("inputSchema", {}) != (theirs[name].get("inputSchema") or {})
    )
    print(
        f"{key[0]}@{key[1]} ({fname}): ours {len(ours)} theirs {len(theirs)} names same {names_same} diffs {dict(diffs)}"
        + ("" if ex is not None else " (annotations not compared)")
    )
print(dict(totals))
