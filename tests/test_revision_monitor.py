"""SUPP-06 monitor cases (e) forked tip and (f) unsigned digest."""

from __future__ import annotations

from histor import revision, revision_monitor, signed_revisions
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


def test_forked_tips_detects_siblings():
    d1, d2 = "sha256:aa", "sha256:bb"
    forks = revision_monitor.forked_tips([
        {"revision": 2, "manifestDigest": d1, "previousManifestDigest": "sha256:00"},
        {"revision": 2, "manifestDigest": d2, "previousManifestDigest": "sha256:00"},
    ])
    assert len(forks) == 1
    assert {t["manifestDigest"] for t in forks[0]["tips"]} == {d1, d2}
    assert revision_monitor.forked_tips([
        {"revision": 1, "manifestDigest": d1},
        {"revision": 2, "manifestDigest": d2, "previousManifestDigest": d1},
    ]) == []


def test_check_records_unsigned_digest_alert(world):
    services = world["services"]
    store = services.store
    with store.db.transaction() as tx:
        tx.execute(
            "INSERT INTO targets(id, name, endpoint, transport, registry, first_listed, last_listed) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            ("abcd1234abcd1234", "io.github.alexar76/histor", "pypi:aimarket-histor",
             "stdio", "official", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    statement = revision.build_statement(
        server="io.github.alexar76/histor", tools=TOOLS, identity=SIGNER,
        previous=None, server_version="0.7.0", source="pypi:aimarket-histor==0.7.0",
    )
    env = revision.assemble_envelope(statement, BUNDLE)
    assert RevisionIngest(store, services.key, (), verify_signatures=False).accept_envelope(
        env, source_url="https://example.test/index.json",
    ) == "accepted"

    other = [{"name": "search", "description": "changed",
              "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}}]
    answer = services.checker.check(
        {"name": "io.github.alexar76/histor", "tools": other},
        allow_scan=False,
    )
    assert answer["signedRevision"]["match"] == "different"
    assert "unsigned_digest" in answer["signedRevision"]["alerts"]
    from histor.security_events import events
    rows = events(store, after=0, limit=50)
    kinds = [r["event"]["kind"] for r in rows]
    assert revision_monitor.KIND_UNSIGNED in kinds
