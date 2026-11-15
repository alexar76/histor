#!/usr/bin/env python3
"""Summary of an instructions_census.py run (JSON Lines in, plain text out). Stdlib only.

    python3 instructions_census_report.py out.jsonl
"""
from __future__ import annotations

import collections
import json
import re
import statistics
import sys

# Steering away from tools outside this server: the class measured in tool descriptions on 2026-10-21 (MCP #3430).
_OTHER = (r"(?:web ?search|search engines?|(?:the |your )?(?:built-?in|native|default) (?:fetch|browser|search|tools?)|"
          r"(?:any |all )?other (?:mcp )?(?:servers?|tools?|providers?|sources?)|"
          r"(?:your|the model'?s) (?:own |general |prior )?(?:knowledge|training(?: data)?|memory))")
ROUTING = re.compile(r"(?:instead of|rather than|prefer\w* (?:\w+ ){0,6}(?:over|to)|over|before (?:using|trying)|"
                     r"not (?:use|rely on)|never (?:use|rely on)|don'?t (?:use|rely on))\s+(?:\w+ ){0,3}" + _OTHER, re.I)
INJECTION = re.compile(r"(ignore (?:all |any )?(?:previous|prior|above)|system prompt|disregard)", re.I)


def pct(a: int, b: int) -> str:
    return f"{a:,}/{b:,} ({a / b:.1%})" if b else f"{a}/0"


def quant(xs: list[int]) -> str:
    if not xs:
        return "-"
    xs = sorted(xs)
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]
    return f"median {statistics.median(xs):,.0f}, p10 {q(.1):,}, p90 {q(.9):,}, max {xs[-1]:,}"


def bucket(n) -> str:
    if not isinstance(n, int):
        return "?"
    return "1-5" if n <= 5 else "6-15" if n <= 15 else "16-40" if n <= 40 else "41+"


def main() -> None:
    rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    hosts = {r["host"] for r in rows}
    print(f"targets read: {len(rows):,} on {len(hosts):,} hosts")
    print("status:", ", ".join(f"{k} {v:,}" for k, v in collections.Counter(r["status"] for r in rows).most_common(8)))
    ok = [r for r in rows if r["status"] == "ok"]
    first: dict[str, dict] = {}
    for r in ok:
        first.setdefault(r["host"], r)
    per_host = list(first.values())

    for label, sample in (("per entry", ok), ("per host (first ok entry)", per_host)):
        has = [r for r in sample if r.get("instructionsPresent")]
        text = [r for r in has if r.get("instructionsType") == "str"]
        nonempty = [r for r in text if (r.get("instructions") or "").strip()]
        lens = [r["instructionsLength"] for r in nonempty]
        print(f"\n== {label}: {len(sample):,} ok")
        print("  instructions member present:", pct(len(has), len(sample)),
              "| types:", dict(collections.Counter(r.get("instructionsType") for r in has)))
        print("  non-empty text:", pct(len(nonempty), len(sample)))
        print("  length (chars):", quant(lens))
        print("  longer than 100 chars (the registry description limit):", pct(sum(x > 100 for x in lens), len(sample)))
        print("  by era:", {e: pct(sum(1 for r in nonempty if r.get("era") == e), sum(1 for r in sample if r.get("era") == e))
                           for e in ("legacy", "modern")})
        by = collections.defaultdict(list)
        for r in sample:
            by[bucket(r.get("toolCount"))].append(r)
        for k in ("1-5", "6-15", "16-40", "41+"):
            s = by.get(k, [])
            ne = [r for r in s if r.get("instructionsType") == "str" and (r.get("instructions") or "").strip()]
            print(f"  tools {k:>5}: non-empty {pct(len(ne), len(s))}; length {quant([r['instructionsLength'] for r in ne])}")

    # repeated texts: templates and frameworks
    texts = [r for r in per_host if r.get("instructionsType") == "str" and (r.get("instructions") or "").strip()]
    common = collections.Counter(r["instructionsSha256"] for r in texts)
    print(f"\n== distinct non-empty texts among {len(texts):,} hosts: {len(common):,}")
    sample_of = {r["instructionsSha256"]: r for r in texts}
    for sha, n in common.most_common(10):
        if n < 3:
            break
        print(f"  {n:>4} hosts: {sample_of[sha]['instructions'][:140]!r}")
    same_as_registry = sum(1 for r in texts if (r.get("registryDescription") or "").strip() == r["instructions"].strip())
    print("  identical to the registry description:", pct(same_as_registry, len(texts)))

    for name, rx in (("routing phrase", ROUTING), ("injection-like phrase", INJECTION)):
        hit = [r for r in texts if rx.search(r["instructions"])]
        print(f"\n== hosts whose instructions match a {name} filter: {pct(len(hit), len(texts))} (filter only; read before citing)")
        for r in hit[:12]:
            m = rx.search(r["instructions"])
            a = max(0, m.start() - 70)
            print(f"  {r['host'][:40]:40} …{r['instructions'][a:m.end() + 70]!r}")


if __name__ == "__main__":
    main()
