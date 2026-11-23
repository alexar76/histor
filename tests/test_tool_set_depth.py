"""mcp-tool-set-digest/1 section 12 in HISTOR: every level up to 512 is read and digested, counting the
tools array as level 1; a contract entry with an array or object deeper than that is MTL-SUBJ-005
(TSD-DEPTH); a server's answer and a /check body are never refused or crashed on for their depth.

The edges are CrawlCheck's (alexar76/mcp-tool-set-digest#1). The digests at the edge are the ones
the three reference implementations of the specification give for the same text.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from histor.mcpclient import parse_json
from histor.subject import MTL_SUBJ_005, MtlError, tool_set_digest
from histor.untrusted import cut_deep, loads_cut

# The reference implementations' contract digest of [{"name":"t","inputSchema":{"x": 509 arrays}}].
AT_THE_EDGE = "sha256-U2SmamxwXvZ4K/aIwTKEH/uhwN0VOxAwt0IhxlX+UmQ="
# ... and of [{"name":"t"}]: a description that is not a string is read as absent.
BARE_TOOL = "sha256-88Wu+RBqslXnbKusDxk1TtLT0D2iT/d07h7ia5bzX2U="


def nest(n: int) -> str:
    return "[" * n + "0" + "]" * n


def schema_text(n: int) -> str:
    return '[{"name":"t","inputSchema":{"x":' + nest(n) + "}}]"


def test_512_levels_are_digested_and_513_are_not() -> None:
    digest, _ = tool_set_digest(loads_cut(schema_text(509)))
    assert digest == AT_THE_EDGE
    with pytest.raises(MtlError) as refused:
        tool_set_digest(loads_cut(schema_text(510)))
    assert refused.value.code == MTL_SUBJ_005 and "'t'" in refused.value.detail


def test_100000_levels_are_read_without_a_crash() -> None:
    tools = loads_cut(schema_text(100_000))
    with pytest.raises(MtlError) as refused:
        tool_set_digest(tools)
    assert refused.value.code == MTL_SUBJ_005


def test_a_deep_value_the_contract_entry_does_not_carry_does_not_count() -> None:
    tools = loads_cut('[{"name":"t","description":' + nest(100_000) + ',"_meta":{"x":' + nest(100_000) + "}}]")
    digest, _ = tool_set_digest(tools)
    assert digest == BARE_TOOL


def test_the_edge_holds_inside_a_crawl_thread() -> None:
    with ThreadPoolExecutor(max_workers=1) as pool:
        digest, _ = pool.submit(lambda: tool_set_digest(loads_cut(schema_text(509)))).result()
    assert digest == AT_THE_EDGE


def test_a_server_answer_is_read_at_any_depth() -> None:
    answer = '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"t","inputSchema":{"x":%s}}]}}'
    shallow = parse_json(answer % nest(509))
    assert tool_set_digest(shallow["result"]["tools"])[0] == AT_THE_EDGE
    deep = parse_json(answer % nest(100_000))
    with pytest.raises(MtlError):
        tool_set_digest(deep["result"]["tools"])


def test_cut_keeps_shallow_text_and_strings_and_refuses_bad_brackets() -> None:
    text = '{"a":"[[[{{{","b":[1,{"c":[]}]}'
    assert cut_deep(text) is text
    assert cut_deep("[" * 3 + "]" * 3, limit=2) == "[[[]]]"
    assert cut_deep('[[{"x":"]]"}]]', limit=1) == "[[]]"
    assert json.loads(cut_deep('{"a":[[[1,2],[3]]],"b":{"c":{}}}', limit=2)) == {"a": [[]], "b": {"c": {}}}
    with pytest.raises(ValueError):
        cut_deep("[" * 3 + "}" + "]" * 2, limit=1)
    with pytest.raises(ValueError):
        cut_deep("[" * 5, limit=1)
