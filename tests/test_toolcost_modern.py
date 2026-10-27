"""Modern/legacy stdio negotiation against controlled local fixtures, never installed packages."""

from __future__ import annotations

import json
import sys
import textwrap
import time

import pytest

from histor.toolcost_cli import MODERN_PROTOCOL_VERSIONS, SourceError, _Stdio, tools_from_json

SERVER = textwrap.dedent('''
    import json, sys
    mode, log = sys.argv[1:]
    modern = "2026-07-28"
    discoveries = 0
    for line in sys.stdin:
        msg = json.loads(line)
        with open(log, "a") as target:
            target.write(json.dumps(msg) + "\\n")
        method = msg.get("method")
        if "id" not in msg or method is None:
            continue
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        if method == "server/discover":
            discoveries += 1
            if mode == "legacy-silent":
                continue
            if mode == "malformed-code":
                reply["error"] = {"code": "-32022"}
            elif mode == "malformed-float-code":
                reply["error"] = {"code": -32022.0}
            elif mode == "malformed-error":
                reply["error"] = None
            elif mode == "dual-envelope":
                reply["error"] = {"code": -32601}
                reply["result"] = {}
            elif mode == "missing-jsonrpc":
                del reply["jsonrpc"]
                reply["error"] = {"code": -32601}
            elif mode in ("mismatched-modern-id", "bool-modern-id"):
                reply["id"] = 999 if mode == "mismatched-modern-id" else True
                reply["error"] = {"code": -32021}
            elif mode.startswith("legacy-no-id--"):
                del reply["id"]
                reply["error"] = {"code": int(mode.split("legacy-no-id-", 1)[1]), "message": "legacy rejection"}
            elif mode.startswith("legacy--"):
                reply["error"] = {"code": int(mode.split("legacy-", 1)[1]), "message": "legacy rejection"}
            elif mode in ("unsupported", "unsupported-no-id", "legacy-only-advertised"):
                reply["error"] = {"code": -32022, "data": {"supported": ["2099-01-01"] if mode.startswith("unsupported") else ["2025-06-18"]}}
                if mode == "unsupported-no-id":
                    del reply["id"]
            elif mode in ("capability", "headers"):
                reply["error"] = {"code": -32021 if mode == "capability" else -32020, "message": "modern rejection"}
            elif mode == "retry-forever" or (mode in ("retry-success", "retry-failure") and discoveries == 1):
                reply["error"] = {"code": -32022, "data": {"supported": [modern]}}
            elif mode == "retry-failure":
                reply["error"] = {"code": -32601, "message": "still no discovery"}
            elif mode == "discover-input":
                reply["result"] = {"resultType": "input_required", "inputRequests": []}
            elif mode == "server-request":
                print(json.dumps({"jsonrpc": "2.0", "id": "server", "method": "roots/list"}), flush=True)
                continue
            else:
                reply["result"] = {
                    "resultType": "complete", "supportedVersions": ["2099-01-01"] if mode == "no-mutual" else [modern],
                    "capabilities": {"tools": {}}, "ttlMs": 0, "cacheScope": "public",
                    "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "fixture", "version": "server-self-report"}}
                }
        elif method == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": "roots", "method": "roots/list"}), flush=True)
            reply["result"] = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "legacy", "version": "1"}}
        elif method == "tools/list":
            params = msg.get("params") or {}
            tool = {"name": "second" if params.get("cursor") else "first", "inputSchema": {"type": "object"}}
            if mode == "list-input":
                reply["result"] = {"resultType": "input_required", "tools": [tool], "inputRequests": []}
            else:
                reply["result"] = {"tools": [tool]}
                if not mode.startswith("legacy-"):
                    reply["result"]["resultType"] = "complete"
                if not params.get("cursor"):
                    reply["result"]["nextCursor"] = "page-2"
                if mode.startswith("invalid-cursor-"):
                    reply["result"]["nextCursor"] = json.loads(mode.split("invalid-cursor-", 1)[1])
        else:
            raise AssertionError(method)
        print(json.dumps(reply), flush=True)
''')


@pytest.fixture
def fixture_server(tmp_path):
    path = tmp_path / "fixture_server.py"
    path.write_text(SERVER)
    clients = []

    def create(mode, timeout=3):
        log = tmp_path / f"{mode}.jsonl"
        client = _Stdio([sys.executable, str(path), mode, str(log)], time.monotonic() + timeout)
        clients.append(client)
        return client, lambda: [json.loads(line) for line in log.read_text().splitlines()]

    yield create
    for client in clients:
        client.close()


@pytest.mark.parametrize("mode", ["modern", "retry-success"])
def test_modern_stdio_discovers_and_drains_pages_without_initializing(fixture_server, mode):
    client, messages = fixture_server(mode)
    assert [tool["name"] for tool in client.tools()] == ["first", "second"]
    assert client.modern and client.protocol_version == MODERN_PROTOCOL_VERSIONS[0]
    assert client.server_info == {"name": "fixture", "version": "server-self-report"}
    requests = messages()
    assert requests[0]["method"] == "server/discover"
    assert all(msg["method"] in ("server/discover", "tools/list") for msg in requests)
    assert len({msg["id"] for msg in requests}) == len(requests)
    for msg in requests:
        assert msg["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert msg["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {}
        assert msg["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["name"] == "histor-tool-cost"
    assert requests[-1]["params"]["cursor"] == "page-2"


@pytest.mark.parametrize("mode", ["legacy--32601", "legacy--32602", "legacy--32000", "legacy--32700", "legacy-silent",
                                  "legacy-no-id--32601", "legacy-no-id--32700"])
def test_stdio_legacy_rejections_and_silence_fallback_within_total_budget(fixture_server, mode):
    client, messages = fixture_server(mode, timeout=0.8 if mode == "legacy-silent" else 3)
    assert [tool["name"] for tool in client.tools()] == ["first", "second"]
    assert not client.modern and client.protocol_version == "2025-06-18"
    assert client.server_info == {"name": "legacy", "version": "1"}
    observed = messages()
    assert [msg.get("method") for msg in observed if "method" in msg] == [
        "server/discover", "initialize", "notifications/initialized", "tools/list", "tools/list"
    ]
    assert any(msg.get("id") == "roots" and "error" in msg for msg in observed)
    assert all("_meta" not in msg["params"] for msg in observed if msg.get("method") == "tools/list")


@pytest.mark.parametrize("mode,match,attempts", [
    ("unsupported", "no mutually supported", 1),
    ("unsupported-no-id", "no mutually supported", 1),
    ("legacy-only-advertised", "no mutually supported", 1),
    ("no-mutual", "no mutually supported", 1),
    ("capability", "server/discover failed", 1),
    ("headers", "server/discover failed", 1),
    ("retry-forever", "server/discover failed", 2),
    ("retry-failure", "server/discover failed", 2),
    ("discover-input", "input_required", 1),
    ("server-request", "no client interaction", 1),
])
def test_identified_modern_server_never_downgrades_or_interacts(fixture_server, mode, match, attempts):
    client, messages = fixture_server(mode)
    with pytest.raises(SourceError, match=match):
        client.tools()
    observed = messages()
    assert len(observed) == attempts and all(msg.get("method") == "server/discover" for msg in observed)
    assert len({msg["id"] for msg in observed}) == len(observed)


def test_input_required_tool_list_is_incomplete_even_with_a_tools_array(fixture_server):
    client, messages = fixture_server("list-input")
    with pytest.raises(SourceError, match="input_required.*observation incomplete"):
        client.tools()
    assert [msg["method"] for msg in messages()] == ["server/discover", "tools/list"]


@pytest.mark.parametrize("mode", ["malformed-code", "malformed-float-code", "malformed-error", "dual-envelope",
                                  "missing-jsonrpc", "mismatched-modern-id", "bool-modern-id"])
def test_malformed_or_wrong_id_discovery_never_triggers_legacy_fallback(fixture_server, mode):
    client, messages = fixture_server(mode)
    with pytest.raises(SourceError, match="no downgrade"):
        client.tools()
    assert [msg["method"] for msg in messages()] == ["server/discover"]


@pytest.mark.parametrize("cursor", [False, 0, [], {}, "", 1, ["next"]])
def test_invalid_stdio_cursor_never_marks_listing_complete(fixture_server, cursor):
    client, messages = fixture_server("invalid-cursor-" + json.dumps(cursor, separators=(",", ":")))
    with pytest.raises(SourceError, match="invalid pagination cursor.*incomplete"):
        client.tools()
    assert [msg["method"] for msg in messages()] == ["server/discover", "tools/list"]


@pytest.mark.parametrize("wrapped", [False, True])
def test_saved_input_required_results_are_not_counted_as_complete(wrapped):
    data = {"resultType": "input_required", "tools": [{"name": "partial"}]}
    if wrapped:
        data = {"jsonrpc": "2.0", "id": 1, "result": data}
    with pytest.raises(SourceError, match="tool list incomplete"):
        tools_from_json(data)
