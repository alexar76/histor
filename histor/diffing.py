"""What changed between two tool sets — the part of a continuity ``fail`` a human can read.

The label says only that two digests differ. This says where: tools added and removed, and for
each surviving tool which of the digested members moved — the description as a word diff, the
schemas as a list of JSON paths. It is derived from two content-addressed tool sets anyone can
fetch, so it is a convenience, not a claim: re-run it and you get the same answer.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

MAX_OPS_TEXT = 20000
_TOKEN = re.compile(r"\s+|[^\s]+")


def word_diff(old: str, new: str) -> list[list[str]]:
    """``[[op, text], …]`` with op in ``=``, ``-``, ``+``."""
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    ops: list[list[str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append(["=", "".join(a[i1:i2])])
            continue
        if i2 > i1:
            ops.append(["-", "".join(a[i1:i2])])
        if j2 > j1:
            ops.append(["+", "".join(b[j1:j2])])
    return ops


def _flatten(value: Any, path: str = "$") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(value, dict):
        if not value:
            out[path] = "{}"
        for key in sorted(value):
            out.update(_flatten(value[key], f"{path}.{json.dumps(key, ensure_ascii=False)}"))
    elif isinstance(value, list):
        if not value:
            out[path] = "[]"
        for i, item in enumerate(value):
            out.update(_flatten(item, f"{path}[{i}]"))
    else:
        out[path] = json.dumps(value, ensure_ascii=False)
    return out


def schema_diff(old: Any, new: Any, limit: int = 200) -> list[dict[str, str]]:
    a, b = _flatten(old), _flatten(new)
    out: list[dict[str, str]] = []
    for path in sorted(set(a) | set(b)):
        if a.get(path) == b.get(path):
            continue
        entry = {"path": path}
        if path in a:
            entry["old"] = a[path][:500]
        if path in b:
            entry["new"] = b[path][:500]
        out.append(entry)
        if len(out) >= limit:
            out.append({"path": "…", "note": f"truncated at {limit} paths"})
            break
    return out


def tool_set_diff(old: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None,
                  old_names: list[str], new_names: list[str]) -> dict[str, Any]:
    """Summary of a change. Works on names alone when either side has no digestible tool set."""
    old_set, new_set = set(old_names), set(new_names)
    summary: dict[str, Any] = {
        "added": sorted(new_set - old_set),
        "removed": sorted(old_set - new_set),
        "modified": [],
        "detail": "full" if old is not None and new is not None else "names-only",
    }
    if old is None or new is None:
        return summary
    by_old = {t["name"]: t for t in old}
    by_new = {t["name"]: t for t in new}
    for name in sorted(old_set & new_set):
        a, b = by_old[name], by_new[name]
        fields: dict[str, Any] = {}
        if a.get("description", "") != b.get("description", ""):
            da, db = a.get("description", ""), b.get("description", "")
            if len(da) + len(db) <= MAX_OPS_TEXT:
                fields["description"] = word_diff(da, db)
            else:
                fields["description"] = [["-", da[:2000] + "…"], ["+", db[:2000] + "…"]]
        for member in ("inputSchema", "outputSchema"):
            if a.get(member) != b.get(member):
                fields[member] = schema_diff(a.get(member, None), b.get(member, None))
        if fields:
            summary["modified"].append({"tool": name, "fields": fields})
    return summary
