"""Two peer tool-definition digests, written from their published rules (not their code).

heldfast:  docs/LOCK.md "Tool digest" (rufat325/heldfast).
AgentAvow: tool-manifest-digest-vectors-v1 README, "derivation" (AgentAvow/AgentAvow).
Both are SHA-256 over RFC 8785; rfc8785 0.1.4 does the canonical form.
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785

MAX_SAFE = 2**53 - 1


def _doubles(value: Any) -> Any:
    # heldfast LOCK.md: an integer beyond 2^53 is hashed as the nearest double (what JSON.parse makes of it).
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) > MAX_SAFE:
        return float(value)
    if isinstance(value, dict):
        return {k: _doubles(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_doubles(v) for v in value]
    return value


def jcs(value: Any) -> bytes:
    return rfc8785.dumps(_doubles(value))


def heldfast_body(tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("input_schema")
    if schema is None:
        schema = tool.get("inputSchema")
    body: dict[str, Any] = {
        "name": str(tool.get("name") or ""),
        "title": str(tool.get("title") or ""),
        "description": str(tool.get("description") or ""),
        "input_schema": dict(schema) if isinstance(schema, dict) else {},
        "annotations": dict(tool["annotations"]) if isinstance(tool.get("annotations"), dict) else {},
    }
    output = tool.get("output_schema")
    if output is None:
        output = tool.get("outputSchema")
    if isinstance(output, dict) and output:
        body["output_schema"] = output
    if isinstance(tool.get("icons"), list) and tool["icons"]:
        body["icons"] = tool["icons"]
    return body


def heldfast_digest(tool: dict[str, Any]) -> str:
    return hashlib.sha256(jcs(heldfast_body(tool))).hexdigest()


AVOW_PROFILE = "agentavow.mcp-tool-definition.v1"
AVOW_FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")


def agentavow_digest(tool: dict[str, Any]) -> str:
    restricted = {k: tool[k] for k in AVOW_FIELDS if tool.get(k) is not None}
    return "sha256:" + hashlib.sha256(jcs({"profile": AVOW_PROFILE, "tool": restricted})).hexdigest()


def agentavow_key(name: str) -> str:
    out = []
    for ch in name:
        if 0x21 <= ord(ch) <= 0x7E and ch not in "%=":
            out.append(ch)
        else:
            out.extend(f"%{b:02X}" for b in ch.encode("utf-8"))
    body = "".join(out)
    if len(body) > 128:
        body = body[:96] + "~" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return "tool:" + body


def _vercel_canonical(value: Any) -> str:
    """ai@7 src/util/canonical-hash.ts canonicalJSON: JSON.stringify with keys sorted, and an
    undefined value written as the bare word ``undefined`` (template-string coercion)."""

    if value is _UNDEFINED:
        return "undefined"
    if value is None or not isinstance(value, (dict, list)):
        return _js_stringify(value)
    if isinstance(value, list):
        return "[" + ",".join(_vercel_canonical(v) for v in value) + "]"
    keys = sorted(value, key=lambda k: k.encode("utf-16-be", "surrogatepass"))
    return "{" + ",".join(_js_stringify(k) + ":" + _vercel_canonical(value[k]) for k in keys) + "}"


class _Undefined:
    pass


_UNDEFINED = _Undefined()


def _js_stringify(value: Any) -> str:
    # JSON.stringify of a primitive: RFC 8785 serializes strings and numbers exactly as ECMAScript does.
    return rfc8785.dumps(_doubles(value)).decode("utf-8")


def vercel_fingerprint(tool: dict[str, Any]) -> str:
    """ai@7 fingerprintTools for a tool built by @ai-sdk/mcp ``tools()`` (schemas 'automatic')."""
    import base64

    description = tool.get("description")
    tagged = {"type": "string", "value": description} if isinstance(description, str) else {"type": "none"}
    schema = dict(tool.get("inputSchema") or {})
    schema["properties"] = schema.get("properties") if schema.get("properties") is not None else {}
    schema["additionalProperties"] = False
    title = tool.get("title")
    if title is None and isinstance(tool.get("annotations"), dict):
        title = tool["annotations"].get("title")
    body = {"description": tagged, "inputSchema": schema, "title": _UNDEFINED if title is None else title}
    digest = hashlib.sha256(_vercel_canonical(body).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
