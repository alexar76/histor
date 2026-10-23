"""Tool list cost: the split, the diff, the regression gate, the CLI against a real stdio server, the API."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from histor import __main__ as cli
from histor import toolcost
from histor.app import create_app
from tests.conftest import make_target, tool

# A tokenizer anyone can count by hand: one token per character. The arithmetic does not depend on it.
CHARS = len


def test_parts_add_up_to_what_is_sent():
    c = toolcost.cost([tool("b_search", "Find things.", q="string"), tool("a_get", "", id="integer")], CHARS)
    assert [r["name"] for r in c["tools"]] == ["a_get", "b_search"]
    t = c["totals"]
    assert t["sent"] == t["names"] + t["descriptions"] + t["inputSchemas"] + t["structure"]
    search = c["tools"][1]
    assert search["parts"]["description"] == len('"Find things."')
    assert search["parts"]["inputSchema"] == len('{"type":"object","properties":{"q":{"type":"string"}}}')
    # {"name":,"description":,"input_schema":} around the three values
    assert search["parts"]["structure"] == len('{"name":,"description":,"input_schema":}')
    assert t["outputSchemas"] == 0 and t["listed"] > 0 and c["format"] == toolcost.FORMAT


def test_missing_members_are_counted_as_a_client_sends_them():
    c = toolcost.cost([{"name": "bare"}, "not a tool", {"name": "out", "outputSchema": {"type": "object"}}], CHARS)
    bare, out = c["tools"]
    assert bare["parts"]["description"] == len('""') and bare["parts"]["inputSchema"] == len('{"type":"object"}')
    assert out["outputSchema"] == len('{"type":"object"}') and c["toolCount"] == 2


def test_a_repeated_name_still_costs():
    c = toolcost.cost([tool("x", "one"), tool("x", "two")], CHARS)
    assert [r["name"] for r in c["tools"]] == ["x", "x#2"]


def test_diff_names_what_moved_and_where():
    before = toolcost.cost([tool("keep", "same"), tool("grow", "short", a="string"), tool("gone", "bye")], CHARS)
    after = toolcost.cost([tool("keep", "same"), tool("grow", "short", a="string", b="string", c="string"),
                           tool("new", "hello")], CHARS)
    d = toolcost.diff(before, after)
    assert [r["name"] for r in d["added"]] == ["new"] and [r["name"] for r in d["removed"]] == ["gone"]
    assert d["unchanged"] == 1 and [c["name"] for c in d["changed"]] == ["grow"]
    grow = d["changed"][0]
    assert grow["description"]["delta"] == 0 and grow["inputSchema"]["delta"] == grow["sent"]["delta"] > 0
    assert d["totals"]["descriptions"]["delta"] == len('"hello"') - len('"bye"')
    assert d["toolCount"] == {"before": 3, "after": 3, "delta": 0}
    # which field: the two new properties, each with its own tokens
    assert [(f["name"], f["before"]) for f in grow["properties"]] == [("b", 0), ("c", 0)]
    assert grow["properties"][0]["after"] == len('"b":{"type":"string"}')


def test_a_property_that_grows_is_named():
    big = {"type": "object", "description": "Filter by " + "field, " * 40}
    before = toolcost.cost([tool("search", "Search.", q="string", limit="integer")], CHARS)
    after = toolcost.cost([{**tool("search", "Search.", q="string", limit="integer"),
                            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "filters": big}}}], CHARS)
    d = toolcost.diff(before, after)
    props = {p["name"]: p for p in d["changed"][0]["properties"]}
    assert props["filters"]["before"] == 0 and props["limit"]["after"] == 0 and "q" not in props
    text = toolcost.markdown(d)
    assert "Where in the input schemas:" in text and "`filters` +" in text and "(new)" in text and "(removed)" in text


def test_diff_refuses_two_tokenizers():
    a = toolcost.cost([tool("x")], CHARS)
    with pytest.raises(ValueError, match="different tokenizers"):
        toolcost.diff(a, {**a, "tokenizer": "cl100k_base"})


def _grown(schema_props: int, description: str = "Search the index.") -> dict:
    props = {f"field_{i}": "string" for i in range(schema_props)}
    return toolcost.cost([tool("search", description, **props), tool("other", "x" * 400)], CHARS)


def test_the_gate_names_a_schema_regression_the_total_hides():
    before = _grown(1, "A long description. " * 20)
    after = _grown(12, "Short.")
    d = toolcost.diff(before, after)
    problems = toolcost.regressions(d, max_increase="10%")
    assert any(p.startswith("input schemas +") for p in problems)
    assert not any(p.startswith("sent to the model") for p in problems)


def test_the_gate_limits():
    d = toolcost.diff(_grown(1), _grown(4))
    grew = d["totals"]["sent"]["delta"]
    assert toolcost.regressions(d, max_increase=f"{grew}") == []          # tokens: not more than allowed
    assert toolcost.regressions(d, max_increase=f"{grew - 1}")            # one token over
    assert toolcost.regressions(d, max_increase="10%", noise=grew + 1) == []
    assert toolcost.regressions(d, max_increase=None, budget=d["totals"]["sent"]["after"]) == []
    assert "over the budget" in toolcost.regressions(d, max_increase=None, budget=10)[0]
    assert toolcost.regressions(toolcost.diff(_grown(4), _grown(1)), max_increase="0%") == []  # shrinking
    with pytest.raises(ValueError):
        toolcost.parse_limit("ten")


def test_markdown_shows_the_split_and_the_verdict():
    d = toolcost.diff(_grown(1), _grown(6))
    text = toolcost.markdown(d, toolcost.regressions(d))
    assert "| input schemas |" in text and "| descriptions |" in text and "❌" in text
    assert "| `search` |" in text
    assert "✅ within the limits" in toolcost.markdown(d, [])


# -- real o200k_base --------------------------------------------------------------------------------

def _encoder():
    pytest.importorskip("tiktoken")
    try:
        return toolcost.encoder()
    except Exception as exc:  # the table is downloaded once; offline, this test cannot run
        pytest.skip(f"o200k_base unavailable: {exc}")


def test_same_count_as_the_published_survey_method():
    count = _encoder()
    tools = [tool("get_weather", "Return the weather for a city.", city="string"),
             {"name": "notes", "description": "Ünïcode — 天气", "inputSchema": {}}]
    # scripts/tool_list_tokens.py, which produced docs/tool-list-cost.md
    survey = sum(count(json.dumps({"name": t.get("name"), "description": t.get("description") or "",
                                   "input_schema": t.get("inputSchema") or {"type": "object"}},
                                  ensure_ascii=False, separators=(",", ":"))) for t in tools)
    c = toolcost.cost(tools)
    assert c["totals"]["sent"] == survey and c["tokenizer"] == "o200k_base"
    assert c["totals"]["structure"] > 0


# -- the command line, against a real stdio server ---------------------------------------------------

SERVER = textwrap.dedent('''
    import json, os, sys
    tools = json.loads(os.environ["TOOLS"])
    print("a log line on stdout", flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg or "method" not in msg:
            continue
        if msg["method"] == "initialize":
            # ask the client something first: it must refuse and carry on
            print(json.dumps({"jsonrpc": "2.0", "id": "s1", "method": "roots/list"}), flush=True)
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "t", "version": "1"}}
        elif msg["method"] == "tools/list":
            page = int((msg.get("params") or {}).get("cursor") or 0)
            result = {"tools": tools[page:page + 1]}
            if page + 1 < len(tools):
                result["nextCursor"] = str(page + 1)
        else:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
''')


@pytest.fixture
def server(tmp_path, monkeypatch):
    path = tmp_path / "server.py"
    path.write_text(SERVER)

    def set_tools(tools):
        monkeypatch.setenv("TOOLS", json.dumps(tools))
        return f"{sys.executable} {path}"
    return set_tools


@pytest.fixture
def chars(monkeypatch):
    monkeypatch.setattr(toolcost, "_encoder", CHARS)


def test_snapshot_check_and_update(server, chars, tmp_path, capsys, monkeypatch):
    baseline = tmp_path / "tool-cost.json"
    cmd = server([tool("search", "Search.", q="string"), tool("fetch", "Fetch a page.", url="string")])
    assert cli.main(["tool-cost", "snapshot", "--stdio", cmd, "-o", str(baseline)]) == 0
    snap = json.loads(baseline.read_text())
    assert snap["toolCount"] == 2 and snap["format"] == toolcost.FORMAT   # both pages drained

    assert cli.main(["tool-cost", "check", "--baseline", str(baseline), "--stdio", cmd]) == 0
    assert "✅ within the limits" in capsys.readouterr().out

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    props = {f"p{i}": "string" for i in range(20)}
    grown = server([tool("search", "Search.", **props), tool("fetch", "Fetch a page.", url="string")])
    assert cli.main(["tool-cost", "check", "--baseline", str(baseline), "--stdio", grown]) == 1
    out = capsys.readouterr().out
    assert "input schemas +" in out and "❌" in out and summary.read_text().count("### Tool list cost") == 1

    assert cli.main(["tool-cost", "check", "--baseline", str(baseline), "--stdio", grown, "--json",
                     "--max-increase", "none", "--update"]) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["ok"] and verdict["diff"]["changed"][0]["name"] == "search"
    assert json.loads(baseline.read_text())["totals"] == toolcost.cost(
        [tool("search", "Search.", **props), tool("fetch", "Fetch a page.", url="string")], CHARS)["totals"]


def test_diff_reads_tool_lists_in_every_saved_shape(chars, tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [tool("x", "old")]}}))
    b.write_text(json.dumps([tool("x", "a newer description"), tool("y")]))
    assert cli.main(["tool-cost", "diff", str(a), str(b), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in d["added"]] == ["y"] and d["changed"][0]["description"]["delta"] > 0


def test_cli_failures_exit_2(server, chars, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"nothing": 1}')
    assert cli.main(["tool-cost", "snapshot", "--tools", str(bad)]) == 2
    assert "no tool list here" in capsys.readouterr().err
    assert cli.main(["tool-cost", "snapshot", "--stdio", f"{sys.executable} -c pass", "--timeout", "5"]) == 2
    err = capsys.readouterr().err
    assert "exited before answering initialize" in err or "closed its input" in err
    assert cli.main(["tool-cost", "snapshot"]) == 2
    assert cli.main(["tool-cost", "check", "--baseline", str(bad), "--tools", str(bad), "--max-increase", "lots"]) == 2


# -- the service ------------------------------------------------------------------------------------

@pytest.fixture
def api(world, monkeypatch):
    monkeypatch.setattr(toolcost, "_encoder", CHARS)
    world["mcp"].tools["/a"] = [tool("get_weather", "Return the weather.", city="string")]
    world["targets"].append(make_target("io.example/weather", "/a"))
    world["services"].crawler.run()
    with TestClient(create_app(world["services"])) as client:
        yield client, world


def test_a_change_carries_its_cost(api):
    client, world = api
    world["mcp"].tools["/a"] = [tool("get_weather", "Return the weather.", city="string", units="string"),
                                tool("get_alerts", "Weather alerts.")]
    world["clock"].advance(days=1)
    world["services"].crawler.run()
    change = client.get("/api/v1/changes").json()["changes"][0]
    cost = client.get(f"/api/v1/changes/{change['id']}/cost").json()
    assert cost["format"] == toolcost.DIFF_FORMAT and cost["change"]["toToolSetDigest"] == change["to_toolset"]
    assert [r["name"] for r in cost["added"]] == ["get_alerts"]
    assert cost["changed"][0]["inputSchema"]["delta"] > 0 and cost["totals"]["inputSchemas"]["delta"] > 0
    assert client.get("/api/v1/changes/99999/cost").status_code == 404

    tid = change["target_id"]
    server = client.get(f"/api/v1/servers/{tid}/cost").json()
    assert server["current"]["toolCount"] == 2 and server["changes"][0]["added"] == 1
    assert server["changes"][0]["cost"] == f"/api/v1/changes/{change['id']}/cost"
    assert client.get("/api/v1/servers/nope/cost").status_code == 404

    digest = server["toolSetDigest"]
    by_digest = client.get(f"/api/v1/cost/toolsets/{digest}").json()
    assert by_digest["totals"] == server["current"]["totals"] and by_digest["toolSetDigest"] == digest
    assert client.get("/api/v1/cost/toolsets/sha256-nope").status_code == 404


def test_without_a_tokenizer_the_service_says_so(api, monkeypatch):
    client, world = api
    monkeypatch.setattr(toolcost, "_encoder", None)
    monkeypatch.setitem(sys.modules, "tiktoken", None)   # import fails
    tid = next(t["id"] for t in world["services"].store.all_targets())
    r = client.get(f"/api/v1/servers/{tid}/cost")
    assert r.status_code == 503 and "cost extra" in r.json()["detail"]


def test_the_cache_counts_a_set_once():
    calls = []
    cache = toolcost.CostCache(size=1, count=CHARS)
    body = lambda: (calls.append(1), json.dumps([tool("x")]))[1]  # noqa: E731
    assert cache.get("d1", body)["toolCount"] == 1
    assert cache.get("d1", body) is cache.get("d1", body) and len(calls) == 1
    cache.get("d2", body)
    cache.get("d1", body)                                             # evicted by d2, counted again
    assert len(calls) == 3 and cache.get("none", lambda: None) is None
