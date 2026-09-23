"""The read-only client against misbehaving servers: pagination, SSE, loops, size, redirects."""

from __future__ import annotations

import json

import httpx

from histor import mcpclient
from histor.mcpclient import McpReader
from tests.conftest import FakeMcp, tool

URL = "https://127.0.0.1/mcp"


def reader(mcp: FakeMcp) -> McpReader:
    return McpReader(timeout=5, allow_private=True, transport=mcp.transport())


def test_only_the_three_read_methods_are_ever_sent():
    mcp = FakeMcp()
    mcp.tools["/mcp"] = [tool("a"), tool("b")]
    obs = reader(mcp).list_tools(URL)
    assert obs.status == "ok" and [t["name"] for t in obs.tools] == ["a", "b"]
    assert {m for _, m in mcp.calls} == {"initialize", "notifications/initialized", "tools/list"}
    assert obs.server_info["name"] == "fake/mcp" and obs.protocol_version == "2025-06-18"


def test_pages_are_drained():
    mcp = FakeMcp()
    mcp.tools["/mcp"] = [tool(f"t{i}") for i in range(10)]
    mcp.page_size["/mcp"] = 4
    obs = reader(mcp).list_tools(URL)
    assert obs.status == "ok" and len(obs.tools) == 10 and obs.pages == 3


def test_a_cursor_that_does_not_advance_is_partial_not_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {"tools": [tool("x")], "nextCursor": "same"}})
        return httpx.Response(202)

    obs = McpReader(timeout=5, allow_private=True, transport=httpx.MockTransport(handler)).list_tools(URL)
    assert obs.status == "partial" and obs.tools is None


def test_sse_answers_are_read_up_to_our_response():
    mcp = FakeMcp()
    mcp.tools["/mcp"] = [tool("streamed")]
    mcp.sse.add("/mcp")
    obs = reader(mcp).list_tools(URL)
    assert obs.status == "ok" and obs.tools[0]["name"] == "streamed"


def test_http_errors_and_redirects_are_statuses():
    mcp = FakeMcp()
    mcp.status["/mcp"] = 401
    assert reader(mcp).list_tools(URL).status == "http-401"
    mcp.status["/mcp"] = 307
    obs = reader(mcp).list_tools(URL)
    assert obs.status == "http-307" and "redirect" in obs.detail


def test_duplicate_json_members_are_refused():
    mcp = FakeMcp()
    mcp.raw["/mcp"] = '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"a","name":"b"}]}}'
    obs = reader(mcp).list_tools(URL)
    assert obs.status == "protocol" and "duplicate" in obs.detail


def test_oversized_bodies_are_cut_off(monkeypatch):
    monkeypatch.setattr(mcpclient, "MAX_BODY_BYTES", 200)
    mcp = FakeMcp()
    mcp.tools["/mcp"] = [tool(f"t{i}", "x" * 50) for i in range(10)]
    assert reader(mcp).list_tools(URL).status == "too-large"


def test_a_private_endpoint_is_blocked_by_default():
    obs = McpReader(timeout=5).list_tools("https://127.0.0.1/mcp")
    assert obs.status == "blocked-address"
