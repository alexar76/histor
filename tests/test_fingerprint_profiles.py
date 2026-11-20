"""Tool fingerprints as the Vercel AI SDK computes them, from HISTOR's record (histor.fingerprint_profiles):
checked against digests the library itself produced, and served as a baseline by /api/v1/fingerprints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from histor.app import create_app
from histor.fingerprint_profiles import CHECKED_AGAINST, ai_sdk_fingerprint, ai_sdk_fingerprints, from_record
from tests.conftest import make_target, tool

VECTORS = Path(__file__).parent / "vectors" / "ai-sdk-fingerprint"


def test_the_digests_are_the_ones_the_library_produced():
    tools = json.loads((VECTORS / "tools.json").read_text(encoding="utf-8"))
    made = json.loads((VECTORS / "vectors.json").read_text(encoding="utf-8"))
    assert made["versions"]["ai"] == CHECKED_AGAINST["ai"] and made["versions"]["@ai-sdk/mcp"] == CHECKED_AGAINST["@ai-sdk/mcp"]
    assert ai_sdk_fingerprints(tools) == made["fingerprints"] and len(made["fingerprints"]) == 16
    # The same digest as the run of 2026-11-19 on a one-tool server (the article's example).
    assert made["fingerprints"]["plain"] == "4VyxOrn4OpyUV4G8Ixs30JkSqacwAZGm5nEbaMmYOoQ"


def test_what_moves_the_digest_and_what_does_not():
    base = {"name": "list_orders", "description": "List the orders of the signed-in account.",
            "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False}}
    digest = ai_sdk_fingerprint(base)
    assert digest == "4VyxOrn4OpyUV4G8Ixs30JkSqacwAZGm5nEbaMmYOoQ"
    # Not covered: the hints, an output schema, _meta, the top-level additionalProperties.
    assert ai_sdk_fingerprint({**base, "annotations": {"readOnlyHint": False, "destructiveHint": True}}) == digest
    assert ai_sdk_fingerprint({**base, "outputSchema": {"type": "object"}, "_meta": {"x": 1}}) == digest
    assert ai_sdk_fingerprint({**base, "inputSchema": {**base["inputSchema"], "additionalProperties": True}}) == digest
    # Covered: the description, the schema, the title (also when it comes from annotations).
    edited = ai_sdk_fingerprint({**base, "description": base["description"] + " Always pass limit=1000."})
    assert edited == "t4wnBXNFwj6Ez6x-JN7gDWZHdo0nGmN_sKStSRILIpM"
    assert ai_sdk_fingerprint({**base, "inputSchema": {"type": "object", "properties": {"limit": {"type": "string"}}}}) != digest
    assert ai_sdk_fingerprint({**base, "annotations": {"title": "Orders"}}) == ai_sdk_fingerprint({**base, "title": "Orders"}) != digest


def test_from_histors_record():
    entries = [{"name": "a", "description": "d", "inputSchema": {"type": "object"}},
               {"name": "b", "description": "", "inputSchema": {}},
               {"name": "c", "description": "d", "inputSchema": {}}]
    extras = {"tools": {"a": {"title": "A", "annotations": {"readOnlyHint": True}}}}
    answer = from_record(entries, extras)
    assert answer["tools"]["a"] == ai_sdk_fingerprint({"name": "a", "description": "d", "inputSchema": {"type": "object"}, "title": "A"})
    # HISTOR keeps "no description" as "": both readings are given.
    assert answer["ifDescriptionAbsent"] == {"b": ai_sdk_fingerprint({"name": "b", "inputSchema": {}})}
    assert answer["tools"]["b"] == ai_sdk_fingerprint({"name": "b", "description": "", "inputSchema": {}}) != answer["ifDescriptionAbsent"]["b"]
    assert "notComputed" not in answer
    # A tool whose other members were not kept whole has no title to hash: it is not guessed.
    assert from_record(entries, {"tools": {}, "oversized": ["c"]})["notComputed"] == ["c"]
    assert from_record(entries, None) == {"tools": {}, "notComputed": ["a", "b", "c"]}


def test_the_baseline_is_served_for_a_recorded_server(world):
    served = [{**tool("list_orders", "List the orders of the signed-in account.", limit="integer"),
               "annotations": {"readOnlyHint": True, "destructiveHint": False}},
              {**tool("cancel", "Cancel an order."), "title": "Cancel order"}]
    world["mcp"].tools["/orders"] = served
    t = make_target("com.example/orders", "/orders")
    world["targets"].append(t)
    world["services"].crawler.run()
    with TestClient(create_app(world["services"])) as client:
        answer = client.get("/api/v1/fingerprints", params={"endpoint": t.endpoint}).json()
        assert answer["type"] == "histor.fingerprints/v1" and answer["profile"] == "ai-sdk"
        assert answer["target"]["id"] == t.id and answer["observed"]["toolCount"] == 2
        assert answer["tools"] == ai_sdk_fingerprints(served)
        assert answer["tools"]["list_orders"] == "4VyxOrn4OpyUV4G8Ixs30JkSqacwAZGm5nEbaMmYOoQ"
        assert client.get("/api/v1/fingerprints", params={"id": t.id}).json()["tools"] == answer["tools"]
        assert client.get("/api/v1/fingerprints", params={"name": "com.example/orders"}).json()["tools"] == answer["tools"]

        # The server flips its hints: the profile's digests stay, HISTOR's extras digest moves.
        world["mcp"].tools["/orders"] = [{**served[0], "annotations": {"readOnlyHint": False, "destructiveHint": True}}, served[1]]
        world["clock"].advance(days=1)
        world["services"].crawler.run()
        after = client.get("/api/v1/fingerprints", params={"endpoint": t.endpoint}).json()
        assert after["tools"] == answer["tools"]
        assert after["observed"]["toolSetDigest"] == answer["observed"]["toolSetDigest"]
        assert after["observed"]["toolExtrasDigest"] != answer["observed"]["toolExtrasDigest"]

        assert client.get("/api/v1/fingerprints", params={"endpoint": "https://unknown.example/mcp"}).status_code == 404
        assert client.get("/api/v1/fingerprints", params={"endpoint": t.endpoint, "profile": "other"}).status_code == 400
        assert client.get("/api/v1/fingerprints").status_code == 400
