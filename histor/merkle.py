"""The append-only Merkle log, as RFC 9162 (Certificate Transparency v2) defines it.

Leaves are labels. A leaf's input is the RFC 8785 canonical form of the secured label
document, so anyone holding a label can recompute its leaf hash without asking us:

    leaf hash = SHA-256(0x00 || JCS(label))
    node hash = SHA-256(0x01 || left || right)

Only *complete* subtrees are stored (``node(level, index)`` covers leaves
``[index·2^level, (index+1)·2^level)``). Every left child in RFC 9162's recursion is such a
subtree, so any tree head, inclusion proof or consistency proof is O(log n) reads plus the
right spine — nothing is recomputed from the leaves.

The verify functions are here too, so the test suite checks proofs with the same algorithm a
stranger would run, and so a client can import them instead of trusting our answer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

EMPTY_ROOT = hashlib.sha256(b"").digest()

NodeReader = Callable[[int, int], bytes]  # (level, index) -> hash of a complete subtree


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    """Largest power of two strictly less than *n* (n >= 2)."""
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def appended_nodes(index: int, leaf: bytes, read: NodeReader) -> list[tuple[int, int, bytes]]:
    """Every complete-subtree node that appending *leaf* at *index* completes, leaf included."""
    out = [(0, index, leaf)]
    level, current, position = 0, leaf, index
    while position % 2 == 1:
        sibling = read(level, position - 1)
        current = node_hash(sibling, current)
        level += 1
        position //= 2
        out.append((level, position, current))
    return out


def subtree_root(start: int, end: int, read: NodeReader) -> bytes:
    """MTH(D[start:end]) where every left subtree of the recursion is stored."""
    n = end - start
    if n == 0:
        return EMPTY_ROOT
    if n & (n - 1) == 0 and start % n == 0:
        return read(n.bit_length() - 1, start // n)
    k = _split(n)
    return node_hash(subtree_root(start, start + k, read), subtree_root(start + k, end, read))


def tree_root(size: int, read: NodeReader) -> bytes:
    return subtree_root(0, size, read)


def inclusion_proof(index: int, size: int, read: NodeReader) -> list[bytes]:
    """PATH(m, D[n]) — RFC 9162 section 2.1.3.1."""
    if not 0 <= index < size:
        raise ValueError("leaf index outside the tree")

    def path(m: int, start: int, end: int) -> list[bytes]:
        n = end - start
        if n == 1:
            return []
        k = _split(n)
        if m < k:
            return path(m, start, start + k) + [subtree_root(start + k, end, read)]
        return path(m - k, start + k, end) + [subtree_root(start, start + k, read)]

    return path(index, 0, size)


def consistency_proof(first: int, second: int, read: NodeReader) -> list[bytes]:
    """PROOF(m, D[n]) — RFC 9162 section 2.1.4.1."""
    if not 0 < first <= second:
        raise ValueError("need 0 < first <= second")

    def subproof(m: int, start: int, end: int, whole: bool) -> list[bytes]:
        n = end - start
        if m == n:
            return [] if whole else [subtree_root(start, end, read)]
        k = _split(n)
        if m <= k:
            return subproof(m, start, start + k, whole) + [subtree_root(start + k, end, read)]
        return subproof(m - k, start + k, end, False) + [subtree_root(start, start + k, read)]

    return subproof(first, 0, second, True)


def verify_inclusion(leaf: bytes, index: int, size: int, proof: list[bytes], root: bytes) -> bool:
    """RFC 9162 section 2.1.3.2."""
    if not 0 <= index < size:
        return False
    fn, sn = index, size - 1
    r = leaf
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == root


def verify_consistency(first: int, second: int, proof: list[bytes], first_root: bytes, second_root: bytes) -> bool:
    """RFC 9162 section 2.1.4.2."""
    if first == second:
        return not proof and first_root == second_root
    if not 0 < first < second or not proof:
        return False
    path = list(proof)
    if first & (first - 1) == 0:  # a power of two: the old root is itself a node of the new tree
        path.insert(0, first_root)
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr = node_hash(c, fr)
            sr = node_hash(c, sr)
            if not fn & 1:
                while fn and not fn & 1:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == first_root and sr == second_root
