"""Edge paths: misbehaving servers, a flaky registry, a failing sidecar, refused API calls."""

from __future__ import annotations

import json
import stat
from datetime import UTC

import httpx
import pytest
from fastapi.testclient import TestClient

from histor import badge, registry
from histor.app import create_app
from histor.config import load_settings
from histor.logbook import STH_TYPE, verify_document
from histor.mcpclient import McpReader
from histor.scanner import Scanner, ScannerError

URL = "https://127.0.0.1/mcp"


def reader_for(handler) -> McpReader:
    return McpReader(timeout=5, allow_private=True, transport=httpx.MockTransport(handler))


def rpc(request):
    return json.loads(request.content)


def ok_init(body):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": 7}})


@pytest.mark.parametrize("reply,status", [
    (lambda b: httpx.Response(200, json={"jsonrpc": "2.0", "id": b["id"], "error": {"code": 1}}), "protocol"),
    (lambda b: httpx.Response(200, json={"jsonrpc": "2.0", "id": b["id"], "result": {"nope": 1}}), "protocol"),
    (lambda b: httpx.Response(200, json={"jsonrpc": "2.0", "id": b["id"], "result": {"tools": [], "nextCursor": 5}}), "partial"),
    (lambda b: httpx.Response(200, text=""), "protocol"),
    (lambda b: httpx.Response(200, text="not json"), "protocol"),
    (lambda b: httpx.Response(200, json={"jsonrpc": "2.0", "id": 999, "result": {"tools": []}}), "protocol"),
    (lambda b: httpx.Response(200, text="data: {\"broken\"\n\n", headers={"content-type": "text/event-stream"}), "protocol"),
])
def test_tools_list_misbehaviour_is_a_status_never_a_crash(reply, status):
    def handler(request):
        body = rpc(request)
        if body["method"] == "initialize":
            return ok_init(body)
        if body["method"] == "notifications/initialized":
            raise httpx.ConnectError("notification refused")  # must be tolerated
        return reply(body)

    obs = reader_for(handler).list_tools(URL)
    assert obs.status == status and obs.tools is None
    assert obs.protocol_version == ""  # a non-string protocolVersion is not recorded


def test_batch_replies_and_unterminated_sse_are_read():
    def handler(request):
        body = rpc(request)
        if body["method"] == "initialize":
            return httpx.Response(200, json=[{"jsonrpc": "2.0", "id": body["id"], "result": {}}])
        if body["method"] == "tools/list":
            msg = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "a"}]}})
            return httpx.Response(200, text=f"data: not-json\n\ndata: {msg}", headers={"content-type": "text/event-stream"})
        return httpx.Response(202)

    obs = reader_for(handler).list_tools(URL)
    assert obs.status == "ok" and obs.tools == [{"name": "a"}]


def test_initialize_error_timeout_and_connect_failures():
    assert reader_for(lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {}})).list_tools(URL).status == "protocol"

    def slow(request):
        raise httpx.ReadTimeout("slow")

    def refused(request):
        raise httpx.ConnectError("refused")

    def odd(request):
        raise httpx.RemoteProtocolError("odd")

    assert reader_for(slow).list_tools(URL).status == "timeout"
    assert reader_for(refused).list_tools(URL).status == "connect"
    assert reader_for(odd).list_tools(URL).status == "network"


def test_sse_size_cap(monkeypatch):
    from histor import mcpclient

    monkeypatch.setattr(mcpclient, "MAX_BODY_BYTES", 50)

    def handler(request):
        return httpx.Response(200, text="data: " + "x" * 200 + "\n\n", headers={"content-type": "text/event-stream"})

    assert reader_for(handler).list_tools(URL).status == "too-large"


def test_the_harvester_retries_a_flaky_registry_and_pages():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        if calls["n"] == 2:
            raise httpx.ReadTimeout("slow page")
        cursor = request.url.params.get("cursor")
        assert request.url.params["version"] == "latest"
        if not cursor:
            return httpx.Response(200, json={"servers": [{"server": {"name": "a/x", "remotes": [
                {"type": "streamable-http", "url": "https://a.example/mcp"}]}}], "metadata": {"nextCursor": "c1"}})
        return httpx.Response(200, json={"servers": [], "metadata": {}})

    targets = registry.harvest("https://registry.test", transport=httpx.MockTransport(handler), pause_s=0, retry_delay=0)
    assert [t.name for t in targets] == ["a/x"] and calls["n"] == 4


def test_the_harvester_gives_up_loudly(monkeypatch):
    def always_slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(httpx.ReadTimeout):
        registry.harvest("https://r.test", transport=httpx.MockTransport(always_slow), pause_s=0, retry_delay=0)

    monkeypatch.setattr(registry, "MAX_PAGES", 2)
    pages = iter(range(10**6))
    endless = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"servers": [], "metadata": {"nextCursor": f"c{next(pages)}"}}))
    with pytest.raises(RuntimeError, match="did not end"):
        registry.harvest("https://r.test", transport=endless, pause_s=0, retry_delay=0)
    stuck = httpx.MockTransport(lambda r: httpx.Response(200, json={"servers": [], "metadata": {"nextCursor": "again"}}))
    monkeypatch.setattr(registry, "MAX_PAGES", 50)
    with pytest.raises(RuntimeError, match="did not advance"):
        registry.harvest("https://r.test", transport=stuck, pause_s=0, retry_delay=0)
    # A loop longer than one step (A → B → A) is caught too: the page cap no longer does it.
    ring = {None: "A", "A": "B", "B": "A"}
    looping = httpx.MockTransport(lambda r: httpx.Response(200, json={
        "servers": [], "metadata": {"nextCursor": ring[r.url.params.get("cursor")]}}))
    monkeypatch.setattr(registry, "MAX_PAGES", 1_000_000)
    with pytest.raises(RuntimeError, match="repeated"):
        registry.harvest("https://r.test", transport=looping, pause_s=0, retry_delay=0)
    five_hundreds = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(RuntimeError, match="kept failing"):
        registry.harvest("https://r.test", transport=five_hundreds, pause_s=0, retry_delay=0)


def fake_node_script(tmp_path, body: str):
    script = tmp_path / "scan.mjs"
    script.write_text(body)
    return Scanner(tmp_path)


def test_a_sidecar_error_line_is_raised_not_swallowed(tmp_path):
    s = fake_node_script(tmp_path, 'process.stdin.on("data",()=>{});process.stdin.on("end",()=>{console.log(JSON.stringify({id:"x",error:"boom"}))});')
    with pytest.raises(ScannerError, match="boom"):
        s.scan([{"id": "x", "server": {}, "tools": []}])


def test_a_sidecar_that_forgets_a_job_or_exits_nonzero_is_an_error(tmp_path):
    s = fake_node_script(tmp_path, 'process.stdin.on("data",()=>{});process.stdin.on("end",()=>{console.log("")});')
    with pytest.raises(ScannerError, match="nothing"):
        s.scan([{"id": "x", "server": {}, "tools": []}])
    s = fake_node_script(tmp_path, "process.exit(3)")
    with pytest.raises(ScannerError, match="exited 3"):
        s.rulesets()
    missing = Scanner(tmp_path, node_bin=str(tmp_path / "no-node"))
    with pytest.raises(ScannerError, match="could not run"):
        missing.rulesets()


def test_badge_states_over_time():
    assert badge.badge_state({"current_subject": None}) == ("not observed", "unknown")
    # names only (undigestible or NUM-001): no digest, so no "unchanged" claim
    assert badge.badge_state({"current_subject": "s", "current_toolset": None, "ok_observations": 9, "changes": 0,
                              "unchanged_since": "2020-01-01T00:00:00Z"}) == ("not digestible", "unknown")
    assert badge.badge_state({"current_subject": "s", "current_toolset": "t", "ok_observations": 1, "first_pinned": "2026-09-01T00:00:00Z",
                              "changes": 0})[1] == "pinned"
    msg, state = badge.badge_state({"current_subject": "s", "current_toolset": "t", "ok_observations": 9, "changes": 0,
                                    "unchanged_since": "2020-01-01T00:00:00Z", "last_ok": "2026-09-20T00:00:00Z"})
    assert state == "unchanged" and msg.startswith("unchanged ") and "09-20" in msg
    from datetime import datetime

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert badge.badge_state({"current_subject": "s", "current_toolset": "t", "ok_observations": 3, "changes": 1, "unchanged_since": now})[1] == "changed"
    assert badge._days_since(None) is None


def test_api_refusals_before_any_crawl(world, monkeypatch):
    from dataclasses import replace

    s = world["services"]
    with TestClient(create_app(s)) as client:
        assert client.get("/api/v1/log/sth").status_code == 404
        assert client.get("/api/v1/log/sth/5").status_code == 404
        assert client.get("/api/v1/labels/urn:uuid:none").status_code == 404
        assert client.get("/api/v1/labels/urn:uuid:none/proof").status_code == 404
        assert client.get("/api/v1/stats").json()["log"] == {"treeSize": 0}
        assert client.get("/api/v1/log/proof/consistency", params={"first": 3, "second": 2}).status_code == 400
        assert client.get("/health").json()["tree_size"] == 0
    s.settings = replace(s.settings, operator_token="")
    with TestClient(create_app(s)) as client:
        assert client.post("/api/v1/admin/crawl").status_code == 503


def test_logbook_edges(world):
    s = world["services"]
    assert s.logbook.publish_sth() is None
    with pytest.raises(ValueError):
        s.logbook.inclusion(0, 5)
    with pytest.raises(ValueError):
        s.logbook.consistency(1, 5)
    assert not verify_document({"type": "other"}, s.key.did, STH_TYPE)
    assert not verify_document({"type": STH_TYPE, "signature": {"value": "!!"}}, "did:key:bogus", STH_TYPE)


def test_check_edge_inputs(world):
    from histor.check import CheckError

    c = world["services"].checker
    with pytest.raises(CheckError):
        c.check({"endpoint": 5}, allow_scan=False)
    with pytest.raises(CheckError):
        c.check({"name": 5}, allow_scan=False)
    res = c.check({"name": "x", "tools": [{"name": "a"}, {"name": "a"}]}, allow_scan=False)
    assert res["query"]["digestError"]["code"] == "MTL-SUBJ-003" and res["match"] == "not-listed"


def test_config_rejects_bad_values(monkeypatch):
    monkeypatch.setenv("HISTOR_PORT", "abc")
    with pytest.raises(RuntimeError, match="integer"):
        load_settings()
    monkeypatch.setenv("HISTOR_PORT", "0")
    with pytest.raises(RuntimeError, match=">= 1"):
        load_settings()
    monkeypatch.delenv("HISTOR_PORT")
    monkeypatch.setenv("HISTOR_PROFILE", "staging")
    with pytest.raises(RuntimeError, match="dev or prod"):
        load_settings()
    assert stat  # imported for readability of permission checks elsewhere
