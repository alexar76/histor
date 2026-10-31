"""Per-tool fingerprints in the Vercel AI SDK, heldfast and AgentAvow formats (histor.fingerprints),
against values produced by those projects' own implementations, and the API that serves them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from histor import fingerprints
from tests.conftest import make_target, tool

VECTORS = json.loads((Path(__file__).parent / "fingerprint-vectors.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=[c["tool"]["name"] for c in VECTORS])
def test_each_format_matches_the_reference_implementation(case):
    t = case["tool"]
    assert fingerprints.vercel_ai(t) == case["vercel-ai"]
    assert fingerprints.heldfast(t) == case["heldfast"]
    assert fingerprints.agentavow_v1(t) == case["agentavow-v1"]
    assert fingerprints.agentavow_key(t["name"]) == case["agentavowKey"]


def test_deepwiki_reproduces_what_agentavow_signed():
    signed = [c for c in VECTORS if "signedAgentavow" in c]
    assert len(signed) == 3
    assert all(fingerprints.agentavow_v1(c["tool"]) == c["signedAgentavow"] for c in signed)


@pytest.mark.parametrize("value, text", [
    (0.0, "0"), (-0.0, "0"), (1.0, "1"), (1e21, "1e+21"), (1e20, "100000000000000000000"),
    (1e-7, "1e-7"), (1e-6, "0.000001"), (0.1, "0.1"), (-2.5e-8, "-2.5e-8"), (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"), (9007199254740993, "9007199254740992"),
    (2**60, "1152921504606847000"), (123, "123"),
])
def test_numbers_are_written_as_ecmascript_writes_them(value, text):
    assert fingerprints.canonical(value) == text


def test_keys_sort_by_utf16_code_units_and_strings_escape_like_json_stringify():
    assert fingerprints.canonical({"": 1, "😀": 2, "10": 3, "2": 4}) == '{"10":3,"2":4,"😀":2,"":1}'
    assert fingerprints.canonical("a\"\\\n\x01 \ud800") == '"a\\"\\\\\\n\\u0001 \\ud800"'


def test_key_encoding_cuts_long_names_and_escapes_the_rest():
    assert fingerprints.agentavow_key("a=b") == "tool:a%3Db"
    assert fingerprints.agentavow_key("read file") == "tool:read%20file"
    assert fingerprints.agentavow_key("x" * 128) == "tool:" + "x" * 128
    cut = fingerprints.agentavow_key("x" * 129)
    assert cut.startswith("tool:" + "x" * 96 + "~") and len(cut) == len("tool:") + 96 + 1 + 16


def test_what_each_format_ignores():
    base = tool("t", "Same.")
    with_output = {**base, "outputSchema": {"type": "object"}}
    with_hint = {**base, "annotations": {"readOnlyHint": True}}
    loose = {**base, "inputSchema": {**base["inputSchema"], "additionalProperties": True}}
    # The Vercel fingerprint does not cover the output contract, annotation hints or the forced member.
    assert fingerprints.vercel_ai(base) == fingerprints.vercel_ai(with_output) == fingerprints.vercel_ai(with_hint) \
        == fingerprints.vercel_ai(loose)
    for fmt in (fingerprints.heldfast, fingerprints.agentavow_v1):
        assert len({fmt(base), fmt(with_output), fmt(with_hint), fmt(loose)}) == 4
    # heldfast writes an empty output schema as absent; AgentAvow keeps it.
    assert fingerprints.heldfast({**base, "outputSchema": {}}) == fingerprints.heldfast(base)
    assert fingerprints.agentavow_v1({**base, "outputSchema": {}}) != fingerprints.agentavow_v1(base)


def test_the_api_serves_the_current_tool_set_in_each_format(world):
    from fastapi.testclient import TestClient

    from histor.app import create_app

    s, mcp = world["services"], world["mcp"]
    served = [{**tool("read_file", "Read a file."), "annotations": {"title": "Read", "readOnlyHint": True}},
              tool("write_file", "Write a file.")]
    mcp.tools["/fp"] = served
    target = make_target("io.example/fp", "/fp")
    world["targets"].append(target)
    s.crawler.run()

    with TestClient(create_app(s)) as client:
        page = client.get(f"/api/v1/servers/{target.id}/fingerprints").json()
        assert page["format"] == "vercel-ai" and page["extrasRecorded"] and page["incomplete"] == []
        assert page["fingerprints"] == {t["name"]: fingerprints.vercel_ai(t) for t in served}
        held = client.get(f"/api/v1/servers/{target.id}/fingerprints?format=heldfast").json()
        assert held["fingerprints"] == {t["name"]: fingerprints.heldfast(t) for t in served}
        avow = client.get(f"/api/v1/servers/{target.id}/fingerprints?format=agentavow-v1").json()
        assert set(avow["fingerprints"]) == {"tool:read_file", "tool:write_file"}
        assert client.get(f"/api/v1/servers/{target.id}/fingerprints?format=md5").status_code == 400
        assert client.get("/api/v1/servers/0000000000000000/fingerprints").status_code == 404


def test_tools_whose_extras_were_not_stored_get_no_fingerprint():
    tools = [tool("a"), tool("b")]
    merged, incomplete = fingerprints.merged_tools(tools, {"tools": {"a": {"title": "A"}}, "oversized": {"b": 99999}})
    assert merged[0]["title"] == "A" and incomplete == ["b"]
