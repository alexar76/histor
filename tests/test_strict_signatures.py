"""Keys HISTOR did not choose are checked strictly (AWR/2 section 6.3.1 rules 1-4), 2026-10-12.

An owner's key and an issuer's did:key come from whoever presents them. `cryptography` accepts,
under the identity key, A = identity / R = base point / S = 1 for EVERY message — no secret key
exists, so anybody could sign "as" such an owner or issuer.
"""

from __future__ import annotations

import base64

import pytest
from awr import SigningKey

from histor.logbook import STH_TYPE, b64url, sign_document, verify_document
from histor.signing import strict_ed25519, verify_owner_signature

IDENTITY = bytes.fromhex("01" + "00" * 31)
BASE = bytes.fromhex("58" + "66" * 31)
ONE = (1).to_bytes(32, "little")
IDENTITY_DID = "did:key:z6MkeXATEjyXENzBXBxgC5EHk2JE5aqd7qMGGtDpLUH1e2Sj"   # AWR/2 section 6.3.1


def test_the_owner_forgery_is_refused():
    with pytest.raises(ValueError, match="owner signature is invalid"):
        verify_owner_signature(public_key_b64=base64.b64encode(IDENTITY).decode(),
                               message=b"I own this MCP server",
                               signature_b64=base64.b64encode(BASE + ONE).decode())


def test_a_document_signed_by_a_small_order_did_key_is_refused():
    body = {"type": STH_TYPE, "treeSize": 7, "rootHash": "x"}
    forged = {**body, "signature": {"alg": "Ed25519", "value": b64url(BASE + ONE)}}
    assert verify_document(forged, IDENTITY_DID, STH_TYPE) is False


def test_honest_documents_and_owners_still_verify():
    key = SigningKey.generate() if hasattr(SigningKey, "generate") else None
    if key is None:
        pytest.skip("awr SigningKey has no generate()")
    doc = sign_document(key, {"type": STH_TYPE, "treeSize": 1})
    assert verify_document(doc, key.did, STH_TYPE) is True
    assert verify_document({**doc, "treeSize": 2}, key.did, STH_TYPE) is False


def test_strict_rules_on_raw_bytes():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    k = Ed25519PrivateKey.generate()
    pk = k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    sig = k.sign(b"m")
    assert strict_ed25519(pk, sig, b"m") is True
    assert strict_ed25519(pk, IDENTITY + sig[32:], b"m") is False             # small-order R
    s_plus_l = int.from_bytes(sig[32:], "little") + 2**252 + 27742317777372353535851937790883648493
    if s_plus_l < 2**256:
        assert strict_ed25519(pk, sig[:32] + s_plus_l.to_bytes(32, "little"), b"m") is False
    assert strict_ed25519(IDENTITY, BASE + ONE, b"anything") is False
