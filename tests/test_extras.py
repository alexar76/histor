"""Tool members beyond the four MTL/1 digests (histor.extras): kept beside the tool set, under a digest
of their own, with their changes recorded — and the tool-set digest and the label chain untouched."""

from __future__ import annotations

import json

from histor.extras import MAX_TOOL_BYTES, extras_diff, tool_extras
from histor.subject import tool_set_digest
from tests.conftest import make_target, tool


def annotated(name: str, description: str = "", **extra):
    return {**tool(name, description), **extra}


def test_only_members_outside_the_digest_are_kept():
    tools = [annotated("read", "Read a file.", annotations={"readOnlyHint": True}, _meta={"price": 0.01},
                       title="Read file"),
             tool("plain", "Nothing extra.")]
    digest, body = tool_extras(tools)
    assert digest and digest.startswith("sha256-")
    assert body == {"tools": {"read": {"annotations": {"readOnlyHint": True}, "_meta": {"price": 0.01},
                                       "title": "Read file"}}}


def test_no_extras_is_none_and_the_digest_ignores_order():
    assert tool_extras([tool("a"), tool("b")]) == (None, None)
    one = [annotated("a", annotations={"x": 1}), annotated("b", icons=[{"src": "i"}])]
    assert tool_extras(one)[0] == tool_extras(list(reversed(one)))[0]


def test_hostile_values_are_cleaned_and_capped():
    big = "x" * (MAX_TOOL_BYTES + 10)
    _, body = tool_extras([annotated("nul", _meta={"k\x00": "v\ud800"}), annotated("huge", _meta={"blob": big})])
    assert body["tools"]["nul"]["_meta"] == {"k�": "v�"}
    assert "huge" not in body["tools"] and body["oversized"]["huge"] > MAX_TOOL_BYTES


def test_the_tool_set_digest_does_not_see_extras():
    bare = [tool("delete", "Delete a file.")]
    hinted = [annotated("delete", "Delete a file.", annotations={"readOnlyHint": True})]
    assert tool_set_digest(bare)[0] == tool_set_digest(hinted)[0]


def test_diff_spells_out_annotation_flips_and_flags_escalations():
    old = tool_extras([annotated("rm", annotations={"readOnlyHint": True}, _meta={"v": 1})])[1]
    new = tool_extras([annotated("rm", annotations={"readOnlyHint": False, "destructiveHint": True}, _meta={"v": 2}),
                       annotated("new", title="New")])[1]
    d = extras_diff(old, new)
    assert d["tools"]["rm"]["annotations"] == {"destructiveHint": [None, True], "readOnlyHint": [True, False]}
    assert d["tools"]["rm"]["members"] == {"_meta": "changed"}
    assert d["tools"]["new"]["members"] == {"title": "added"}
    assert d["escalations"] == ["rm"] and d["changedTools"] == 2


def _extras_rows(s, target_id):
    with s.store.db.read() as tx:
        target = tx.one("SELECT current_extras, current_toolset, changes FROM targets WHERE id=?", (target_id,))
        changes = tx.execute("SELECT * FROM extras_changes WHERE target_id=? ORDER BY seq", (target_id,))
        observed = [r["extras"] for r in tx.execute(
            "SELECT extras FROM observations WHERE target_id=? ORDER BY observed_at", (target_id,))]
    return target, changes, observed


def test_the_crawler_keeps_extras_and_records_a_flip_without_a_tool_set_change(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/x"] = [annotated("delete_file", "Delete a file.", annotations={"readOnlyHint": True})]
    target = make_target("io.example/files", "/x")
    world["targets"].append(target)
    s.crawler.run()
    first, changes, observed = _extras_rows(s, target.id)
    assert first["current_extras"] and changes == [] and observed == [first["current_extras"]]
    assert json.loads(s.store.blob_text(first["current_extras"], "toolextras"))["tools"]["delete_file"] == {
        "annotations": {"readOnlyHint": True}}

    clock.advance(days=1)
    mcp.tools["/x"] = [annotated("delete_file", "Delete a file.", annotations={"readOnlyHint": False})]
    s.crawler.run()
    second, changes, _ = _extras_rows(s, target.id)
    # Same tool set: no MTL/1 change, no new digest — the flip is recorded beside it.
    assert second["current_toolset"] == first["current_toolset"] and second["changes"] == 0
    assert len(changes) == 1 and changes[0]["from_extras"] == first["current_extras"]
    summary = json.loads(changes[0]["summary"])
    assert summary["escalations"] == ["delete_file"]
    assert summary["tools"]["delete_file"]["annotations"] == {"readOnlyHint": [True, False]}


def test_a_target_first_seen_by_this_code_is_a_baseline_and_losing_extras_is_a_change(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/y"] = [tool("plain", "No extras.")]
    target = make_target("io.example/plain", "/y")
    world["targets"].append(target)
    s.crawler.run()
    row, changes, observed = _extras_rows(s, target.id)
    assert row["current_extras"] == "" and changes == [] and observed == [""]

    clock.advance(days=1)
    mcp.tools["/y"] = [annotated("plain", "No extras.", _meta={"x402": {"price": "0.01"}})]
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/y"] = [tool("plain", "No extras.")]
    s.crawler.run()
    row, changes, _ = _extras_rows(s, target.id)
    assert [(c["from_extras"] is None, c["to_extras"] is None) for c in changes] == [(True, False), (False, True)]
    assert row["current_extras"] == ""


def test_the_api_and_the_dataset_carry_the_extras(world, tmp_path):
    import gzip

    from fastapi.testclient import TestClient

    from histor import dataset
    from histor.app import create_app

    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/z"] = [annotated("pay", "Pay a bill.", annotations={"destructiveHint": False}, _meta={"price": 0.25})]
    target = make_target("io.example/pay", "/z")
    world["targets"].append(target)
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/z"] = [annotated("pay", "Pay a bill.", annotations={"destructiveHint": True}, _meta={"price": 0.25})]
    s.crawler.run()

    with TestClient(create_app(s)) as client:
        page = client.get(f"/api/v1/servers/{target.id}").json()
        digest = page["server"]["toolExtrasDigest"]
        assert page["toolExtras"]["tools"]["pay"]["annotations"] == {"destructiveHint": True}
        assert page["extrasChanges"][0]["summary"]["escalations"] == ["pay"]
        blob = client.get(f"/api/v1/tool-extras/{digest}")
        assert blob.status_code == 200 and blob.json()["tools"]["pay"]["_meta"] == {"price": 0.25}

    manifest = dataset.export(s.store, s.key, tmp_path / "snap", now="2026-10-23T15:00:00Z")
    assert manifest["files"]["tool_extras.jsonl.gz"]["rows"] == 2
    assert manifest["files"]["extras_changes.jsonl.gz"]["rows"] == 1
    rows = [json.loads(line) for line in gzip.open(tmp_path / "snap" / "servers.jsonl.gz", "rt")]
    assert [r["toolExtrasDigest"] for r in rows if r["id"] == target.id] == [digest]
    card = (tmp_path / "snap" / "README.md").read_text()
    assert "config_name: tool_extras" in card and "**tool_extras**" in card
