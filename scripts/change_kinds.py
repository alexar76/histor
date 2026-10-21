#!/usr/bin/env python3
"""What kind of change each recorded change is, over a HISTOR snapshot (histor/dataset.py).

    python3 scripts/change_kinds.py DIR            # DIR holds servers.jsonl.gz and changes.jsonl.gz
    python3 scripts/change_kinds.py DIR --json     # the same numbers as JSON

Standard library only. Every change goes into exactly one kind, checked in this order:

    names     only tool names were readable (the list could not be fully digested)
    addrm     a tool was added or removed
    text      a tool description, or a parameter description in the input schema, changed
              (beyond digits and whitespace)
    shape     input types, enums, required fields, defaults or limits changed, no description text
    noise     only digits or whitespace in a description, or only $schema / title / examples
    output    only the output schema changed

Kinds are by field, not by meaning: a typo fix in a description counts as "text". The second
table is per remote server listed and observed from the first day of the snapshot's changes.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

DIGITS = re.compile(r"^[\s\d.,:\-/]*$")
COSMETIC = {"$schema", "title", "examples", "$comment", "$id"}
ORDER = ("text", "addrm", "noise", "names", "output", "shape")
LABEL = {
    "text": "a tool or parameter description changed",
    "addrm": "tools added or removed",
    "noise": "only digits/whitespace in a description, or $schema/title/examples",
    "names": "only tool names readable",
    "output": "only the output schema",
    "shape": "only input types, enums, required fields, defaults or limits",
}


def rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


def value(v):
    """A diff value is JSON text, sometimes cut short; a cut one is read as the raw text."""
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return v


def leaf(path: str) -> str:
    keys = re.findall(r'"([^"]+)"', path)
    return keys[-1] if keys else path


def kind(summary: dict) -> str:
    if summary.get("detail") != "full":
        return "names"
    if summary.get("added") or summary.get("removed"):
        return "addrm"
    seen: set[str] = set()
    for tool in summary.get("modified") or []:
        for field, diff in (tool.get("fields") or {}).items():
            if field == "description":
                edits = [op[1] for op in diff if op[0] in "+-"]
                seen.add("noise" if all(DIGITS.match(e) for e in edits) else "text")
            elif field == "inputSchema":
                for entry in diff:
                    key = leaf(entry.get("path", ""))
                    if key == "description":
                        plain = [value(v) for v in (entry.get("old"), entry.get("new")) if v is not None]
                        noisy = bool(plain) and all(isinstance(v, str) and DIGITS.match(v) for v in plain)
                        seen.add("noise" if noisy else "text")
                    elif key in COSMETIC:
                        seen.add("noise")
                    else:
                        seen.add("shape")
            elif field == "outputSchema":
                seen.add("output")
            else:
                seen.add("shape")
    for k in ("text", "shape"):
        if k in seen:
            return k
    if seen and seen <= {"noise"}:
        return "noise"
    return "output" if "output" in seen else "noise"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    d = Path(argv[0])
    servers = {r["id"]: r for r in rows(d / "servers.jsonl.gz")}
    changes = list(rows(d / "changes.jsonl.gz"))
    total = Counter()
    per: dict[str, Counter] = defaultdict(Counter)
    for c in changes:
        k = kind(json.loads(c["summaryJson"]))
        total[k] += 1
        per[c["serverId"]][k] += 1
    first_day = min(c["observedAt"] for c in changes)[:10] + "T23:59:59Z"
    remote = [r for r in servers.values() if r["transport"] != "stdio" and not r["delisted"]
              and r["toolSetDigest"] and r["firstPinned"] and r["firstPinned"] <= first_day]
    packages = [r for r in servers.values() if r["transport"] == "stdio" and not r["delisted"] and r["toolSetDigest"]]

    def changed(group):
        return [r for r in group if any(n for k, n in per[r["id"]].items() if k != "names")]

    out = {
        "changes": len(changes),
        "kinds": {k: total[k] for k in ORDER},
        "remote": {"observed": len(remote), "changed": len(changed(remote)),
                   "changedText": sum(1 for r in changed(remote) if per[r["id"]]["text"]),
                   "addedOrRemoved": sum(1 for r in changed(remote) if per[r["id"]]["addrm"])},
        "packages": {"observed": len(packages), "changed": len([r for r in packages if per[r["id"]]]),
                     "changedText": sum(1 for r in packages if per[r["id"]]["text"]),
                     "addedOrRemoved": sum(1 for r in packages if per[r["id"]]["addrm"])},
    }
    if "--json" in argv:
        print(json.dumps(out, indent=2))
        return 0
    print(f"{out['changes']:,} changes")
    for k in ORDER:
        n = total[k]
        print(f"  {n:7,}  {100 * n / len(changes):5.1f}%  {LABEL[k]}")
    for name in ("remote", "packages"):
        g = out[name]
        print(f"{name}: {g['changed']:,} of {g['observed']:,} changed; {g['changedText']:,} changed description text; "
              f"{g['addedOrRemoved']:,} added or removed tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
