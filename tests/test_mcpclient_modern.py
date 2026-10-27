"""Modern HTTP observations and conservative era/version boundaries; no real network."""

from __future__ import annotations

import json

import httpx
import pytest

from histor import mcpclient
from histor.mcpclient import McpReader
from histor.netguard import PinnedTarget
from tests.conftest import FakeMcp, tool

URL = "https://127.0.0.1/mcp"
MODERN = "2026-07-28"


def observe(handler, **kwargs):
    return McpReader(timeout=5, allow_private=True, transport=httpx.MockTransport(handler), **kwargs).list_tools(URL)


def success(body, result, *, headers=None):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}, headers=headers)


def error(body, code, *, status=400, supported=None):
    fault = {"code": code, "message": "DO NOT COPY: untrusted private-looking server text"}
    if supported is not None:
        fault["data"] = {"supported": supported}
    return httpx.Response(status, json={"jsonrpc": "2.0", "id": body["id"], "error": fault})


def discovery(body, **kwargs):
    protocol = body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"]
    return success(body, {"resultType": "complete", "supportedVersions": [protocol], "capabilities": {"tools": {}},
        "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "modern-fixture", "version": "1"}},
        "ttlMs": 0, "cacheScope": "public", **kwargs})


def test_modern_only_and_dual_era_servers_use_metadata_without_initialize():
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(request)
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["MCP-Protocol-Version"] == MODERN
        assert request.headers["Mcp-Method"] == body["method"]
        assert "Mcp-Session-Id" not in request.headers
        meta = body["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == MODERN
        assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
        assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "histor"
        if body["method"] == "server/discover":
            return discovery(body)
        assert body["method"] == "tools/list"
        cursor = body["params"].get("cursor")
        result = {"resultType": "complete", "tools": [tool("second" if cursor else "first")],
                  "ttlMs": 3600000, "cacheScope": "public"}
        if cursor is None:
            result["nextCursor"] = "page-2"
        else:
            assert cursor == "page-2"
        # A server cannot turn a modern observation into a session by sending this header.
        return success(body, result, headers={"Mcp-Session-Id": "ignored"})

    obs = observe(handle)
    assert obs.status == "ok" and [t["name"] for t in obs.tools] == ["first", "second"]
    assert obs.pages == 2 and obs.protocol_version == MODERN
    assert obs.server_info == {"name": "modern-fixture", "version": "1"}
    assert [json.loads(r.content)["method"] for r in requests] == ["server/discover", "tools/list", "tools/list"]


@pytest.mark.parametrize("code", [-32600, -32601, -32602])
def test_legacy_discovery_json_rpc_errors_in_http_200_fall_back(code):
    legacy = FakeMcp()
    legacy.tools["/mcp"] = [tool("old")]
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "server/discover":
            return error(body, code, status=200)
        return legacy.handle(request)

    obs = observe(handle)
    assert obs.status == "ok" and obs.protocol_version == "2025-06-18"
    assert calls == ["server/discover", "initialize", "notifications/initialized", "tools/list"]


@pytest.mark.parametrize("status", [400, 404, 405])
def test_legacy_http_rejection_allows_only_initial_probe_fallback(status):
    legacy = FakeMcp()
    legacy.tools["/mcp"] = [tool("old")]

    def handle(request):
        if json.loads(request.content)["method"] == "server/discover":
            return httpx.Response(status, text="<html>Old endpoint</html>", headers={"content-type": "text/html"})
        assert "Mcp-Method" not in request.headers
        return legacy.handle(request)

    assert observe(handle).status == "ok"


@pytest.mark.parametrize("status", [400, 404, 405])
@pytest.mark.parametrize("request_id", [False, 999, "0"])
def test_wrong_id_http_error_does_not_trigger_legacy_initialization(status, request_id):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content)["method"])
        return httpx.Response(status, json={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601}})

    obs = observe(handle)
    assert obs.status == "protocol" and obs.tools is None
    assert "no downgrade" in obs.detail and calls == ["server/discover"]


@pytest.mark.parametrize("status", [401, 403, 407, 408, 429, 500, 503, 307])
def test_auth_rate_limit_network_and_redirect_failures_never_downgrade(status):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content)["method"])
        return httpx.Response(status, text="untrusted text", headers={"location": "https://elsewhere.example/mcp"})

    obs = observe(handle)
    assert obs.status == f"http-{status}" and obs.tools is None
    assert calls == ["server/discover"]
    assert "untrusted" not in obs.detail


@pytest.mark.parametrize("status", [200, 400, 404, 405])
@pytest.mark.parametrize("code", [-32020, -32021, -32022])
def test_recognized_modern_errors_never_initialize_or_echo_server_messages(status, code):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        return error(body, code, status=status, supported=["2025-11-25"])

    obs = observe(handle)
    assert obs.status == "protocol" and obs.tools is None
    assert calls == ["server/discover"]
    assert "DO NOT COPY" not in obs.detail and "no downgrade" in obs.detail


@pytest.mark.parametrize("supported", [None, "2026-07-28", ["2026-07-28\r\nX: 1"], [7], [MODERN] * 33, [MODERN]])
def test_invalid_or_contradictory_supported_versions_cannot_trigger_unbounded_retry(supported):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        return error(body, -32022, supported=supported)

    obs = observe(handle)
    assert obs.status == "protocol" and obs.tools is None and len(calls) == 1


def test_version_retry_is_limited_to_implemented_modern_revisions_before_listing(monkeypatch):
    # Simulate a future reader implementing a second modern revision with the same
    # request profile. An arbitrary server-supplied date is never sufficient.
    alternate = "2026-07-29"
    monkeypatch.setattr(mcpclient, "MODERN_PROTOCOL_VERSIONS", (MODERN, alternate))
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        requested = request.headers["MCP-Protocol-Version"]
        if requested == MODERN:
            return error(body, -32022, supported=[alternate])
        assert requested == alternate
        if body["method"] == "server/discover":
            return discovery(body)
        return success(body, {"resultType": "complete", "tools": []})

    obs = observe(handle)
    assert obs.status == "ok" and obs.protocol_version == alternate
    assert [b["method"] for b in calls] == ["server/discover", "server/discover", "tools/list"]
    assert len({b["id"] for b in calls}) == 3


def test_advertised_version_ping_pong_is_bounded(monkeypatch):
    alternate = "2026-07-29"
    monkeypatch.setattr(mcpclient, "MODERN_PROTOCOL_VERSIONS", (MODERN, alternate))
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        return error(body, -32022, supported=[MODERN, alternate])

    obs = observe(handle)
    assert obs.status == "protocol" and len(calls) == 2
    assert {b["method"] for b in calls} == {"server/discover"}


@pytest.mark.parametrize("response", [
    lambda b: httpx.Response(200, text="not JSON"),
    lambda b: httpx.Response(200, json={"jsonrpc": "2.0", "id": 999, "result": {}}),
    lambda b: httpx.Response(200, json={"jsonrpc": "2.0", "id": False, "result": {}}),
    lambda b: success(b, {"tools": []}),
    lambda b: discovery(b, capabilities=None),
    lambda b: discovery(b, supportedVersions=["2025-11-25"]),
    lambda b: discovery(b, _meta=[]),
    lambda b: httpx.Response(200, json=[{"jsonrpc": "2.0", "id": b["id"], "result": {}}]),
    lambda b: httpx.Response(400, json={"jsonrpc": "2.0", "id": b["id"], "error": {"code": "-32022"}}),
    lambda b: httpx.Response(400, json=[{"jsonrpc": "2.0", "id": b["id"], "error": {"code": -32022}}]),
])
def test_successful_malformed_modern_discovery_never_falls_back(response):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        return response(body)

    obs = observe(handle)
    assert obs.status == "protocol" and obs.tools is None and calls == ["server/discover"]


@pytest.mark.parametrize("stage", ["server/discover", "tools/list"])
def test_interactive_result_is_incomplete_and_never_answered(stage):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["method"] == "server/discover" and stage != "server/discover":
            return discovery(body)
        return success(body, {"resultType": "input_required", "tools": [tool("not-a-complete-list")],
                              "inputRequests": {"login": {"method": "elicitation/create", "params": {}}}})

    obs = observe(handle)
    assert obs.status == "partial" and obs.tools is None
    assert all(b["method"] in ("server/discover", "tools/list") and "inputResponses" not in b["params"] for b in calls)


def test_pagination_error_cannot_negotiate_version_or_switch_to_legacy():
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["method"] == "server/discover":
            return discovery(body)
        assert request.headers["MCP-Protocol-Version"] == MODERN
        if "cursor" not in body["params"]:
            return success(body, {"resultType": "complete", "tools": [tool("discard-me")], "nextCursor": "next"})
        return error(body, -32022, supported=["2025-06-18"])

    obs = observe(handle)
    assert obs.status == "protocol" and obs.tools is None and obs.protocol_version == MODERN
    assert [b["method"] for b in calls] == ["server/discover", "tools/list", "tools/list"]


def test_cursor_cycle_is_partial_without_hitting_the_page_limit():
    def handle(request):
        body = json.loads(request.content)
        if body["method"] == "server/discover":
            return discovery(body)
        cursor = body["params"].get("cursor")
        return success(body, {"resultType": "complete", "tools": [],
                              "nextCursor": {None: "A", "A": "B", "B": "A"}[cursor]})

    obs = observe(handle)
    assert obs.status == "partial" and obs.tools is None and obs.pages == 3


@pytest.mark.parametrize("cursor", [False, 0, [], {}, "", 1, ["next"]])
def test_invalid_cursor_never_marks_a_modern_tool_list_complete(cursor):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "server/discover":
            return discovery(body)
        return success(body, {"resultType": "complete", "tools": [tool("partial")], "nextCursor": cursor})

    obs = observe(handle)
    assert obs.status == "partial" and obs.tools is None and obs.pages == 1
    assert calls == ["server/discover", "tools/list"]


def test_cache_hints_do_not_cause_reuse_and_scope_cannot_change_between_pages():
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["method"] == "server/discover":
            return discovery(body)
        cursor = body["params"].get("cursor")
        result = {"tools": [], "ttlMs": 10**12, "cacheScope": "private" if cursor else "public"}
        if not cursor:
            result["nextCursor"] = "next"
        return success(body, result)

    for _ in range(2):
        obs = observe(handle)
        assert obs.status == "partial" and obs.tools is None
    assert len(calls) == 6


def test_probe_error_bodies_are_bounded_and_duplicate_members_do_not_downgrade(monkeypatch):
    calls = []

    def oversized(request):
        calls.append(request)
        return httpx.Response(400, text="x" * 1024)

    monkeypatch.setattr(mcpclient, "MAX_BODY_BYTES", 256)
    obs = observe(oversized)
    assert obs.status == "too-large" and len(calls) == 1

    calls.clear()

    def duplicate(request):
        calls.append(request)
        return httpx.Response(400, text='{"error":{"code":-32601,"code":-32022}}',
                              headers={"content-type": "application/json"})

    obs = observe(duplicate)
    assert obs.status == "protocol" and "duplicate" in obs.detail and len(calls) == 1


def test_modern_error_in_sse_http_400_is_recognized_without_downgrade():
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        message = {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32021}}
        return httpx.Response(400, text="data: " + json.dumps(message) + "\n\n",
                              headers={"content-type": "text/event-stream"})

    obs = observe(handle)
    assert obs.status == "protocol" and "-32021" in obs.detail and calls == ["server/discover"]


@pytest.mark.parametrize("limit", ["bytes", "tools", "pages"])
def test_modern_tool_set_limits_apply_across_all_pages(monkeypatch, limit):
    if limit == "bytes":
        monkeypatch.setattr(mcpclient, "MAX_SET_BYTES", 350)
    elif limit == "tools":
        monkeypatch.setattr(mcpclient, "MAX_TOOLS", 1)
    else:
        monkeypatch.setattr(mcpclient, "MAX_PAGES", 2)

    def handle(request):
        body = json.loads(request.content)
        if body["method"] == "server/discover":
            return discovery(body)
        cursor = int(body["params"].get("cursor", "0"))
        return success(body, {"resultType": "complete", "tools": [tool(f"t{cursor}", "x" * 50)],
                              "nextCursor": str(cursor + 1)})

    obs = observe(handle)
    assert obs.status == ("partial" if limit == "pages" else "too-large") and obs.tools is None


def test_modern_pages_share_the_original_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(mcpclient.time, "monotonic", lambda: now[0])
    timeouts = []

    def handle(request):
        body = json.loads(request.content)
        timeouts.append(request.extensions["timeout"]["read"])
        now[0] += 0.4
        if body["method"] == "server/discover":
            return discovery(body)
        return success(body, {"resultType": "complete", "tools": [tool("a")], "nextCursor": "again"})

    obs = observe(handle, deadline=1.0)
    assert obs.status == "timeout" and obs.tools is None
    assert timeouts == pytest.approx([1.0, 0.6, 0.2])


def test_legacy_fallback_cannot_reset_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(mcpclient.time, "monotonic", lambda: now[0])
    legacy = FakeMcp()
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body["method"])
        now[0] += 0.6
        return legacy.handle(request)

    obs = observe(handle, deadline=1.0)
    assert obs.status == "timeout" and obs.tools is None
    assert calls == ["server/discover", "initialize"]


def test_discovery_and_all_pages_use_one_resolved_address_and_original_host(monkeypatch):
    resolves = []
    requests = []

    def pin(url, **kwargs):
        resolves.append(url)
        return PinnedTarget("https://93.184.216.34/mcp", "mcp.example", "mcp.example", "93.184.216.34")

    monkeypatch.setattr(mcpclient, "resolve_public", pin)

    def handle(request):
        body = json.loads(request.content)
        requests.append(request)
        assert request.url.host == "93.184.216.34" and request.headers["host"] == "mcp.example"
        assert request.extensions["sni_hostname"] == "mcp.example"
        if body["method"] == "server/discover":
            return discovery(body)
        result = {"tools": []}
        if "cursor" not in body["params"]:
            result["nextCursor"] = "next"
        return success(body, result)

    obs = observe(handle)
    assert obs.status == "ok" and obs.address == "93.184.216.34"
    assert len(resolves) == 1 and len(requests) == 3
