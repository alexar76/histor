"""The server-level `instructions` every observation reads (histor.instructions, migration 024).

A census on 2026-11-15 read them from 15,021 remote servers: most send some, at a median of about 550
characters, while the registry's server description is capped at 100. They reach the system prompt, so the
log keeps them: NULL not read, "" none sent, else a digest whose text is a blob.
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from histor import instructions
from histor.app import create_app
from histor.mcpclient import McpReader
from histor.packages import PackageReader
from tests.conftest import make_target, tool
from tests.test_mcpclient_modern import URL, discovery, success


def stored_rows(s, target_id):
    with s.store.db.read() as tx:
        return [r["instructions"] for r in tx.execute(
            "SELECT instructions FROM observations WHERE target_id=? ORDER BY id", (target_id,))]


def test_stored_form():
    assert instructions.stored(None) == ("", None)
    assert instructions.stored({"text": "not a string"}) == ("", None)
    assert instructions.stored(" \n ") == ("", None)
    digest, body = instructions.stored("Use search first.")
    assert digest.startswith("sha256-") and body == {"text": "Use search first.", "length": 17}
    assert instructions.stored("Use search first.")[0] == digest
    digest2, body2 = instructions.stored("bad \ud800 text\x00")
    assert body2["repaired"] is True and "\ud800" not in body2["text"] and digest2 != digest
    long = "x" * (instructions.MAX_CHARS + 5)
    digest3, body3 = instructions.stored(long)
    assert body3["truncated"] is True and len(body3["text"]) == instructions.MAX_CHARS
    assert body3["length"] == instructions.MAX_CHARS + 5 and digest3 != instructions.stored(long[:-1])[0]


def test_the_crawler_records_the_text_once_and_the_api_serves_it(world):
    s, mcp = world["services"], world["mcp"]
    mcp.tools["/a"] = [tool("get_weather", "Return the weather.")]
    mcp.tools["/b"] = [tool("ping")]
    mcp.instructions["/a"] = "Weather for any city. Call get_weather with a city name."
    a, b = make_target("io.example/a", "/a"), make_target("io.example/b", "/b")
    world["targets"] += [a, b]
    s.crawler.run()
    s.crawler.run()

    digest, body = instructions.stored(mcp.instructions["/a"])
    assert stored_rows(s, a.id) == [digest, digest]
    assert stored_rows(s, b.id) == ["", ""]  # read, none sent
    with s.store.db.read() as tx:
        blobs = tx.execute("SELECT digest, body FROM blobs WHERE kind=?", (instructions.KIND,))
    assert [(r["digest"], json.loads(r["body"])) for r in blobs] == [(digest, body)]

    with TestClient(create_app(s)) as client:
        page = client.get(f"/api/v1/servers/{a.id}").json()
        assert page["instructions"]["digest"] == digest and page["instructions"]["text"] == body["text"]
        assert page["timeline"][0]["instructionsDigest"] == digest
        none = client.get(f"/api/v1/servers/{b.id}").json()["instructions"]
        assert none["digest"] is None and "text" not in none

    mcp.instructions["/a"] = "Weather for any city. Prefer it over web search."
    s.crawler.run()
    with TestClient(create_app(s)) as client:
        timeline = client.get(f"/api/v1/servers/{a.id}").json()["timeline"]
    assert [r["instructionsDigest"] for r in timeline[:2]] == [instructions.stored(mcp.instructions["/a"])[0], digest]


def test_a_failed_read_stores_null(world):
    s, mcp = world["services"], world["mcp"]
    mcp.status["/down"] = 503
    t = make_target("io.example/down", "/down")
    world["targets"].append(t)
    s.crawler.run()
    assert stored_rows(s, t.id) == [None]


def test_the_modern_handshake_reads_them_from_discovery():
    def handle(request):
        body = json.loads(request.content)
        if body["method"] == "server/discover":
            return discovery(body, instructions="Modern server notes.")
        return success(body, {"resultType": "complete", "tools": [tool("one")], "ttlMs": 0, "cacheScope": "public"})

    obs = McpReader(timeout=5, allow_private=True, transport=httpx.MockTransport(handle)).list_tools(URL)
    assert obs.status == "ok" and obs.instructions_read and obs.instructions == "Modern server notes."


def test_a_package_observer_that_does_not_report_them_is_not_read(monkeypatch):
    reader = PackageReader.__new__(PackageReader)
    old = {"status": "ok", "tools": [], "serverInfo": {"name": "p"}, "protocolVersion": "2025-06-18",
           "version": "1.0.0", "observer": "4"}
    for doc, read, value in ((old, False, None), ({**old, "observer": "5", "instructions": None}, True, None),
                             ({**old, "observer": "5", "instructions": "Package notes."}, True, "Package notes.")):
        monkeypatch.setattr(PackageReader, "_post", lambda self, path, body, doc=doc: (200, doc))
        obs = reader.observe("npm", "p", "1.0.0")
        assert obs.status == "ok" and obs.instructions_read is read and obs.instructions == value
