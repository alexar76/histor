"""MTL/1 digests: the profile's own worked example, ordering, and the withheld-digest cases."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from histor.subject import MtlError, build_subject, canonical_tool_set, tool_set_digest, utf16_sort_key

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


@pytest.mark.parametrize("tools,code", [
    ([], "MTL-SUBJ-002"),
    ([{"name": "a"}, {"name": "a"}], "MTL-SUBJ-003"),
    ([{"description": "no name"}], "MTL-SUBJ-001"),
    (["not an object"], "MTL-SUBJ-001"),
])
def test_undigestible_sets_raise_the_profiles_codes(tools, code):
    with pytest.raises(MtlError) as err:
        tool_set_digest(tools)
    assert err.value.code == code


def test_a_float_withholds_the_digest_but_keeps_the_subject():
    tools = [{"name": "p", "inputSchema": {"type": "object", "properties": {"x": {"minimum": 0.5}}}}]
    subject = build_subject(server_name="s", registry="r", tools=tools)
    assert subject.hazard is not None and subject.hazard.code == "MTL-NUM-001"
    assert subject.tool_set_digest is None and "digestSRI" not in subject.descriptor["toolSet"]
    assert subject.names == ["p"]


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
    ]
    for tools in cases:
        mine = build_subject(server_name="n", registry="r", tools=tools, endpoint="https://x/mcp",
                             transport="streamable-http")
        theirs, sri, hazard = ref.build_descriptor(server_name="n", registry="r", tools=tools,
                                                   endpoint="https://x/mcp", transport="streamable-http")
        assert mine.descriptor == theirs and mine.tool_set_digest == sri
        assert mine.digest == ref.descriptor_digest(theirs)
        assert mine.reference() == ref.subject_reference(theirs)
