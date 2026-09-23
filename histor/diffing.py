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

MAX_NEW_ADDRESSES = 50
MAX_SCAN_CHARS = 200_000

# Where a tool set can point outside itself: a URL's host, an e-mail address, a bare domain, an
# IPv4 address. Written without words on purpose, so it reads the same in every language. Every
# boundary is ASCII-only (never \b or \w), so an address written flush against CJK text is found,
# and every pattern can start only at the beginning of a run, so matching stays linear.
_URL = re.compile(
    r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,30}://(?:[A-Za-z0-9._~%!$&'()*+,;=:-]{0,256}@)?"
    r"(\[[0-9A-Fa-f:.]{2,64}\]|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?)"
)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@((?:[A-Za-z0-9-]{1,63}\.){1,10}[A-Za-z]{2,24})(?![A-Za-z0-9-])")
_DOMAIN = re.compile(
    r"(?<![A-Za-z0-9@/.:_%+\\-])((?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.){1,10}([A-Za-z]{2,24}))(?![A-Za-z0-9_-])"
)
_IPV4 = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?!\.?\d)")
# A bare "name.ext" is far more often a file than a host. Applied only when a single label precedes
# the extension ("result.json"); "upload.example.pl" is a host even though "pl" is also a file type.
_FILE_EXTENSIONS = frozenset(
    "json jsonl txt csv tsv md rst pdf png jpg jpeg gif svg webp ico yaml yml xml html htm log py js mjs cjs ts "
    "tsx jsx sh bash zsh ps1 bat exe dll so zip tar gz tgz bz2 xz 7z rar docx doc xlsx xls pptx ppt mp3 mp4 wav "
    "avi mov mkv env toml ini cfg conf lock sql db sqlite parquet ipynb rb go rs java jar kt swift c h cpp hpp cs "
    "php pl css scss less vue svelte wasm bin dat bak tmp pem crt key pub".split()
)


def _addresses_in(text: str) -> set[str]:
    text = text[:MAX_SCAN_CHARS]
    found: set[str] = set()
    for m in _URL.finditer(text):
        host = m.group(1).strip(".-").lower()
        if host:
            found.add(host)
    # Hosts inside URLs are counted once, and URL userinfo is never read as an e-mail address.
    rest = _URL.sub(" ", text)
    for m in _EMAIL.finditer(rest):
        found.add(m.group(0).lower())
    rest = _EMAIL.sub(" ", rest)
    for m in _DOMAIN.finditer(rest):
        name, ext = m.group(1).lower(), m.group(2).lower()
        if ext in _FILE_EXTENSIONS and name.count(".") == 1:
            continue
        found.add(name)
    for m in _IPV4.finditer(rest):
        found.add(m.group(1))
    return found


def _strings(value: Any) -> list[str]:
    """Every string key and value in a schema, each on its own (never the serialised JSON, whose
    escapes would glue a letter onto the address that follows)."""
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.append(str(k))
            out.extend(_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_strings(v))
    elif isinstance(value, str):
        out.append(value)
    return out


def _tool_texts(tools: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for t in tools:
        out.append(str(t.get("name", "")))
        out.append(str(t.get("description", "")))
        for member in ("inputSchema", "outputSchema"):
            if member in t:
                out.extend(_strings(t[member]))
    return out


def _all_addresses(tools: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for text in _tool_texts(tools):
        found |= _addresses_in(text)
    return found


def new_addresses(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> tuple[list[str], int]:
    """Hosts, e-mail addresses and IPs the new tool set mentions and the old one did not, and how
    many there are in all (the list is capped at MAX_NEW_ADDRESSES; the count is not).

    Language-independent: an address is spelled the same in every language, so a changed
    description that starts pointing somewhere new shows up here whatever words surround it.
    Compared by host, so a new path on a host the tools already named is not reported.
    """
    fresh = sorted(_all_addresses(new) - _all_addresses(old))
    return fresh[:MAX_NEW_ADDRESSES], len(fresh)


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
    summary["newAddresses"], summary["newAddressesTotal"] = new_addresses(old, new)
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
