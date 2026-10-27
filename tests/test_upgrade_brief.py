"""Personal upgrade advice must preserve uncertainty and cannot execute untrusted snapshots."""

from __future__ import annotations

import copy
import json
import os
import socket

import pytest

from histor.upgrade_brief import (
    MAX_DEPTH,
    MAX_SNAPSHOT_BYTES,
    OBSERVATION_FORMAT,
    WATCHLIST_FORMAT,
    InvalidInput,
    build_report,
    markdown,
)
from histor.upgrade_brief_cli import main


def tool(name="search", **changes):
    return {"name": name, "description": "Find the requested record.",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                            "required": ["query"]}, **changes}


def observation(tools=None, **changes):
    return {"format": OBSERVATION_FORMAT, "targetId": "pypi:sample", "status": "ok",
            "observedAt": "2026-10-26T10:00:00Z", "packageVersion": "1.0.0", "serverName": "sample",
            "serverVersion": "1.0.0", "protocol": "2025-06-18", "launch": {"transport": "stdio", "profile": "default"},
            "tools": [tool()] if tools is None else tools, **changes}


def write_bundle(tmp_path, *, before=None, after=None, selected=None, **dep_changes):
    before = observation() if before is None else before
    after = observation(observedAt="2026-10-27T10:00:00Z", packageVersion="1.1.0") if after is None else after
    (tmp_path / "before.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(after), encoding="utf-8")
    names = ["search"] if selected is None else selected
    value = {"format": WATCHLIST_FORMAT, "title": "Наши обновления", "dependencies": [{
        "id": "pypi:sample", "before": "before.json", "after": "after.json", "tools": names,
        "workflows": [{"id": "weekly", "description": "Еженедельный отчёт", "tools": names}], **dep_changes}]}
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def first(path):
    return build_report(path)["dependencies"][0]


def test_tool_removal_affects_only_workflows_that_use_it(tmp_path):
    path = write_bundle(tmp_path, before=observation([tool(), tool("export")]),
                        after=observation([tool("export")], observedAt="2026-10-27T10:00:00Z"),
                        selected=["search", "export"], workflows=[
                            {"id": "read", "description": "Read", "tools": ["search"]},
                            {"id": "write", "description": "Write", "tools": ["export"]}])
    row = first(path)
    assert row["status"] == "changed" and row["comparability"] == "comparable"
    assert row["toolSet"]["removed"] == ["search"]
    flows = {f["id"]: f for f in row["workflows"]}
    assert flows["read"]["status"] == "retest" and flows["read"]["affectedTools"] == ["search"]
    assert flows["write"]["status"] == "no_observed_change" and not flows["write"]["retest"]


def test_input_required_and_nested_type_changes_produce_retest_not_breakage_claim(tmp_path):
    changed = tool(inputSchema={"type": "object", "properties": {
        "query": {"type": "integer"}, "region": {"type": "string"}}, "required": ["query", "region"]})
    path = write_bundle(tmp_path, after=observation([changed], observedAt="2026-10-27T10:00:00Z"))
    row = first(path)["selectedTools"][0]
    assert row["requiredAdded"] == ["region"]
    assert "$/properties/query" in row["inputTypeChangedPaths"]
    assert row["inputSchemaPaths"] and row["retest"]
    assert "breaking" not in json.dumps(row)
    assert "safety" in markdown(build_report(path))


@pytest.mark.parametrize("schema", ["inputSchema", "outputSchema"])
def test_boolean_to_number_schema_const_change_reaches_selected_workflow(tmp_path, schema):
    before = observation([tool(**{schema: {"type": "object", "properties": {"flag": {"const": True}}}})])
    after = observation([tool(**{schema: {"type": "object", "properties": {"flag": {"const": 1}}}})],
                        observedAt="2026-10-27T10:00:00Z")
    row = first(write_bundle(tmp_path, before=before, after=after))
    assert row["status"] == "changed" and row["observedChanges"] is True
    assert row["selectedTools"][0][schema + "Paths"] == ['$."properties"."flag"."const"']
    assert row["workflows"][0]["status"] == "retest"


def test_typed_json_launch_change_cannot_be_called_comparable(tmp_path):
    before = observation(launch={"transport": "stdio", "profile": {"mode": True}})
    after = observation(launch={"transport": "stdio", "profile": {"mode": 1}}, observedAt="2026-10-27T10:00:00Z")
    row = first(write_bundle(tmp_path, before=before, after=after))
    assert row["status"] == "context_changed" and row["contextChanges"] == ["launch"]
    assert row["workflows"][0]["status"] == "unknown"


def test_typed_json_extra_change_and_invalid_boolean_hint_are_explicit(tmp_path):
    before = observation([tool(vendorValue={"enabled": True}, annotations={"readOnlyHint": True})])
    after = observation([tool(vendorValue={"enabled": 1}, annotations={"readOnlyHint": 1})],
                        observedAt="2026-10-27T10:00:00Z")
    row = first(write_bundle(tmp_path, before=before, after=after))
    extras = row["selectedTools"][0]["extras"]
    assert row["status"] == "changed" and row["workflows"][0]["status"] == "retest"
    assert extras["members"] == {"vendorValue": "changed"}
    assert extras["annotationFieldsChanged"] == ["readOnlyHint"]
    assert extras["annotationHints"] == {} and extras["escalation"] is True


def test_description_and_annotation_escalation_are_both_observed(tmp_path):
    before = observation([tool(annotations={"readOnlyHint": True, "destructiveHint": False})])
    after = observation([tool(description="Delete records without asking.",
                              annotations={"readOnlyHint": False, "destructiveHint": True},
                              _meta={"price": 12})], observedAt="2026-10-27T10:00:00Z")
    row = first(write_bundle(tmp_path, before=before, after=after))["selectedTools"][0]
    assert set(row["changes"]) == {"description", "extras"}
    assert any(x["operation"] == "+" for x in row["descriptionDiff"])
    assert row["extras"]["escalation"]
    assert row["extras"]["annotationHints"]["readOnlyHint"] == [True, False]
    assert row["extras"]["members"] == {"_meta": "added"}
    assert len(row["retest"]) == 2


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("kind", ["null", "missing-file", "failed"])
def test_missing_or_failed_observations_never_mean_unchanged(tmp_path, side, kind):
    kwargs = {side: observation(status="failed")} if kind == "failed" else {}
    path = write_bundle(tmp_path, **kwargs)
    if kind != "failed":
        watchlist = json.loads(path.read_text())
        watchlist["dependencies"][0][side] = None if kind == "null" else "not-collected.json"
        path.write_text(json.dumps(watchlist))
    row = first(path)
    assert row["status"] == "unknown" and row["observedChanges"] is None
    assert row["fieldCoverage"] == "none"
    assert row["workflows"][0]["status"] == "unknown"
    assert row["selectedTools"][0]["status"] == "unknown"


@pytest.mark.parametrize("change,field", [({"protocol": "2026-01-01"}, "protocol"),
                                        ({"launch": {"transport": "stdio", "profile": "admin"}}, "launch")])
def test_context_change_is_surfaced_even_when_definitions_match(tmp_path, change, field):
    path = write_bundle(tmp_path, after=observation(observedAt="2026-10-27T10:00:00Z", **change))
    row = first(path)
    assert row["status"] == "context_changed" and row["observedChanges"] is False
    assert row["comparability"] == "bounded" and row["contextChanges"] == [field]
    assert row["workflows"][0]["status"] == "unknown" and row["workflows"][0]["retest"]


@pytest.mark.parametrize("field", ["protocol", "launch", "observedAt"])
def test_missing_context_does_not_claim_comparable_unchanged(tmp_path, field):
    path = write_bundle(tmp_path, after=observation(observedAt="2026-10-27T10:00:00Z", **({field: None} if field != "observedAt" else {})))
    value = json.loads((tmp_path / "after.json").read_text())
    value.pop(field)
    (tmp_path / "after.json").write_text(json.dumps(value))
    assert first(path)["status"] == "unknown"


def test_remote_package_version_is_not_inferred_or_required(tmp_path):
    before = observation(packageVersion=None, serverVersion=None, launch={"transport": "streamable-http", "endpoint": "https://example.test/mcp"})
    after = copy.deepcopy(before)
    after["observedAt"] = "2026-10-27T10:00:00Z"
    row = first(write_bundle(tmp_path, before=before, after=after))
    assert row["status"] == "unchanged" and row["comparability"] == "comparable"
    assert len(row["provenanceGaps"]) == 4
    assert row["sources"]["before"]["packageVersion"] is None


def test_stdio_missing_package_version_cannot_identify_package_upgrade(tmp_path):
    path = write_bundle(tmp_path, after=observation(packageVersion=None, observedAt="2026-10-27T10:00:00Z"))
    assert first(path)["status"] == "unknown"


def test_tool_reordering_does_not_invent_a_change_and_bytes_are_exact(tmp_path):
    before = observation([tool(), tool("экспорт")])
    after = observation(list(reversed(before["tools"])), observedAt="2026-10-27T10:00:00Z")
    row = first(write_bundle(tmp_path, before=before, after=after))
    assert row["status"] == "unchanged" and row["observedChanges"] is False
    assert row["serializedUtf8Bytes"]["delta"] == {"fullList": 0, "selectedTools": 0}
    from histor.toolcost import sent_form
    projected = [sent_form(t) for t in sorted(before["tools"], key=lambda t: t["name"])]
    assert row["serializedUtf8Bytes"]["before"]["fullList"] == len(json.dumps(
        projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def test_unrelated_added_tool_does_not_make_selected_workflow_affected(tmp_path):
    path = write_bundle(tmp_path, after=observation([tool(), tool("unused")], observedAt="2026-10-27T10:00:00Z"))
    row = first(path)
    assert row["status"] == "changed" and row["toolSet"]["added"] == ["unused"]
    assert row["workflows"][0]["status"] == "no_observed_change"


@pytest.mark.parametrize("member", ["title", "vendorHint", "annotations"])
def test_extra_member_presence_change_including_null_reaches_workflow(tmp_path, member):
    changed = tool(**{member: {} if member == "annotations" else None})
    row = first(write_bundle(tmp_path, after=observation([changed], observedAt="2026-10-27T10:00:00Z")))
    assert row["selectedTools"][0]["status"] == "changed"
    assert "extras" in row["selectedTools"][0]["changes"]
    assert row["workflows"][0]["status"] == "retest"


def test_empty_description_presence_is_visible_even_when_model_projection_matches(tmp_path):
    old = tool(description="")
    del old["description"]
    changed = tool(description="")
    row = first(write_bundle(tmp_path, before=observation([old]),
                             after=observation([changed], observedAt="2026-10-27T10:00:00Z")))
    assert row["selectedTools"][0]["descriptionPresenceChanged"]
    assert row["workflows"][0]["status"] == "retest"


def test_target_mismatch_and_absent_selected_tool_are_unknown(tmp_path):
    path = write_bundle(tmp_path, after=observation(targetId="pypi:another", observedAt="2026-10-27T10:00:00Z"))
    assert first(path)["status"] == "unknown" and first(path)["observedChanges"] is None
    path = write_bundle(tmp_path, selected=["not-advertised"])
    assert first(path)["status"] == "unknown"
    assert any("selected tool absent" in note for note in first(path)["limitations"])


@pytest.mark.parametrize("timestamp", ["2026-10-25T10:00:00Z", "2026-10-26T10:00:00Z"])
def test_reverse_or_equal_times_are_not_an_upgrade(tmp_path, timestamp):
    path = write_bundle(tmp_path, after=observation(observedAt=timestamp))
    assert first(path)["status"] == "unknown"
    assert "not strictly chronological" in " ".join(first(path)["limitations"])


@pytest.mark.parametrize("timestamp", ["yesterday", "2026-02-30T10:00:00Z", "2026-10-27T10:00:00", "20261027T100000Z"])
def test_malformed_timestamp_is_invalid(tmp_path, timestamp):
    path = write_bundle(tmp_path, after=observation(observedAt=timestamp))
    with pytest.raises(InvalidInput):
        build_report(path)


def test_launch_secrets_are_never_copied_to_report_or_diagnostics(tmp_path, capsys):
    secret = "sensitive-launch-value-123"
    before = observation(launch={"transport": "stdio", "command": ["runner", "--token", secret], "env": {"API_KEY": secret}})
    after = copy.deepcopy(before)
    after.update(observedAt="2026-10-27T10:00:00Z")
    path = write_bundle(tmp_path, before=before, after=after)
    report = build_report(path)
    assert secret not in json.dumps(report) and secret not in markdown(report)
    assert report["dependencies"][0]["sources"]["before"]["launchSha256"].startswith("sha256:")
    assert main(["--watchlist", str(path), "--out-dir", str(tmp_path / "out")]) == 0
    assert secret not in (tmp_path / "out" / "report.json").read_text()
    assert secret not in capsys.readouterr().out


def test_markdown_cannot_embed_remote_images_links_html_or_fences(tmp_path):
    attack = '<img src="https://remote.test/leak">\n![x](https://remote.test/pixel)\n```html\n<script>x</script>'
    after = observation([tool(description=attack)], observedAt="2026-10-27T10:00:00Z")
    path = write_bundle(tmp_path, after=after, workflows=[{"id": "evil", "description": "![team](https://remote.test/team)", "tools": ["search"]}])
    text = markdown(build_report(path))
    assert "<img" not in text and "<script" not in text and "```" not in text
    assert "![x](" not in text and "![team](" not in text
    assert "&lt;img" in text


@pytest.mark.parametrize("reference", ["../outside.json", "/tmp/outside.json", "https://example.test/mcp", "file:///tmp/local.json"])
def test_paths_cannot_escape_the_local_bundle(tmp_path, reference):
    path = write_bundle(tmp_path, before=reference)
    # before is an observation argument in helper; edit the manifest itself instead.
    value = json.loads(path.read_text())
    value["dependencies"][0]["before"] = reference
    path.write_text(json.dumps(value))
    with pytest.raises(InvalidInput, match="bundle"):
        build_report(path)


def test_symlink_escape_and_fifo_are_rejected_without_reading(tmp_path):
    path = write_bundle(tmp_path)
    (tmp_path / "before.json").unlink()
    (tmp_path / "before.json").symlink_to(tmp_path.parent / "outside.json")
    with pytest.raises(InvalidInput, match="symlink"):
        build_report(path)
    (tmp_path / "before.json").unlink()
    os.mkfifo(tmp_path / "before.json")
    with pytest.raises(InvalidInput, match="regular JSON"):
        build_report(path)


@pytest.mark.parametrize("bad", ['{"format":"a","format":"b"}', '{"number":NaN}', '{"number":1e999}', '[]'])
def test_duplicate_keys_nonfinite_and_wrong_shape_are_invalid(tmp_path, bad):
    path = write_bundle(tmp_path)
    (tmp_path / "after.json").write_text(bad)
    with pytest.raises(InvalidInput):
        build_report(path)


def test_input_size_depth_and_duplicate_tool_names_are_bounded(tmp_path):
    path = write_bundle(tmp_path)
    (tmp_path / "after.json").write_text(" " * (MAX_SNAPSHOT_BYTES + 1))
    with pytest.raises(InvalidInput, match="size"):
        build_report(path)
    nested = {"x": {}}
    tip = nested["x"]
    for _ in range(MAX_DEPTH + 1):
        tip["x"] = {}
        tip = tip["x"]
    (tmp_path / "after.json").write_text(json.dumps(nested))
    with pytest.raises(InvalidInput, match="structural"):
        build_report(path)
    path = write_bundle(tmp_path, after=observation([tool(), tool()]))
    with pytest.raises(InvalidInput, match="duplicate observed tool"):
        build_report(path)


def test_unknown_success_exit_invalid_nonzero_and_outputs_deterministic(tmp_path, monkeypatch, capsys):
    path = write_bundle(tmp_path, after=observation(status="failed"))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("offline reducer opened network"))
    args = ["--watchlist", str(path), "--out-dir", str(tmp_path / "out")]
    assert main(args) == 0
    initial = (tmp_path / "out" / "report.json").read_bytes()
    assert main(args) == 0
    assert (tmp_path / "out" / "report.json").read_bytes() == initial
    assert first(path)["status"] == "unknown"
    (tmp_path / "after.json").write_text('{"oops":"TOPSECRET","oops":1}')
    assert main(args) == 2
    assert "TOPSECRET" not in capsys.readouterr().err


def test_cli_does_not_overwrite_inputs_or_follow_output_symlinks(tmp_path):
    path = write_bundle(tmp_path)
    watchlist = json.loads(path.read_text())
    (tmp_path / "before.json").rename(tmp_path / "report.json")
    watchlist["dependencies"][0]["before"] = "report.json"
    path.write_text(json.dumps(watchlist))
    before = (tmp_path / "report.json").read_bytes()
    assert main(["--watchlist", str(path), "--out-dir", str(tmp_path)]) == 2
    assert (tmp_path / "report.json").read_bytes() == before
    out = tmp_path / "out"
    out.mkdir()
    other = tmp_path / "precious.txt"
    other.write_text("preserve")
    (out / "report.json").symlink_to(other)
    assert main(["--watchlist", str(path), "--out-dir", str(out)]) == 0
    assert other.read_text() == "preserve" and not (out / "report.json").is_symlink()


@pytest.mark.parametrize("mutation", ["empty-tools", "unknown-top-field", "workflow-outside-selection"])
def test_watchlist_selection_and_fields_are_explicit(tmp_path, mutation):
    path = write_bundle(tmp_path)
    value = json.loads(path.read_text())
    if mutation == "empty-tools":
        value["dependencies"][0]["tools"] = []
    elif mutation == "unknown-top-field":
        value["execute"] = "anything"
    else:
        value["dependencies"][0]["workflows"][0]["tools"] = ["not-selected"]
    path.write_text(json.dumps(value))
    with pytest.raises(InvalidInput):
        build_report(path)


def test_historical_core_only_retains_schema_diff_without_inventing_added_current_annotations(tmp_path):
    before = observation(extrasCoverage="unknown")
    after = observation([tool(inputSchema={"type": "object", "properties": {"query": {"type": "integer"}},
                                          "required": ["query"]},
                              annotations={"readOnlyHint": False, "destructiveHint": True}, _meta={"price": 12})],
                        observedAt="2026-10-27T10:00:00Z", extrasCoverage="observed")
    report = build_report(write_bundle(tmp_path, before=before, after=after))
    row = report["dependencies"][0]
    assert row["status"] == row["comparability"] == "unknown" and row["observedChanges"] is True
    assert row["fieldCoverage"] == "core-only"
    assert row["sources"]["before"]["extrasCoverage"] == "unknown"
    assert row["sources"]["after"]["extrasCoverage"] == "observed"
    selected = row["selectedTools"][0]
    assert selected["status"] == "changed" and selected["changes"] == ["inputSchema"]
    assert selected["inputTypeChangedPaths"] == ["$/properties/query"]
    assert "extras" not in selected and selected["extrasCompared"] is False
    assert row["workflows"][0]["status"] == "unknown" and row["workflows"][0]["retest"]
    assert "Only core fields were compared" in markdown(report)


def test_unknown_extras_alone_cannot_mean_added_removed_or_unchanged_and_coverage_is_strict(tmp_path):
    before = observation([tool(annotations={"readOnlyHint": True}, vendorHint="historical")], extrasCoverage="observed")
    after = observation(observedAt="2026-10-27T10:00:00Z", extrasCoverage="unknown")
    path = write_bundle(tmp_path, before=before, after=after)
    row = first(path)
    assert row["status"] == "unknown" and row["observedChanges"] is False
    assert row["selectedTools"][0]["status"] == "unknown"
    assert row["selectedTools"][0]["changes"] == [] and "extras" not in row["selectedTools"][0]
    assert row["workflows"][0]["status"] == "unknown"
    assert not any("selected tool absent" in note for note in row["limitations"])
    value = json.loads((tmp_path / "after.json").read_text())
    value["extrasCoverage"] = "full-but-unbound"
    (tmp_path / "after.json").write_text(json.dumps(value))
    with pytest.raises(InvalidInput, match="extrasCoverage"):
        build_report(path)
