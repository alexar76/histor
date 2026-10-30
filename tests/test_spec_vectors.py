"""The running code agrees with the normative vectors of docs/spec/transparency-log.md."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from awr import canonical_sri, canonicalize, parse_did_key

from histor import merkle
from histor.logbook import STH_TYPE, verify_document

VECTORS = Path(__file__).resolve().parents[1] / "test-vectors" / "vectors.json"


@pytest.fixture(scope="module")
def v() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_vector_file_names_this_format(v):
    assert v["format"] == "histor-tlog/2"
    assert v["emptyRoot"] == merkle.EMPTY_ROOT.hex()


def test_every_leaf_hash_and_digest_recomputes(v):
    for leaf in v["leaves"]:
        canon = canonicalize(leaf["document"])
        assert canon.decode("utf-8") == leaf["canonical"]
        assert merkle.leaf_hash(canon).hex() == leaf["leafHash"]
        assert canonical_sri(leaf["document"]) == leaf["digestSRI"]


def _tree(v):
    nodes: dict[tuple[int, int], bytes] = {}

    def read(level, index):
        return nodes[(level, index)]

    for leaf in v["leaves"]:
        for level, idx, digest in merkle.appended_nodes(leaf["index"], bytes.fromhex(leaf["leafHash"]), read):
            nodes[(level, idx)] = digest
    return read


def test_every_root_matches(v):
    read = _tree(v)
    for size, root in v["roots"].items():
        assert merkle.tree_root(int(size), read).hex() == root, size


def test_every_inclusion_proof_matches_and_verifies(v):
    read = _tree(v)
    leaves = {leaf["index"]: bytes.fromhex(leaf["leafHash"]) for leaf in v["leaves"]}
    for case in v["inclusion"]:
        proof = merkle.inclusion_proof(case["leafIndex"], case["treeSize"], read)
        assert [p.hex() for p in proof] == case["proof"], case
        root = bytes.fromhex(v["roots"][str(case["treeSize"])])
        assert merkle.verify_inclusion(leaves[case["leafIndex"]], case["leafIndex"], case["treeSize"],
                                       [bytes.fromhex(p) for p in case["proof"]], root)


def test_every_consistency_proof_matches_and_verifies(v):
    read = _tree(v)
    for case in v["consistency"]:
        proof = merkle.consistency_proof(case["first"], case["second"], read)
        assert [p.hex() for p in proof] == case["proof"], case
        assert merkle.verify_consistency(
            case["first"], case["second"], [bytes.fromhex(p) for p in case["proof"]],
            bytes.fromhex(v["roots"][str(case["first"])]), bytes.fromhex(v["roots"][str(case["second"])]),
        )


def test_signed_tree_heads_verify_under_the_test_key(v):
    did = v["testKey"]["did"]
    assert parse_did_key(did).hex() == v["testKey"]["publicKeyHex"]
    for size, head in v["signedTreeHeads"].items():
        assert head["type"] == STH_TYPE
        assert head["treeSize"] == int(size) and head["rootHash"] == v["roots"][size]
        assert verify_document(head, did, STH_TYPE)


def test_negative_vectors_are_rejected(v):
    did = v["testKey"]["did"]
    by_name = {n["name"]: n for n in v["negative"]}
    tampered = by_name["tampered-leaf"]
    assert merkle.leaf_hash(canonicalize(tampered["document"])).hex() != tampered["claimedLeafHash"]
    assert not verify_document(by_name["sth-type-changed-after-signing"]["document"], did, "histor.check/v1")
    assert not verify_document(by_name["sth-type-changed-after-signing"]["document"], did, STH_TYPE)
    assert not verify_document(by_name["sth-signature-moved-to-another-size"]["document"], did, STH_TYPE)


def test_checkpoint_vectors_verify(v):
    from histor.checkpoint import (
        VerifierKey, parse_checkpoint_text, parse_note, verify_cosignature, verify_log_signature,
    )

    c = v["checkpoints"]
    note = parse_note(c["checkpointNote"])
    assert note.text == c["checkpointText"]
    cp = parse_checkpoint_text(note.text)
    assert cp.origin == c["origin"] and cp.tree_size == 8 and cp.root.hex() == v["roots"]["8"]
    assert verify_log_signature(note, VerifierKey.parse(c["logVkey"]))
    cosigned = parse_note(c["cosignedNote"])
    assert len(cosigned.signatures) == 2
    witness = VerifierKey.parse(c["witness"]["vkey"])
    assert verify_cosignature(cosigned.text, cosigned.signatures[1], witness) == c["witness"]["timestamp"]
    assert c["cosignatureLine"] == cosigned.signatures[1].render()
