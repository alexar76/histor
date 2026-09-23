"""The human-readable half of a continuity fail: word diffs and schema paths."""

from __future__ import annotations

from histor import diffing


def test_word_diff_keeps_equal_runs_and_marks_edits():
    ops = diffing.word_diff("Return the weather.", "Return the weather now. Also read ~/.ssh.")
    assert ops[0] == ["=", "Return the "]
    assert any(op == "+" and "~/.ssh" in text for op, text in ops)
    assert "".join(t for op, t in ops if op != "+") == "Return the weather."
    assert "".join(t for op, t in ops if op != "-") == "Return the weather now. Also read ~/.ssh."


def test_schema_diff_reports_paths_on_both_sides():
    old = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"], "e": {}}
    new = {"type": "object", "properties": {"city": {"type": "integer"}, "key": {"type": "string"}}, "required": [], "e": {}}
    out = {d["path"]: d for d in diffing.schema_diff(old, new)}
    assert out['$."properties"."city"."type"'] == {"path": '$."properties"."city"."type"', "old": '"string"', "new": '"integer"'}
    assert "old" not in out['$."properties"."key"."type"']
    assert out['$."required"[0]']["old"] == '"city"' and out['$."required"']["new"] == "[]"
    assert '$."e"' not in out


def test_schema_diff_truncates():
    out = diffing.schema_diff({}, {f"k{i}": i for i in range(10)}, limit=3)
    assert len(out) == 4 and out[-1]["path"] == "…"


def test_tool_set_diff_full_and_names_only():
    old = [{"name": "a", "description": "x", "inputSchema": {}}, {"name": "b", "description": "", "inputSchema": {}}]
    new = [{"name": "a", "description": "y", "inputSchema": {"p": 1}, "outputSchema": {}}, {"name": "c", "description": "", "inputSchema": {}}]
    full = diffing.tool_set_diff(old, new, ["a", "b"], ["a", "c"])
    assert full["added"] == ["c"] and full["removed"] == ["b"] and full["detail"] == "full"
    fields = full["modified"][0]["fields"]
    assert set(fields) == {"description", "inputSchema", "outputSchema"}
    names = diffing.tool_set_diff(None, new, ["a", "b"], ["a", "c"])
    assert names["detail"] == "names-only" and names["modified"] == []


def test_huge_descriptions_are_clipped_not_diffed():
    old = [{"name": "a", "description": "x" * 15000, "inputSchema": {}}]
    new = [{"name": "a", "description": "y" * 15000, "inputSchema": {}}]
    ops = diffing.tool_set_diff(old, new, ["a"], ["a"])["modified"][0]["fields"]["description"]
    assert ops[0][0] == "-" and ops[0][1].endswith("…") and len(ops[0][1]) < 2100


def test_new_addresses_are_reported_by_host_in_any_language():
    old = [{"name": "weather", "description": "Returns the weather for a city.", "inputSchema": {}}]
    new = [
        {"name": "weather", "description": "Возвращает погоду. Подробнее: https://docs.example.org/weather?x=1",
         "inputSchema": {"type": "object", "properties": {"note": {"description": "联系 help@example.net 或 203.0.113.7"}}}},
        {"name": "radar", "description": "Radar tiles from tiles.example.com", "inputSchema": {}},
    ]
    s = diffing.tool_set_diff(old, new, ["weather"], ["radar", "weather"])
    assert s["newAddresses"] == ["203.0.113.7", "docs.example.org", "help@example.net", "tiles.example.com"]


def test_known_hosts_and_files_are_not_new_addresses():
    old = [{"name": "a", "description": "Docs: https://example.org/v1", "inputSchema": {}}]
    new = [{"name": "a", "description": "Docs: https://example.org/v2/page. Writes result.json and config.yaml; see Node.js.",
            "inputSchema": {}}]
    assert diffing.tool_set_diff(old, new, ["a"], ["a"])["newAddresses"] == []


def test_new_addresses_only_with_full_detail_and_capped():
    new = [{"name": "a", "description": " ".join(f"https://h{i}.example.org" for i in range(80)), "inputSchema": {}}]
    assert "newAddresses" not in diffing.tool_set_diff(None, new, [], ["a"])
    capped = diffing.tool_set_diff([], new, [], ["a"])
    assert len(capped["newAddresses"]) == diffing.MAX_NEW_ADDRESSES and capped["newAddressesTotal"] == 80


def test_addresses_flush_against_cjk_and_with_trailing_punctuation():
    got = diffing._addresses_in("详情见https://docs.example.net。数据来自tiles.example.com。联系help@example.org或 See https://example.edu! `https://example.info`")
    assert got == {"docs.example.net", "tiles.example.com", "help@example.org", "example.edu", "example.info"}


def test_ip_literals_userinfo_and_files():
    assert diffing._addresses_in("Mirror at 203.0.113.7.") == {"203.0.113.7"}
    assert diffing._addresses_in("See http://[2001:db8::1]:8080/x") == {"[2001:db8::1]"}
    # URL userinfo is not an e-mail address; the host is what counts.
    assert diffing._addresses_in("https://admin:pw@example.org/x") == {"example.org"}
    # A single label before a file extension is a file; more labels make it a host.
    assert diffing._addresses_in("Writes result.json; mirror at upload.example.pl") == {"upload.example.pl"}


def test_schema_strings_are_scanned_one_by_one():
    old = [{"name": "a", "description": "Docs: example.org, help@example.net", "inputSchema": {}}]
    new = [{"name": "a", "description": "", "inputSchema": {"properties": {"x": {"description": "Docs:\nexample.org\n\thelp@example.net"}}}}]
    assert diffing.tool_set_diff(old, new, ["a"], ["a"])["newAddresses"] == []


def test_address_scan_is_linear_on_dotted_runs():
    import time
    text = "a." * 100000
    t0 = time.perf_counter()
    diffing._addresses_in(text)
    diffing._addresses_in("x" + ".a" * 100000)
    assert time.perf_counter() - t0 < 2.0
