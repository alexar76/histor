"""A minimal Streamable HTTP MCP client for the live checks: initialize, then tools/list."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

UA = "histor-interop-check"


def post(url: str, body: dict[str, Any], session: str | None = None, version: str | None = None):
    """POST one JSON-RPC message; ``version`` is sent as MCP-Protocol-Version when given."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": UA}
    if session:
        headers["Mcp-Session-Id"] = session
    if version:
        headers["MCP-Protocol-Version"] = version
    request = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode()
        session = response.headers.get("Mcp-Session-Id") or session
    if raw.lstrip().startswith("{"):
        return json.loads(raw), session
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:]), session
    return None, session


def initialize(url: str, requested: str, header: bool = True) -> tuple[str, str | None, str | None]:
    """``(negotiated, session, version_to_send)``; ``header=False`` sends no version header afterwards."""
    params = {"protocolVersion": requested, "capabilities": {}, "clientInfo": {"name": UA, "version": "0"}}
    result, session = post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    negotiated = result["result"]["protocolVersion"]
    send = negotiated if header else None
    try:
        post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session, send)
    except OSError:
        pass
    return negotiated, session, send


def list_tools(url: str, requested: str = "2025-06-18", header: bool = True, all_pages: bool = True):
    """``(negotiated, pages, tools)`` following nextCursor unless ``all_pages`` is false."""
    negotiated, session, send = initialize(url, requested, header)
    tools, pages, cursor = [], [], None
    while True:
        params = {"cursor": cursor} if cursor else {}
        message = {"jsonrpc": "2.0", "id": 2 + len(pages), "method": "tools/list", "params": params}
        result, session = post(url, message, session, send)
        page = result["result"]["tools"]
        tools += page
        pages.append(len(page))
        cursor = result["result"].get("nextCursor")
        if not cursor or not all_pages or len(pages) >= 30:
            return negotiated, pages, tools
