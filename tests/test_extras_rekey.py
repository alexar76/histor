"""The two ways HISTOR can write the extras digest (histor.extras: legacy, rfc8785), and moving a log from
one to the other (histor.extras_rekey) without recording a single extras change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from histor import extras, extras_rekey
from histor.store import Store
from tests.conftest import make_target, tool

VECTORS = Path(__file__).resolve().parents[2] / "mcp-tool-set-digest" / "vectors" / "vectors.json"


def annotated(name: str, description: str = "", **extra):
    return {**tool(name, description), **extra}


def rows(s, target_id):
    with s.store.db.read() as tx:
        target = tx.one("SELECT current_extras FROM targets WHERE id=?", (target_id,))
        changes = tx.execute("SELECT from_extras, to_extras FROM extras_changes WHERE target_id=? ORDER BY seq", (target_id,))
        observed = [r["extras"] for r in tx.execute(
            "SELECT extras FROM observations WHERE target_id=? AND status='ok' ORDER BY id", (target_id,))]
        scheme = Store.extras_scheme(tx)
    return target["current_extras"], changes, observed, scheme


# --- the digest -------------------------------------------------------------------------------------------

def test_the_two_schemes_differ_only_where_rfc_8785_writes_otherwise():
    plain = [annotated("read", annotations={"readOnlyHint": True}, title="Read")]
    assert extras.tool_extras(plain, extras.LEGACY) == extras.tool_extras(plain, extras.RFC8785)
    for meta in ({"price": 1.0}, {"big": 9007199254740993}, {"\U0001F600": 1, "￿": 2}, {"p": 1e-7}):
        tools = [annotated("pay", _meta=meta)]
        legacy, body = extras.tool_extras(tools, extras.LEGACY)
        modern, same_body = extras.tool_extras(tools, extras.RFC8785)
        assert body == same_body and legacy != modern, meta
        assert extras.digest_of(body, extras.LEGACY) == legacy and extras.digest_of(body, extras.RFC8785) == modern


def test_a_number_with_no_double_is_stored_as_null_in_both_schemes():
    for scheme in extras.SCHEMES:
        _, body = extras.tool_extras([annotated("x", _meta={"huge": 10 ** 400, "inf": float("inf")})], scheme)
        assert body["tools"]["x"]["_meta"] == {"huge": None, "inf": None}


def test_an_unknown_scheme_is_refused():
    with pytest.raises(ValueError, match="extras scheme"):
        extras.digest_of({"tools": {}}, "sorted-keys")


@pytest.mark.skipif(not VECTORS.is_file(), reason="mcp-tool-set-digest vectors not in this checkout")
def test_rfc8785_agrees_with_the_specification_where_the_bounded_form_does_not_apply():
    """mcp-tool-set-digest/1 section 8: under rfc8785 HISTOR's extras digest is the specification's on every
    vector except the limits of section 8.1 (and the entries the specification gives no digest)."""
    bounded = {"number-overflow-in-extras", "lone-surrogate-in-extras", "duplicate-names-both-with-extras",
               "one-bad-among-good", "tool-is-string", "tool-is-null", "tool-is-array", "name-missing",
               "name-empty", "name-not-string"}
    vectors = json.loads(VECTORS.read_text("utf-8"))["vectors"]
    assert bounded < {vector["id"] for vector in vectors}
    for vector in vectors:
        if vector["id"] in bounded:
            continue
        want = vector["extras"].get("digest") if "extras" in vector else None
        got = extras.tool_extras(json.loads(vector["tools"]), extras.RFC8785)[0]
        assert got == want, vector["id"]


# --- moving a log -----------------------------------------------------------------------------------------

def test_rekey_moves_every_reference_and_records_no_change(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/p"] = [annotated("pay", "Pay.", _meta={"price": 1.0}, annotations={"readOnlyHint": False})]
    mcp.tools["/q"] = [annotated("read", "Read.", annotations={"readOnlyHint": True})]
    a, b = make_target("io.example/pay", "/p"), make_target("io.example/read", "/q")
    world["targets"] += [a, b]
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/p"] = [annotated("pay", "Pay.", _meta={"price": 2.0}, annotations={"readOnlyHint": False})]
    s.crawler.run()
    legacy_now, legacy_changes, legacy_observed, scheme = rows(s, a.id)
    assert scheme == "legacy" and len(legacy_changes) == 1 and len(legacy_observed) == 2
    plain_before = rows(s, b.id)[0]

    result = extras_rekey.rekey(s.store.db, extras.RFC8785)
    assert result["from"] == "legacy" and result["to"] == "rfc8785" and result["moved"] == 2
    now, changes, observed, scheme = rows(s, a.id)
    assert scheme == "rfc8785"
    want = extras.tool_extras(mcp.tools["/p"], extras.RFC8785)[0]
    assert now == want and observed[-1] == want and changes[0]["to_extras"] == want
    assert changes[0]["from_extras"] == observed[0] != legacy_observed[0]
    assert len(changes) == 1                                  # nothing recorded by the move
    assert rows(s, b.id)[0] == plain_before                   # no fraction, no astral name: the same digest

    clock.advance(days=1)
    s.crawler.run()                                           # the next read agrees with the moved record
    assert rows(s, a.id)[1] == changes and rows(s, a.id)[0] == want

    clock.advance(days=1)
    mcp.tools["/p"] = [annotated("pay", "Pay.", _meta={"price": 2.5}, annotations={"readOnlyHint": False})]
    s.crawler.run()                                           # a real change still is one
    assert len(rows(s, a.id)[1]) == 2

    assert extras_rekey.stale(s.store.db) and extras_rekey.prune(s.store.db) == 2
    assert extras_rekey.stale(s.store.db) == []
    assert all(s.store.blob_text(d, "toolextras") for d in rows(s, a.id)[2])


def test_an_observation_digested_the_old_way_after_the_switch_is_not_a_change(world):
    """A crawl that read the scheme just before the switch writes the old digest once; the next read,
    under the new scheme, sees the same body and records nothing."""
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/p"] = [annotated("pay", "Pay.", _meta={"price": 1.0})]
    target = make_target("io.example/pay", "/p")
    world["targets"].append(target)
    s.crawler.run()
    with s.store.db.transaction() as tx:                      # only the scheme moves, no re-keying yet
        Store.set_meta(tx, extras.META_KEY, extras.RFC8785)
    clock.advance(days=1)
    s.crawler.run()
    now, changes, observed, _ = rows(s, target.id)
    assert changes == [] and now == extras.tool_extras(mcp.tools["/p"], extras.RFC8785)[0] != observed[0]


def test_server_detail_identifies_the_digest_scheme_during_rekey(world):
    from fastapi.testclient import TestClient

    from histor.app import create_app

    s, mcp = world["services"], world["mcp"]
    mcp.tools["/p"] = [annotated("pay", _meta={"price": 1.0})]
    target = make_target("io.example/pay", "/p")
    world["targets"].append(target)
    s.crawler.run()
    # Rekey changes the setting before it remaps existing target digests.
    s.store.set_meta_value(extras.META_KEY, extras.RFC8785)

    with TestClient(create_app(s)) as client:
        path = f"/api/v1/servers/{target.id}"
        old = client.get(path).json()
        assert old["server"]["toolExtrasDigestScheme"] == extras.LEGACY
        assert old["server"]["toolExtrasDigest"] == extras.digest_of(old["toolExtras"], extras.LEGACY)

        extras_rekey.rekey(s.store.db, extras.RFC8785)
        new = client.get(path).json()
        assert new["server"]["toolExtrasDigestScheme"] == extras.RFC8785
        assert new["server"]["toolExtrasDigest"] == extras.digest_of(new["toolExtras"], extras.RFC8785)
        assert old["server"]["toolExtrasDigest"] != new["server"]["toolExtrasDigest"]
        assert old["toolExtras"] == new["toolExtras"]


def test_the_way_back(world):
    s, mcp = world["services"], world["mcp"]
    mcp.tools["/p"] = [annotated("pay", "Pay.", _meta={"price": 1.0, "\U0001F600": True})]
    target = make_target("io.example/pay", "/p")
    world["targets"].append(target)
    s.crawler.run()
    original = rows(s, target.id)
    extras_rekey.rekey(s.store.db, extras.RFC8785)
    assert rows(s, target.id)[0] != original[0]
    again = extras_rekey.rekey(s.store.db, extras.RFC8785)    # safe to repeat: nothing left to move
    assert again["moved"] == 0
    extras_rekey.rekey(s.store.db, extras.LEGACY)
    assert rows(s, target.id) == original
