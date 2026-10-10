"""The receipts log (histor.receipts): anchors in, proofs out, and nothing else changes.

Every proof here is checked the way an outside verifier would — recomputing the root from the
leaf and the audit path (RFC 9162 §2.1.3.2) and checking the head's signature — not by asking
HISTOR whether HISTOR is right.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from awr import SigningKey, canonicalize
from fastapi.testclient import TestClient

from histor import merkle
from histor.app import create_app
from histor.logbook import verify_document
from histor.receipts import RECEIPT_STH_TYPE, AnchorRefused, ReceiptLog, sign_anchor

HUB = SigningKey.from_seed(hashlib.sha256(b"histor-receipts-test-hub").digest())
STRANGER = SigningKey.from_seed(hashlib.sha256(b"histor-receipts-test-stranger").digest())


def sri(n: int) -> str:
    import base64

    return "sha256-" + base64.b64encode(hashlib.sha256(f"receipt-{n}".encode()).digest()).decode()


def now_ts(delta: timedelta = timedelta()) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def log(world):
    services = world["services"]
    return ReceiptLog(services.store, services.key, {HUB.did})


def verify_inclusion(proof: dict) -> bool:
    """RFC 9162 §2.1.3.2, done by hand from the leaf bytes."""
    index, size = proof["leaf_index"], proof["tree_size"]
    node = merkle.leaf_hash(canonicalize(proof["anchor"]))
    fn, sn = index, size - 1
    for sibling_hex in proof["audit_path"]:
        sibling = bytes.fromhex(sibling_hex)
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            node = merkle.node_hash(sibling, node)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            node = merkle.node_hash(node, sibling)
        fn >>= 1
        sn >>= 1
    return sn == 0 and node.hex() == proof["sth"]["rootHash"]


class TestAdmission:
    def test_a_signed_anchor_is_logged_and_provable(self, log, world):
        results = log.submit([sign_anchor(HUB, receipt_digest=sri(1), issued_at=now_ts())])
        assert results == [{"receipt_digest": sri(1), "status": "logged", "leaf_index": 0}]
        proof = log.proof(sri(1))
        assert proof["anchor"]["issuer"] == HUB.did
        sth = proof["sth"]
        assert sth["type"] == RECEIPT_STH_TYPE and sth["treeSize"] == 1
        assert verify_document(sth, world["services"].key.did, RECEIPT_STH_TYPE)
        assert verify_inclusion(proof)

    @pytest.mark.parametrize("make, reason", [
        (lambda: sign_anchor(STRANGER, receipt_digest=sri(2), issued_at=now_ts()), "not accepted"),
        (lambda: {**sign_anchor(HUB, receipt_digest=sri(2), issued_at=now_ts()), "receiptDigest": sri(3)}, "signature"),
        (lambda: sign_anchor(HUB, receipt_digest="sha256-short", issued_at=now_ts()), "SRI"),
        (lambda: sign_anchor(HUB, receipt_digest=sri(2), issued_at=now_ts(timedelta(hours=1))), "window"),
        (lambda: sign_anchor(HUB, receipt_digest=sri(2), issued_at=now_ts(-timedelta(days=40))), "window"),
        (lambda: {**sign_anchor(HUB, receipt_digest=sri(2), issued_at=now_ts()), "price": 1}, "exactly"),
    ])
    def test_refusals_name_the_reason_and_log_nothing(self, log, make, reason):
        [result] = log.submit([make()])
        assert result["status"] == "refused" and reason in result["reason"]
        assert log.latest_sth() is None

    def test_one_bad_anchor_never_blocks_a_batch(self, log):
        good = [sign_anchor(HUB, receipt_digest=sri(n), issued_at=now_ts()) for n in range(3)]
        bad = sign_anchor(STRANGER, receipt_digest=sri(9), issued_at=now_ts())
        results = log.submit([good[0], bad, good[1], good[2]])
        assert [r["status"] for r in results] == ["logged", "refused", "logged", "logged"]
        assert [r.get("leaf_index") for r in results] == [0, None, 1, 2]

    def test_resubmitting_is_idempotent_and_a_digest_belongs_to_one_issuer(self, world):
        services = world["services"]
        log = ReceiptLog(services.store, services.key, {HUB.did, STRANGER.did})
        first = log.submit([sign_anchor(HUB, receipt_digest=sri(1), issued_at=now_ts())])
        again = log.submit([sign_anchor(HUB, receipt_digest=sri(1), issued_at=now_ts())])
        theft = log.submit([sign_anchor(STRANGER, receipt_digest=sri(1), issued_at=now_ts())])
        assert first[0]["status"] == "logged"
        assert again[0] == {"receipt_digest": sri(1), "status": "duplicate", "leaf_index": 0}
        assert theft[0]["status"] == "refused" and "another issuer" in theft[0]["reason"]
        assert log.latest_sth()["treeSize"] == 1

    def test_batch_bounds(self, log):
        with pytest.raises(AnchorRefused):
            log.submit([])
        with pytest.raises(AnchorRefused):
            log.submit([sign_anchor(HUB, receipt_digest=sri(0), issued_at=now_ts())] * 101)


class TestTheLogItself:
    def test_every_anchor_verifies_against_every_later_head(self, log):
        for n in range(13):
            log.submit([sign_anchor(HUB, receipt_digest=sri(n), issued_at=now_ts())])
        for n in range(13):
            for size in range(n + 1, 14):
                assert verify_inclusion(log.proof(sri(n), size)), (n, size)

    def test_consistency_between_heads(self, log):
        for n in range(9):
            log.submit([sign_anchor(HUB, receipt_digest=sri(n), issued_at=now_ts())])
        for first in range(1, 10):
            proof = [bytes.fromhex(h) for h in log.consistency(first, 9)]
            old, new = log.sth(first)["rootHash"], log.sth(9)["rootHash"]
            assert merkle.verify_consistency(first, 9, proof, bytes.fromhex(old), bytes.fromhex(new))

    def test_it_does_not_touch_the_label_log(self, log, world):
        store = world["services"].store
        before = store.tree_size(), store.latest_sth()
        log.submit([sign_anchor(HUB, receipt_digest=sri(n), issued_at=now_ts()) for n in range(5)])
        assert (store.tree_size(), store.latest_sth()) == before


class TestHttp:
    def test_submit_and_prove_over_http(self, world):
        services = world["services"]
        services.receipts = ReceiptLog(services.store, services.key, {HUB.did})
        with TestClient(create_app(services)) as client:
            anchor = sign_anchor(HUB, receipt_digest=sri(7), issued_at=now_ts())
            r = client.post("/api/v1/receipts/anchors", json={"anchors": [anchor]})
            assert r.status_code == 200, r.text
            assert r.json()["results"][0]["status"] == "logged"
            assert r.json()["sth"]["treeSize"] == 1
            proof = client.get("/api/v1/receipts/proof", params={"digest": sri(7)}).json()
            assert verify_inclusion(proof)
            assert client.get("/api/v1/receipts/sth").json()["treeSize"] == 1
            assert client.get("/api/v1/receipts/proof", params={"digest": sri(8)}).status_code == 404

    def test_an_unencoded_plus_still_finds_the_proof(self, world):
        services = world["services"]
        services.receipts = ReceiptLog(services.store, services.key, {HUB.did})
        # a digest whose base64 carries a '+', pasted into a URL without percent-encoding
        n = next(i for i in range(200) if "+" in sri(i))
        with TestClient(create_app(services)) as client:
            client.post("/api/v1/receipts/anchors",
                        json={"anchors": [sign_anchor(HUB, receipt_digest=sri(n), issued_at=now_ts())]})
            r = client.get(f"/api/v1/receipts/proof?digest={sri(n)}")
            assert r.status_code == 200, r.text
            assert r.json()["anchor"]["receiptDigest"] == sri(n)

    def test_a_log_with_no_issuers_accepts_nothing(self, world):
        services = world["services"]
        services.receipts = ReceiptLog(services.store, services.key, set())
        with TestClient(create_app(services)) as client:
            anchor = sign_anchor(HUB, receipt_digest=sri(1), issued_at=now_ts())
            assert client.post("/api/v1/receipts/anchors", json={"anchors": [anchor]}).status_code == 503


def test_the_settings_read_the_issuer_list(settings, monkeypatch):
    from histor.config import load_settings

    monkeypatch.setenv("HISTOR_RECEIPT_ISSUERS", f" {HUB.did} ,{STRANGER.did},")
    assert load_settings().receipt_issuers == (HUB.did, STRANGER.did)
    assert replace(settings).receipt_issuers == ()
