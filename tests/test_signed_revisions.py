"""Publisher-signed revisions: ingest into the revision tree and surface on /check."""

from __future__ import annotations

import json

from awr import canonical_sri

from histor import revision, signed_revisions
from histor.logbook import verify_document
from histor.signed_revisions import RevisionIngest

SIGNER = "https://github.com/alexar76/histor/.github/workflows/sign-manifest-revision.yml@refs/heads/main"
TOOLS = [{"name": "search", "description": "Search the docs.",
          "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]
BUNDLE = {
    "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
    "verificationMaterial": {"tlogEntries": [{
        "logIndex": "3803861704", "logId": {"keyId": "abc="}, "integratedTime": "1794370000",
        "inclusionProof": {"logIndex": "120", "rootHash": "cm9vdA==", "treeSize": "121", "hashes": ["aA=="],
                           "checkpoint": {"envelope": "rekor.sigstore.dev - 1\n121\ncm9vdA==\n"}},
    }]},
    "messageSignature": {"messageDigest": {"algorithm": "SHA2_256", "digest": "ZA=="}, "signature": "c2ln"},
}


def _envelope(tools=TOOLS, revision_n=1, previous=None):
    statement = revision.build_statement(
        server="io.github.alexar76/histor", tools=tools, identity=SIGNER,
        previous=previous, server_version="0.7.0", source="pypi:aimarket-histor==0.7.0",
    )
    assert statement["revision"] == revision_n
    return revision.assemble_envelope(statement, BUNDLE)


def test_ingest_accepts_envelope_appends_leaf_and_answers_check(world):
    services = world["services"]
    store = services.store
    with store.db.transaction() as tx:
        tx.execute(
            "INSERT INTO targets(id, name, endpoint, transport, registry, first_listed, last_listed) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            ("abcd1234abcd1234", "io.github.alexar76/histor", "pypi:aimarket-histor",
             "stdio", "official", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    ingest = RevisionIngest(store, services.key, (), verify_signatures=False)
    env = _envelope()
    assert ingest.accept_envelope(env, source_url="https://example.test/index.json") == "accepted"
    assert ingest.accept_envelope(env, source_url="https://example.test/index.json") == "duplicate"
    ingest.journal.publish_sth()

    row = signed_revisions.latest_for_server(store, "io.github.alexar76/histor")
    assert row is not None
    assert row["revision"] == 1
    assert row["log_index"] == "3803861704"
    assert row["leaf_index"] == 0
    assert row["target_id"] == "abcd1234abcd1234"
    assert row["manifest_digest"] == revision.digests(TOOLS)[0]
    assert row["envelope_digest"] == canonical_sri(json.loads(row["body"]))

    with store.db.read() as tx:
        sth_row = tx.one("SELECT body FROM revision_sths ORDER BY tree_size DESC LIMIT 1")
        leaf = tx.one("SELECT hash FROM revision_log_nodes WHERE level=0 AND idx=0")
    sth = json.loads(sth_row["body"])
    assert sth["treeSize"] == 1
    assert verify_document(sth, services.key.did, signed_revisions.STH_TYPE)
    assert len(leaf["hash"]) == 64

    answer = services.checker.check(
        {"name": "io.github.alexar76/histor", "tools": TOOLS},
        allow_scan=False,
    )
    assert answer["signedRevision"]["revision"] == 1
    assert answer["signedRevision"]["logIndex"] == "3803861704"
    assert answer["signedRevision"]["match"] == "same"
    assert "revision 1" in answer["note"]

    other = [{"name": "search", "description": "changed",
              "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}}]
    answer2 = services.checker.check(
        {"name": "io.github.alexar76/histor", "tools": other},
        allow_scan=False,
    )
    assert answer2["signedRevision"]["match"] == "different"


def test_parse_sources_default_and_dash():
    assert signed_revisions.parse_sources(None)[0][0] == "io.github.alexar76/histor"
    assert signed_revisions.parse_sources("-") == ()
    pairs = signed_revisions.parse_sources(
        "io.github.alexar76/warden=https://raw.githubusercontent.com/alexar76/warden/manifest-revisions/index.json"
    )
    assert pairs == (("io.github.alexar76/warden",
                      "https://raw.githubusercontent.com/alexar76/warden/manifest-revisions/index.json"),)
