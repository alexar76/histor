"""What a tool definition carries beyond the four members MTL/1 digests: ``annotations``, ``_meta``,
``title``, ``icons``, ``execution`` and whatever else a server puts on a tool.

The tool-set digest is normative (PROFILE.md section 5) and covers only ``name``, ``description``,
``inputSchema`` and ``outputSchema``; changing what it covers would move every digest at once and
break every label chain. The rest is still what the server showed the model: ``readOnlyHint`` decides
whether a client asks before a call, and prices, payment terms and vendor flags live in ``_meta`` or
in members of their own. So HISTOR keeps them beside the tool set, under a digest of their own, and
records when they change. Never in a label: these members are not part of the MTL/1 subject.

How the body is written before hashing is the log's **extras scheme**, kept in ``meta`` under
``extras_digest_scheme`` (``python -m histor.extras_rekey`` sets it and re-keys what is stored):

* ``legacy`` (the default, and the only form before 2026-11-12): SHA-256 over
  ``json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)``. It differs from RFC 8785
  in three narrow cases: ``1.0`` and other integral floats, integers beyond 2^53, and member names
  outside the Basic Multilingual Plane (sorted by code point, not UTF-16 code unit).
* ``rfc8785``: SHA-256 over the RFC 8785 text, every number read as an IEEE 754 double (MTL/1.1, the
  serialiser of the tool-set digest; fractions are fine). This is the extras digest of
  ``mcp-tool-set-digest/1`` section 8, in its bounded form (section 8.1).

Both hash the same body, so one body has one digest per scheme and a change of scheme is not a
change of extras (``Crawler._settle_extras``).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from awr import AwrError

from histor.subject import canonical_tool_bytes
from histor.untrusted import clean_text

LEGACY = "legacy"
RFC8785 = "rfc8785"
SCHEMES = (LEGACY, RFC8785)
META_KEY = "extras_digest_scheme"
_LARGEST_DOUBLE = 1.7976931348623157e308

DIGESTED = frozenset({"name", "description", "inputSchema", "outputSchema"})
MAX_TOOL_BYTES = 16_384      # one tool's extra members; a larger value is replaced by its size
MAX_SET_BYTES = 262_144      # every tool's together; past it the rest are listed as omitted
MAX_DIFF_TOOLS = 200
MAX_DEPTH = 12


def _clean(value: Any, depth: int = 0) -> Any:
    """JSON any database stores: strings cleaned, keys cleaned, nesting bounded."""
    if depth > MAX_DEPTH:
        return "…"
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean_text(str(k)): _clean(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, depth + 1) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > _LARGEST_DOUBLE:
        return None     # a number with no double, as 1e400 already is (it parses as infinity)
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def scheme_problem(scheme: Any) -> str:
    return "" if scheme in SCHEMES else f"extras scheme must be one of {', '.join(SCHEMES)}, not {scheme!r}"


def digest_of(body: Any, scheme: str = LEGACY) -> str:
    """The SRI digest of a stored extras body under *scheme* (see the module text)."""
    if scheme == RFC8785:
        data = canonical_tool_bytes(body)
    elif scheme == LEGACY:
        data = _dumps(body).encode("utf-8")
    else:
        raise ValueError(scheme_problem(scheme))
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def scheme_for_digest(digest: str, body: dict[str, Any], preferred: str) -> str | None:
    """Identify how a stored body hashes, even while rekeying updates its references in batches."""
    schemes = (RFC8785, LEGACY) if preferred == RFC8785 else SCHEMES
    for scheme in schemes:
        try:
            if digest_of(body, scheme) == digest:
                return scheme
        except (AwrError, OverflowError, ValueError):
            continue
    return None


def tool_extras(tools: Any, scheme: str = LEGACY) -> tuple[str | None, dict[str, Any] | None]:
    """``(digest, body)`` for the extra members of a tools/list result, or ``(None, None)`` when no
    tool has any. ``body`` is ``{"tools": {name: {member: value}}}`` with only the tools that carry
    something, plus ``"omitted"`` (names) and ``"oversized"`` ({name: bytes}) when caps applied.
    The caps measure the compact JSON HISTOR stores, whatever the scheme, so the body never depends
    on it: only the digest does."""
    if not isinstance(tools, list):
        return None, None
    per_tool: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            continue
        extra = {k: v for k, v in tool.items() if k not in DIGESTED}
        if extra:
            per_tool[clean_text(tool["name"])] = _clean(extra)
    if not per_tool:
        return None, None
    body: dict[str, Any] = {"tools": {}}
    oversized: dict[str, int] = {}
    omitted: list[str] = []
    used = 0
    for name in sorted(per_tool):
        size = len(_dumps(per_tool[name]).encode("utf-8"))
        if size > MAX_TOOL_BYTES:
            oversized[name] = size
            continue
        if used + size > MAX_SET_BYTES:
            omitted.append(name)
            continue
        body["tools"][name] = per_tool[name]
        used += size
    if oversized:
        body["oversized"] = oversized
    if omitted:
        body["omitted"] = omitted
    return digest_of(body, scheme), body


def _annotation_flips(old: Any, new: Any) -> dict[str, list[Any]]:
    """``{hint: [before, after]}`` for every annotation key whose value moved (absent is None)."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    out: dict[str, list[Any]] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            out[key] = [old.get(key), new.get(key)]
    return out


def extras_diff(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """What moved between two extras bodies, tool by tool.

    ``annotations`` changes are spelled out value by value (they are small and they decide what a
    client asks the user); every other member is named when it appeared, disappeared or changed.
    ``escalations`` lists the tools that stopped saying they are read-only or started saying they
    are destructive: the flips a client that auto-approves on hints should hear about first.
    """
    old_tools = (old or {}).get("tools") or {}
    new_tools = (new or {}).get("tools") or {}
    tools: dict[str, Any] = {}
    escalations: list[str] = []
    for name in sorted(set(old_tools) | set(new_tools)):
        before, after = old_tools.get(name) or {}, new_tools.get(name) or {}
        if before == after:
            continue
        entry: dict[str, Any] = {}
        flips = _annotation_flips(before.get("annotations"), after.get("annotations"))
        if flips:
            entry["annotations"] = flips
            ro, de = flips.get("readOnlyHint"), flips.get("destructiveHint")
            if (ro and ro[0] is True and ro[1] is not True) or (de and de[1] is True and de[0] is not True):
                escalations.append(name)
        members = sorted((set(before) | set(after)) - {"annotations"})
        moved = {m: ("added" if m not in before else "removed" if m not in after else "changed")
                 for m in members if before.get(m) != after.get(m)}
        if moved:
            entry["members"] = moved
        if entry:
            tools[name] = entry
    names = sorted(tools)
    summary: dict[str, Any] = {"tools": {n: tools[n] for n in names[:MAX_DIFF_TOOLS]}, "changedTools": len(names)}
    if len(names) > MAX_DIFF_TOOLS:
        summary["truncated"] = True
    if escalations:
        summary["escalations"] = escalations
    return summary
