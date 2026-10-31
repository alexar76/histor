"""MTL/1 digests: the profile's own worked example, ordering, and the withheld-digest cases."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from awr import canonical_sri

from histor.subject import (
    MtlError,
    build_subject,
    canonical_tool_bytes,
    canonical_tool_set,
    es_number,
    tool_set_digest,
    utf16_sort_key,
)

PROFILE_DIR = Path(__file__).resolve().parents[2] / "awr" / "adoption" / "mcp-trust-label"

CLEAN_TOOLS = [
    {"name": "get_weather", "description": "Return the current weather for a city.",
     "inputSchema": {"type": "object", "properties": {"city": {"type": "string", "description": "City name."}},
                     "required": ["city"]}},
    {"name": "list_cities", "description": "List the cities this server can report on.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def test_the_profiles_worked_example_reproduces():
    """PROFILE.md section 4.2 prints both digests; a drift here breaks every registry that recomputes."""
    subject = build_subject(server_name="com.example/weather", registry="urn:awr:mtl:1:registry:example",
                            tools=CLEAN_TOOLS, server_version="1.4.2", transport="stdio",
                            package="npm:@example/weather-mcp@1.4.2")
    assert subject.tool_set_digest == "sha256-9+Gp1Oq15KPBAAGmFucoVwSQ/PUzwLt1CRxesSUvX5c="
    assert subject.digest == "sha256-nNR6utZJHl/EpVoffkzaYj4kA7LbJOig5Yz91lk6k1s="
    assert subject.urn.startswith("urn:awr:mtl:1:subject:sha256:9cd47aba")


def test_order_is_utf16_code_units_not_locale_or_code_points():
    names = ["résumé", "rezume", "Z_tool", "a_tool", "\U0001F600x", "￿x"]
    ordered = [e["name"] for e in canonical_tool_set([{"name": n} for n in names])]
    assert ordered == sorted(names, key=utf16_sort_key)
    assert ordered.index("Z_tool") < ordered.index("a_tool")
    # Outside the BMP a surrogate pair (0xD83D…) sorts BEFORE U+FFFF; code-point order says after.
    assert ordered.index("\U0001F600x") < ordered.index("￿x")


def test_output_schema_presence_changes_the_digest():
    base = [{"name": "t", "description": "d", "inputSchema": {}}]
    with_empty = [{**base[0], "outputSchema": {}}]
    assert tool_set_digest(base)[0] != tool_set_digest(with_empty)[0]


def test_non_digested_members_are_dropped():
    a = [{"name": "t", "description": "d", "inputSchema": {}, "title": "One", "annotations": {"readOnlyHint": True}}]
    b = [{"name": "t", "description": "d", "inputSchema": {}}]
    assert tool_set_digest(a)[0] == tool_set_digest(b)[0]


@pytest.mark.parametrize("tools", [[{"description": "no name"}], ["not an object"]])
def test_an_entry_with_no_name_raises_subj_001(tools):
    with pytest.raises(MtlError) as err:
        tool_set_digest(tools)
    assert err.value.code == "MTL-SUBJ-001"


def test_an_empty_set_is_digested():
    # MTL/1.1: "serves no tools" is a state, and a server that later serves some has changed.
    sri, entries = tool_set_digest([])
    assert entries == [] and sri == canonical_sri([])


def test_duplicate_names_are_digested_whatever_the_listing_order():
    a = [{"name": "a", "description": "two"}, {"name": "b"}, {"name": "a", "description": "one"}]
    assert tool_set_digest(a)[0] == tool_set_digest(list(reversed(a)))[0]
    names = [e["description"] for e in canonical_tool_set(a)]
    assert names == ["one", "two", ""]  # the shared name, then canonical bytes


def test_a_float_is_digested_and_the_subject_says_so():
    tools = [{"name": "p", "inputSchema": {"type": "object", "properties": {"x": {"minimum": 0.5}}}}]
    subject = build_subject(server_name="s", registry="r", tools=tools)
    assert subject.hazard is None and subject.tool_set_digest == subject.descriptor["toolSet"]["digestSRI"]
    assert canonical_tool_bytes(subject.entries).endswith(b'"properties":{"x":{"minimum":0.5}},"type":"object"},"name":"p"}]')


def test_a_number_with_no_double_still_withholds_the_digest():
    tools = [{"name": "p", "inputSchema": {"maximum": float("inf")}}]
    subject = build_subject(server_name="s", registry="r", tools=tools)
    assert subject.hazard is not None and subject.hazard.code == "MTL-NUM-001" and "'p'" in subject.hazard.detail
    assert subject.tool_set_digest is None and "digestSRI" not in subject.descriptor["toolSet"]
    assert subject.names == ["p"]


def test_integer_only_sets_keep_their_awr_bytes():
    """Every digest issued before MTL/1.1 is unchanged: integers inside 2^53 take AWR/2's bytes."""
    tools = [{"name": "t", "inputSchema": {"maximum": 9007199254740991, "minimum": -9007199254740991, "d": [0, -1]}}]
    sri, entries = tool_set_digest(tools)
    assert sri == canonical_sri(entries)


@pytest.mark.parametrize("value,text", [
    # Node's JSON.stringify for each (RFC 8785 section 3.2.2.3, ECMAScript Number::toString).
    (0.0, "0"), (-0.0, "0"), (0.7, "0.7"), (5.0, "5"), (100.0, "100"), (123.456, "123.456"),
    (1e20, "100000000000000000000"), (1e21, "1e+21"), (1e-6, "0.000001"), (1e-7, "1e-7"),
    (1.5e-7, "1.5e-7"), (0.1 + 0.2, "0.30000000000000004"), (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"), (-4.35, "-4.35"),
    (float(18446744073709551615), "18446744073709552000"),
])
def test_numbers_are_written_as_ecmascript_writes_them(value, text):
    assert es_number(value) == text


def test_an_integer_beyond_2_53_is_its_nearest_double_as_json_parse_holds_it():
    exact = [{"name": "t", "inputSchema": {"maximum": 9007199254740993}}]
    rounded = [{"name": "t", "inputSchema": {"maximum": 9007199254740992.0}}]
    assert tool_set_digest(exact)[0] == tool_set_digest(rounded)[0]


def test_optional_members_are_omitted_never_null():
    subject = build_subject(server_name="s", registry="r", tools=[{"name": "t"}])
    assert "artifact" not in subject.descriptor and "version" not in subject.descriptor["server"]


def test_parity_with_the_profiles_reference_tool():
    """HISTOR carries a copy of mtl_subject.py; both must compute the same bytes."""
    tool_path = PROFILE_DIR / "tools" / "mtl_subject.py"
    if not tool_path.is_file():
        pytest.skip("standalone checkout: the AWR profile is not beside this package")
    spec = importlib.util.spec_from_file_location("mtl_subject_ref", tool_path)
    ref = importlib.util.module_from_spec(spec)
    sys.modules["mtl_subject_ref"] = ref
    spec.loader.exec_module(ref)
    cases = [
        CLEAN_TOOLS,
        [{"name": "Z"}, {"name": "a", "outputSchema": {"type": "object"}}, {"name": "é", "description": 3}],
        [{"name": "\U0001F600"}, {"name": "￿"}],
        [{"name": "p", "inputSchema": {"minimum": 0.5, "maximum": 1e21, "big": 18446744073709551615}}],
        [],
        [{"name": "a", "description": "two"}, {"name": "a", "description": "one"}],
        [{"name": "p", "inputSchema": {"maximum": float("inf")}}],
    ]
    for tools in cases:
        mine = build_subject(server_name="n", registry="r", tools=tools, endpoint="https://x/mcp",
                             transport="streamable-http")
        theirs, sri, hazard = ref.build_descriptor(server_name="n", registry="r", tools=tools,
                                                   endpoint="https://x/mcp", transport="streamable-http")
        assert mine.descriptor == theirs and mine.tool_set_digest == sri
        assert mine.digest == ref.descriptor_digest(theirs)
        assert mine.reference() == ref.subject_reference(theirs)
