"""Tool fingerprints as a client library computes them, from what HISTOR recorded.

A client that pins tool definitions needs a baseline to compare with. The Vercel AI SDK computes one
digest per tool (``fingerprintTools`` in the ``ai`` package) and leaves the baseline to the
application; this module computes the same digests from HISTOR's own record of a server, so the
record can serve as a baseline taken by an outside observer:

    detectToolDrift(await fingerprintTools(await client.tools()), baseline.tools)

Profile ``ai-sdk``. What the library hashes, read from the source of ``ai`` 7.0.177 and
``@ai-sdk/mcp`` 2.0.101 and reproduced on fingerprints the library itself produced
(tests/vectors/ai-sdk-fingerprint): per tool, SHA-256 (base64url, no padding) over the JSON text,
keys sorted by UTF-16 code units, of

    {"description": {"type": "string", "value": <description>}   or {"type": "none"} without one,
     "inputSchema": {...inputSchema, "properties": <its properties, or {}>, "additionalProperties": false},
     "title": <title, else annotations.title, else the bare word undefined>}

Numbers are written as JavaScript writes them after ``JSON.parse`` (an integer beyond 2^53 is the
nearest double). ``annotations`` other than ``title``, ``outputSchema``, ``_meta`` and every other
member are not part of the digest: a change there does not move it.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from histor.subject import MAX_SAFE_INTEGER, es_number, utf16_sort_key

PROFILE = "ai-sdk"
CHECKED_AGAINST = {"ai": "7.0.177", "@ai-sdk/mcp": "2.0.101"}
_UNDEFINED = object()
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(text: str) -> str:
    """``JSON.stringify`` of a string: the short escapes, \\u00xx for other controls, \\udxxx for a lone surrogate."""
    out = ['"']
    for ch in text:
        cp = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif cp < 0x20 or 0xD800 <= cp <= 0xDFFF:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _text(value: Any) -> str:
    if value is _UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value) if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else es_number(float(value))
    if isinstance(value, float):
        return es_number(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_text(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(f"{_string(key)}:{_text(value[key])}" for key in sorted(value, key=utf16_sort_key)) + "}"
    raise TypeError(f"{type(value).__name__} is not a JSON value")


def ai_sdk_fingerprint(tool: dict[str, Any]) -> str:
    """The AI SDK's fingerprint of one tool definition as a server sends it."""
    description = tool.get("description")
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = schema.get("properties")
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    title = tool.get("title")
    if title is None:
        title = annotations.get("title")
    hashed = {
        "description": {"type": "string", "value": description} if isinstance(description, str) else {"type": "none"},
        "inputSchema": {**schema, "properties": {} if properties is None else properties, "additionalProperties": False},
        "title": _UNDEFINED if title is None else title,
    }
    digest = hashlib.sha256(_text(hashed).encode("utf-8", "surrogatepass")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def ai_sdk_fingerprints(tools: list[dict[str, Any]]) -> dict[str, str]:
    """name → fingerprint, as ``fingerprintTools`` returns it for the tool set an MCP client builds
    (a later tool with the same name replaces an earlier one, as it does in the client's object)."""
    return {tool["name"]: ai_sdk_fingerprint(tool) for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)}


def from_record(entries: list[dict[str, Any]], extras: dict[str, Any] | None) -> dict[str, Any]:
    """The profile's answer from HISTOR's record of one tool set: *entries* are the recorded tools
    (name, description, inputSchema; histor.subject), *extras* the recorded other members
    (histor.extras: ``{"tools": {name: {...}}}``, or None when they were not recorded).

    HISTOR keeps a description that was not sent as ``""``, and the library hashes the two
    differently; for a tool recorded with an empty description the second possibility is given in
    ``ifDescriptionAbsent``. A tool whose other members HISTOR did not keep whole (too large) cannot
    be given a title, so it is listed in ``notComputed`` instead of being guessed."""
    other = (extras or {}).get("tools") if isinstance((extras or {}).get("tools"), dict) else {}
    dropped = set((extras or {}).get("omitted") or []) | set((extras or {}).get("oversized") or [])
    tools: dict[str, str] = {}
    absent: dict[str, str] = {}
    not_computed: list[str] = []
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        members = other.get(name) if isinstance(other.get(name), dict) else {}
        if extras is None or name in dropped or any(not isinstance(members.get(k), (str, dict, type(None))) for k in ("title", "annotations")):
            not_computed.append(name)
            continue
        tool = {"name": name, "description": entry.get("description"), "inputSchema": entry.get("inputSchema"),
                "title": members.get("title"), "annotations": members.get("annotations")}
        try:
            tools[name] = ai_sdk_fingerprint(tool)
            if entry.get("description") == "":
                absent[name] = ai_sdk_fingerprint({**tool, "description": None})
        except (TypeError, ValueError):
            not_computed.append(name)
    return {"tools": tools, **({"ifDescriptionAbsent": absent} if absent else {}),
            **({"notComputed": sorted(not_computed)} if not_computed else {})}
