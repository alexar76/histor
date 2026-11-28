"""A server that answers MCP 2026-07-28 and the handshake, and lists other definitions in each.

The log holds the reading HISTOR takes: 2026-07-28 where the server answers it. A client that opens
with ``initialize`` then holds a set the log has never seen, and /check used to tell it "different from
every set observed". The crawl now takes the handshake reading too, keeps it beside the log, and /check
answers ``same-other-revision`` for it (4 of 277 sampled servers listed differently, 2026-11-27).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from histor.app import create_app
from histor.check import CHECK_TYPE
from histor.logbook import verify_document
from histor.mcpclient import McpReader
from histor.subject import tool_set_digest
from tests.conftest import FakeMcp, make_target, tool

MODERN_TOOLS = [{**tool("get_price", "Return the price."), "outputSchema": {"oneOf": [{"type": "object"}, {"type": "null"}]}}]
HANDSHAKE_TOOLS = [{**tool("get_price", "Return the price."), "outputSchema": {
    "type": "object", "properties": {"result": {"oneOf": [{"type": "object"}, {"type": "null"}]}}, "required": ["result"]}}]
PLAIN = [tool("get_weather", "Return the weather.")]
MODERN_DIGEST, HANDSHAKE_DIGEST = tool_set_digest(MODERN_TOOLS)[0], tool_set_digest(HANDSHAKE_TOOLS)[0]


def check(client, **body):
    return client.post("/api/v1/check", json=body)


@pytest.fixture
def dual(world):
    """/dual lists differently per revision, /both the same in each, /old only knows the handshake."""
    mcp = world["mcp"]
    mcp.modern["/dual"], mcp.tools["/dual"] = MODERN_TOOLS, HANDSHAKE_TOOLS
    mcp.modern["/both"], mcp.tools["/both"] = PLAIN, PLAIN
    mcp.tools["/old"] = PLAIN
    world["targets"] += [make_target("io.example/dual", "/dual"), make_target("io.example/both", "/both"),
                         make_target("io.example/old", "/old")]
    world["services"].crawler.run()
    with TestClient(create_app(world["services"])) as client:
        yield client, world


def target_id(world, name):
    return next(t["id"] for t in world["services"].store.all_targets() if t["name"] == name)


def test_reader_can_open_with_the_handshake_on_a_server_that_answers_both():
    mcp = FakeMcp()
    mcp.modern["/dual"], mcp.tools["/dual"] = MODERN_TOOLS, HANDSHAKE_TOOLS
    reader = McpReader(timeout=5, allow_private=True, transport=httpx.MockTransport(mcp.handle))
    first = reader.list_tools("https://127.0.0.1/dual")
    assert first.status == "ok" and first.protocol_version == "2026-07-28" and first.tools == MODERN_TOOLS
    mcp.calls.clear()
    second = reader.list_tools("https://127.0.0.1/dual", handshake_only=True)
    assert second.status == "ok" and second.protocol_version == "2025-06-18" and second.tools == HANDSHAKE_TOOLS
    assert [m for _, m in mcp.calls] == ["initialize", "notifications/initialized", "tools/list"]


def test_the_crawl_keeps_the_handshake_reading_beside_the_log_only_for_modern_servers(dual):
    _, world = dual
    store = world["services"].store
    row = store.revision_reading(target_id(world, "io.example/dual"))
    assert row["status"] == "ok" and row["protocol"] == "2025-06-18" and row["tool_count"] == 1
    assert row["beside_toolset"] == MODERN_DIGEST and row["toolset"] == HANDSHAKE_DIGEST
    same = store.revision_reading(target_id(world, "io.example/both"))
    assert same["toolset"] == same["beside_toolset"]
    assert store.revision_reading(target_id(world, "io.example/old")) is None      # read by handshake already
    # The log entry is the 2026-07-28 reading; the second one added no observation.
    dual_target = next(t for t in store.all_targets() if t["name"] == "io.example/dual")
    assert dual_target["current_toolset"] == MODERN_DIGEST and dual_target["ok_observations"] == 1


def test_check_recognises_what_a_handshake_client_was_listed(dual):
    client, world = dual
    endpoint = make_target("io.example/dual", "/dual").endpoint
    modern = check(client, endpoint=endpoint, tools=MODERN_TOOLS).json()
    assert modern["match"] == "same"
    assert modern["otherRevision"]["toolSetDigest"] == HANDSHAKE_DIGEST       # shown either way: there are two lists
    old = check(client, endpoint=endpoint, tools=HANDSHAKE_TOOLS).json()
    assert old["type"] == CHECK_TYPE and old["match"] == "same-other-revision"
    assert verify_document(old, world["services"].key.did, CHECK_TYPE)
    assert old["observed"]["toolSetDigest"] == MODERN_DIGEST
    assert old["otherRevision"] == {"protocolVersion": "2025-06-18", "toolSetDigest": HANDSHAKE_DIGEST, "toolCount": 1,
                                    "readAt": old["otherRevision"]["readAt"], "besideToolSetDigest": MODERN_DIGEST}
    assert "initialize handshake (protocol 2025-06-18" in old["note"] and "beside the log, not in it" in old["note"]
    assert "{" not in old["note"]
    # By digest alone, as WARDEN asks.
    assert check(client, endpoint=endpoint, toolSetDigest=HANDSHAKE_DIGEST).json()["match"] == "same-other-revision"
    # Anything else is still different, and a server that lists the same both ways shows no second list.
    other = check(client, endpoint=endpoint, tools=[tool("get_price", "Something else.")]).json()
    assert other["match"] == "different" and "difference of revision" not in other["note"]
    both = check(client, endpoint=make_target("io.example/both", "/both").endpoint, tools=PLAIN).json()
    assert both["match"] == "same" and "otherRevision" not in both


def test_a_second_reading_is_taken_again_only_when_due(dual):
    _, world = dual
    mcp, crawler, clock = world["mcp"], world["services"].crawler, world["clock"]

    def handshakes():
        return sum(1 for path, method in mcp.calls if method == "initialize" and path in ("/dual", "/both"))

    assert handshakes() == 2
    clock.advance(days=1)
    crawler.run()
    assert handshakes() == 2                    # same list, reading not a week old: no second read
    mcp.modern["/dual"] = [tool("get_price", "Return the price, now in euros.")]
    clock.advance(days=1)
    crawler.run()
    assert handshakes() == 3                    # the 2026-07-28 list changed: read the handshake list again
    row = world["services"].store.revision_reading(target_id(world, "io.example/dual"))
    assert row["beside_toolset"] == tool_set_digest(mcp.modern["/dual"])[0] and row["toolset"] == HANDSHAKE_DIGEST
    clock.advance(days=8)
    crawler.run()
    assert handshakes() == 5                    # both are older than revision_reread_days now


def test_the_daily_limit_and_the_off_switch(world):
    from dataclasses import replace

    mcp, crawler = world["mcp"], world["services"].crawler
    for i in range(3):
        mcp.modern[f"/m{i}"], mcp.tools[f"/m{i}"] = MODERN_TOOLS, HANDSHAKE_TOOLS
        world["targets"].append(make_target(f"io.example/m{i}", f"/m{i}"))
    crawler.settings = replace(crawler.settings, revision_reads_per_day=2)
    crawler.run()
    assert sum(1 for _, method in mcp.calls if method == "initialize") == 2
    assert len(world["services"].store.revision_reading_index()) == 2
    world["clock"].advance(days=1)              # a new day: the third one gets its reading
    crawler.run()
    assert len(world["services"].store.revision_reading_index()) == 3
    crawler.settings = replace(crawler.settings, revision_reads_per_day=0)
    mcp.modern["/m0"] = PLAIN
    world["clock"].advance(days=1)
    before = len(mcp.calls)
    crawler.run()
    assert not [m for _, m in mcp.calls[before:] if m == "initialize"]


def test_a_refused_handshake_is_recorded_and_check_says_what_it_lacks(world):
    mcp = world["mcp"]
    mcp.modern["/only"], mcp.tools["/only"] = MODERN_TOOLS, []
    mcp.no_handshake.add("/only")
    world["targets"].append(make_target("io.example/only", "/only"))
    world["services"].crawler.run()
    row = world["services"].store.revision_reading(target_id(world, "io.example/only"))
    assert row["status"] != "ok" and row["toolset"] is None and row["beside_toolset"] == MODERN_DIGEST
    with TestClient(create_app(world["services"])) as client:
        answer = check(client, endpoint=make_target("io.example/only", "/only").endpoint, tools=HANDSHAKE_TOOLS).json()
    assert answer["match"] == "different" and "otherRevision" not in answer
    assert "has no reading of what it lists after an initialize handshake" in answer["note"]
