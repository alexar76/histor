"""scripts/change_kinds.py: "description text changed" and "nothing but descriptions changed" are different counts."""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "change_kinds.py"
spec = importlib.util.spec_from_file_location("change_kinds", SCRIPT)
change_kinds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(change_kinds)


def summary(modified=(), added=(), removed=(), detail="full"):
    return {"added": list(added), "removed": list(removed), "modified": list(modified), "detail": detail}


DESC = {"tool": "search", "fields": {"description": [["=", "Find "], ["-", "notes"], ["+", "files"]]}}
PARAM_DESC = {"tool": "search", "fields": {"inputSchema": [
    {"path": '$."properties"."q"."description"', "old": '"the query"', "new": '"what to look for"'}]}}
TYPE = {"tool": "search", "fields": {"inputSchema": [
    {"path": '$."properties"."limit"."type"', "old": '"string"', "new": '"integer"'}]}}
BOTH = {"tool": "search", "fields": {**DESC["fields"], **TYPE["fields"]}}


def test_only_descriptions_counts_tool_and_parameter_descriptions_and_nothing_else():
    assert change_kinds.descriptions_only(summary([DESC])) == "tool"
    assert change_kinds.descriptions_only(summary([DESC, PARAM_DESC])) == "param"
    # A description edit that came with a type change is "text" in the kinds table, but not description-only.
    assert change_kinds.kind(summary([BOTH])) == "text"
    assert change_kinds.descriptions_only(summary([BOTH])) is None
    assert change_kinds.descriptions_only(summary([DESC], added=["new_tool"])) is None
    # A rename is a removal plus an addition: never description-only.
    assert change_kinds.descriptions_only(summary([], added=["find"], removed=["search"])) is None
    assert change_kinds.descriptions_only(summary(detail="names-only")) is None
    truncated = {"tool": "search", "fields": {"inputSchema": [{"path": "…", "note": "truncated at 200 paths"}]}}
    assert change_kinds.descriptions_only(summary([truncated])) is None


def test_the_report_prints_both_counts(tmp_path, capsys):
    servers = [{"id": "a", "transport": "streamable-http", "delisted": False, "toolSetDigest": "sha256-x",
                "firstPinned": "2026-09-24T12:00:00Z"}]
    changes = [{"serverId": "a", "observedAt": f"2026-09-2{i}T10:00:00Z", "summaryJson": json.dumps(s)}
               for i, s in enumerate([summary([DESC]), summary([PARAM_DESC]), summary([BOTH]), summary([TYPE])], 4)]
    for name, rows in (("servers.jsonl.gz", servers), ("changes.jsonl.gz", changes)):
        with gzip.open(tmp_path / name, "wt", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
    assert change_kinds.main([str(tmp_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kinds"]["text"] == 3
    assert out["descriptionsOnly"] == {"toolDescriptions": 1, "withParameterDescriptions": 2}
