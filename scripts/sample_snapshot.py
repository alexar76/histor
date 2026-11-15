#!/usr/bin/env python3
"""A reproducible sample of one HISTOR dataset snapshot, for people who asked for "a sample dataset".

It takes the servers whose tool lists changed at least once in the snapshot, orders them by
sha256("<seed>:<serverId>") and keeps the first N. For those servers it copies, byte for byte, their
rows of servers.jsonl.gz, every row of changes.jsonl.gz, and every tool list in toolsets.jsonl.gz that
one of those rows names (current list, and both sides of every change).

Every input file is checked against the SHA-256 in the snapshot's signed manifest.json before a row is
used (toolsets.jsonl.gz, ~1.6 GB, is hashed while it streams and never written to disk). Because rows
are copied unchanged, anyone can check a sample line by finding the same line in the published file.

Stdlib only. Usage:
  python3 sample_snapshot.py --snapshot 2026-11-09 --n 200 --seed histor-sample --out ./sample
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.request
from collections import Counter

BASE = "https://histor.modelmarket.dev/datasets/{snapshot}/"
UA = {"User-Agent": "histor-sample/1 (+https://histor.modelmarket.dev/datasets/)"}
DIGEST_RE = re.compile(rb'"digest"\s*:\s*"([^"]+)"')


class HashingReader:
    """File-like wrapper that hashes every byte read through it."""

    def __init__(self, raw):
        self.raw, self.sha, self.bytes = raw, hashlib.sha256(), 0

    def read(self, n=-1):
        b = self.raw.read(n)
        self.sha.update(b)
        self.bytes += len(b)
        return b


def open_checked(url: str, expect: dict):
    """(gzip line iterator, reader) for a snapshot file; call verify(reader, expect) after reading it all."""
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120)
    reader = HashingReader(raw)
    return gzip.GzipFile(fileobj=reader), reader


def drain_and_verify(name: str, reader: HashingReader, expect: dict) -> None:
    while reader.read(1 << 20):
        pass
    got = reader.sha.hexdigest()
    if got != expect["sha256"] or reader.bytes != expect["bytes"]:
        sys.exit(f"{name}: sha256/bytes {got}/{reader.bytes} differ from the signed manifest "
                 f"{expect['sha256']}/{expect['bytes']}")
    print(f"{name}: sha256 matches the signed manifest ({reader.bytes:,} bytes)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", default="histor-sample")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    base = BASE.format(snapshot=a.snapshot)
    os.makedirs(a.out, exist_ok=True)

    manifest_bytes = urllib.request.urlopen(urllib.request.Request(base + "manifest.json", headers=UA),
                                            timeout=60).read()
    manifest = json.loads(manifest_bytes)
    files = manifest["files"]
    open(os.path.join(a.out, "manifest.json"), "wb").write(manifest_bytes)

    # 1. changes: rows per server
    gz, rd = open_checked(base + "changes.jsonl.gz", files["changes.jsonl.gz"])
    change_lines: dict[str, list[bytes]] = {}
    for line in gz:
        row = json.loads(line)
        change_lines.setdefault(row["serverId"], []).append(line)
    drain_and_verify("changes.jsonl.gz", rd, files["changes.jsonl.gz"])

    # 2. servers: the population is every listed server with at least one change row
    gz, rd = open_checked(base + "servers.jsonl.gz", files["servers.jsonl.gz"])
    server_lines: dict[str, bytes] = {}
    for line in gz:
        row = json.loads(line)
        if row["id"] in change_lines:
            server_lines[row["id"]] = line
    drain_and_verify("servers.jsonl.gz", rd, files["servers.jsonl.gz"])

    population = sorted(server_lines)
    key = lambda sid: hashlib.sha256(f"{a.seed}:{sid}".encode()).hexdigest()
    chosen = sorted(population, key=key)[: a.n]

    want: set[str] = set()
    for sid in chosen:
        cur = json.loads(server_lines[sid]).get("toolSetDigest")
        if cur:
            want.add(cur)
        for line in change_lines[sid]:
            row = json.loads(line)
            for k in ("fromToolSetDigest", "toToolSetDigest"):
                if row.get(k):
                    want.add(row[k])

    # 3. toolsets: stream ~1.6 GB, keep the named lists
    gz, rd = open_checked(base + "toolsets.jsonl.gz", files["toolsets.jsonl.gz"])
    toolset_lines: dict[str, bytes] = {}
    for line in gz:
        m = DIGEST_RE.search(line[:400])
        d = m.group(1).decode() if m else json.loads(line)["digest"]
        if d in want:
            toolset_lines[d] = line
    drain_and_verify("toolsets.jsonl.gz", rd, files["toolsets.jsonl.gz"])

    def write(name: str, lines: list[bytes]) -> dict:
        path = os.path.join(a.out, name)
        with open(path, "wb") as f:
            for line in lines:
                f.write(line if line.endswith(b"\n") else line + b"\n")
        return {"rows": len(lines), "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest()}

    out_files = {
        "servers.jsonl": write("servers.jsonl", [server_lines[s] for s in chosen]),
        "changes.jsonl": write("changes.jsonl", [l for s in chosen for l in change_lines[s]]),
        "toolsets.jsonl": write("toolsets.jsonl", [toolset_lines[d] for d in sorted(toolset_lines)]),
    }
    kinds = Counter("package" if json.loads(server_lines[s])["endpoint"].startswith(("npm:", "pypi:"))
                    else "remote" for s in chosen)
    info = {
        "snapshot": a.snapshot,
        "source": base,
        "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "treeSize": manifest["treeHead"]["treeSize"],
        "selection": f"servers with >= 1 row in changes.jsonl.gz, ordered by sha256('{a.seed}:' + serverId), first {a.n}",
        "population": len(population),
        "chosen": len(chosen),
        "kinds": dict(kinds),
        "toolsetsNamed": len(want),
        "toolsetsFound": len(toolset_lines),
        "files": out_files,
    }
    json.dump(info, open(os.path.join(a.out, "SAMPLE.json"), "w"), indent=1)
    print(json.dumps(info, indent=1))


if __name__ == "__main__":
    main()
