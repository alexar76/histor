#!/usr/bin/env python3
"""Generate the normative vectors for docs/spec/transparency-log.md (format histor-tlog/1).

Deterministic: a fixed test seed, fixed leaf documents, fixed timestamps. Run from the
repository root with the project environment:

    uv run python test-vectors/generate.py

The seed below is for vectors only. A log MUST NOT use it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from awr import SigningKey, canonical_sri, canonicalize, parse_did_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from histor import merkle  # noqa: E402
from histor.checkpoint import COSIGNATURE_V1_TYPE, LOG_KEY_TYPE, checkpoint_text, cosign, sign_checkpoint, vkey  # noqa: E402
from histor.logbook import STH_TYPE, b64url, sign_document, verification_method  # noqa: E402

SEED = hashlib.sha256(b"histor-tlog/1 test vectors").digest()  # 32 bytes, fixed
WITNESS_SEED = hashlib.sha256(b"histor-tlog/2 witness test vectors").digest()
ORIGIN = "example.com/histor-tlog-vectors"
WITNESS_NAME = "example.com/witness"
COSIGN_TIME = 1761800000
SIZES = 8

OUT = Path(__file__).resolve().parent / "vectors.json"


def leaf_documents() -> list[dict]:
    """Small label-shaped documents. The format only requires JSON; these mimic labels."""
    docs = []
    for i in range(SIZES):
        docs.append({
            "id": f"urn:uuid:00000000-0000-4000-8000-{i:012d}",
            "type": ["VerifiableCredential", "VerificationVerdict"],
            "issuer": "did:key:TEST",
            "credentialSubject": {
                "verifiedWork": {"id": f"urn:awr:mtl:1:subject:sha256:{'%064x' % (i + 1)}"},
                "verdict": "pass" if i % 3 else "inconclusive",
                "method": {"id": "urn:awr:mtl:1:method:tool-set-observation"},
                "mcpTrustLabel": {"profile": "MTL/1", "observedAt": f"2026-10-30T00:00:{i:02d}Z",
                                   "unicode": "résumé ✓ 𝄞", "count": i * 7},
            },
        })
    return docs


def main() -> None:
    key = SigningKey(Ed25519PrivateKey.from_private_bytes(SEED))
    docs = leaf_documents()
    leaves = []
    hashes: list[bytes] = []
    for i, doc in enumerate(docs):
        canon = canonicalize(doc)
        lh = merkle.leaf_hash(canon)
        hashes.append(lh)
        leaves.append({
            "index": i, "document": doc, "canonical": canon.decode("utf-8"),
            "leafHash": lh.hex(), "digestSRI": canonical_sri(doc),
        })

    # an in-memory store of complete subtrees, built the way the log builds it
    nodes: dict[tuple[int, int], bytes] = {}

    def read(level: int, index: int) -> bytes:
        return nodes[(level, index)]

    for i, lh in enumerate(hashes):
        for level, idx, digest in merkle.appended_nodes(i, lh, read):
            nodes[(level, idx)] = digest

    roots = {str(n): merkle.tree_root(n, read).hex() for n in range(1, SIZES + 1)}
    roots["0"] = merkle.EMPTY_ROOT.hex()

    inclusion = []
    for size in range(1, SIZES + 1):
        for index in range(size):
            proof = merkle.inclusion_proof(index, size, read)
            assert merkle.verify_inclusion(hashes[index], index, size, proof, bytes.fromhex(roots[str(size)]))
            inclusion.append({"leafIndex": index, "treeSize": size, "proof": [p.hex() for p in proof]})

    consistency = []
    for first in range(1, SIZES + 1):
        for second in range(first, SIZES + 1):
            proof = merkle.consistency_proof(first, second, read)
            assert merkle.verify_consistency(first, second, proof, bytes.fromhex(roots[str(first)]),
                                             bytes.fromhex(roots[str(second)]))
            consistency.append({"first": first, "second": second, "proof": [p.hex() for p in proof]})

    heads = {}
    for size, ts in ((5, "2026-10-30T00:00:05Z"), (8, "2026-10-30T00:00:08Z")):
        heads[str(size)] = sign_document(key, {
            "type": STH_TYPE, "log": key.did, "treeSize": size, "rootHash": roots[str(size)], "timestamp": ts,
        })

    # negatives — each MUST be rejected
    tampered = json.loads(json.dumps(docs[2]))
    tampered["credentialSubject"]["verdict"] = "fail"
    wrong_type = dict(heads["8"]); wrong_type["type"] = "histor.check/v1"
    moved = dict(heads["5"]); moved["treeSize"] = 8; moved["rootHash"] = roots["8"]
    negatives = [
        {"name": "tampered-leaf", "document": tampered, "claimedLeafHash": leaves[2]["leafHash"],
         "expect": "leaf hash does not match: SHA-256(0x00 || JCS(document)) differs"},
        {"name": "sth-type-changed-after-signing", "document": wrong_type,
         "expect": "signature invalid: type is inside the signed bytes"},
        {"name": "sth-signature-moved-to-another-size", "document": moved,
         "expect": "signature invalid: treeSize and rootHash are inside the signed bytes"},
    ]

    # histor-tlog/2: the head at size 8 as a C2SP checkpoint note signed by the test key, and a
    # cosignature/v1 on it by a witness test key at a fixed time.
    witness_key = SigningKey(Ed25519PrivateKey.from_private_bytes(WITNESS_SEED))
    cp_text = checkpoint_text(ORIGIN, SIZES, bytes.fromhex(roots[str(SIZES)]))
    cp_note = sign_checkpoint(key, ORIGIN, SIZES, bytes.fromhex(roots[str(SIZES)]))
    cosig = cosign(witness_key, WITNESS_NAME, cp_text, COSIGN_TIME)
    checkpoints = {
        "format": "c2sp.org/tlog-checkpoint + c2sp.org/signed-note + c2sp.org/tlog-cosignature",
        "origin": ORIGIN,
        "logVkey": vkey(ORIGIN, LOG_KEY_TYPE, key.public_key_bytes),
        "witness": {"name": WITNESS_NAME, "seedHex": WITNESS_SEED.hex(),
                    "vkey": vkey(WITNESS_NAME, COSIGNATURE_V1_TYPE, witness_key.public_key_bytes),
                    "timestamp": COSIGN_TIME},
        "checkpointText": cp_text,
        "checkpointNote": cp_note,
        "cosignatureLine": cosig.render(),
        "cosignedNote": cp_note + cosig.render(),
        "cosignatureMessage": ("cosignature/v1\ntime %d\n" % COSIGN_TIME) + cp_text,
    }

    out = {
        "format": "histor-tlog/2",
        "checkpoints": checkpoints,
        "spec": "docs/spec/transparency-log.md",
        "testKey": {"seedHex": SEED.hex(), "did": key.did, "publicKeyHex": parse_did_key(key.did).hex(),
                    "verificationMethod": verification_method(key.did),
                    "note": "vectors only; a log MUST NOT use this key"},
        "leafHashRule": "SHA-256(0x00 || JCS(document))",
        "nodeHashRule": "SHA-256(0x01 || left || right)",
        "emptyRoot": merkle.EMPTY_ROOT.hex(),
        "leaves": leaves,
        "roots": roots,
        "inclusion": inclusion,
        "consistency": consistency,
        "signedTreeHeads": heads,
        "negative": negatives,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(inclusion)} inclusion, {len(consistency)} consistency proofs)")


if __name__ == "__main__":
    main()
