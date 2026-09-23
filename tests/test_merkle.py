"""The log's arithmetic against a naive RFC 9162 MTH, for every size and index up to 70."""

from __future__ import annotations

import pytest

from histor import merkle


def naive_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        return merkle.EMPTY_ROOT
    if len(leaves) == 1:
        return leaves[0]
    k = merkle._split(len(leaves))
    return merkle.node_hash(naive_root(leaves[:k]), naive_root(leaves[k:]))


@pytest.fixture(scope="module")
def tree():
    store: dict[tuple[int, int], bytes] = {}
    leaves = []
    for i in range(70):
        leaf = merkle.leaf_hash(b"label-%d" % i)
        leaves.append(leaf)
        for level, idx, h in merkle.appended_nodes(i, leaf, lambda lv, j: store[(lv, j)]):
            store[(level, idx)] = h
    return leaves, (lambda lv, j: store[(lv, j)])


def test_roots_match_the_naive_definition(tree):
    leaves, read = tree
    for n in range(0, 71):
        assert merkle.tree_root(n, read) == naive_root(leaves[:n])


def test_every_inclusion_proof_verifies_and_rejects_a_wrong_root(tree):
    leaves, read = tree
    for n in range(1, 71):
        root = merkle.tree_root(n, read)
        for m in range(n):
            proof = merkle.inclusion_proof(m, n, read)
            assert merkle.verify_inclusion(leaves[m], m, n, proof, root)
            assert not merkle.verify_inclusion(leaves[m], m, n, proof, merkle.EMPTY_ROOT)
            if n > 1:
                assert not merkle.verify_inclusion(leaves[(m + 1) % n], m, n, proof, root)


def test_every_consistency_proof_verifies(tree):
    leaves, read = tree
    for n in range(1, 71):
        new_root = merkle.tree_root(n, read)
        for m in range(1, n + 1):
            proof = merkle.consistency_proof(m, n, read)
            old_root = naive_root(leaves[:m])
            assert merkle.verify_consistency(m, n, proof, old_root, new_root)
            if m < n:
                assert not merkle.verify_consistency(m, n, proof, merkle.leaf_hash(b"forged"), new_root)


def test_proof_arguments_are_bounded(tree):
    _, read = tree
    with pytest.raises(ValueError):
        merkle.inclusion_proof(5, 5, read)
    with pytest.raises(ValueError):
        merkle.consistency_proof(0, 5, read)
