"""Appending labels to the log, and signing tree heads over it.

A signed tree head (STH) is the log's promise: "the first N labels are exactly these, and their
Merkle root is R". Anyone who saved yesterday's head can ask for a consistency proof to today's
and check that nothing was removed or rewritten in between — without trusting HISTOR, only its
key. That is the property that makes this a log rather than a database with a website.

STH format (``histor.sth/v1``) — the signature is Ed25519 by the issuer ``did:key`` over the
RFC 8785 bytes of every other member:

    {"type": "histor.sth/v1", "log": <did>, "treeSize": N, "rootHash": <hex>,
     "timestamp": "...Z", "signature": {"alg": "Ed25519", "verificationMethod": ..., "value": <b64url>}}
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awr import AwrError, SigningKey, canonical_sri, canonicalize, parse_did_key
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from histor import merkle
from histor.db import Tx
from histor.keys import write_head_marker
from histor.store import Store, dumps

STH_TYPE = "histor.sth/v1"


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verification_method(did: str) -> str:
    return f"{did}#{did.split(':')[-1]}"


def sign_document(key: SigningKey, body: dict[str, Any]) -> dict[str, Any]:
    """Sign every member of *body*; ``type`` inside the bytes stops cross-type replay."""
    if "type" not in body:
        raise ValueError("a signed HISTOR document must carry its type")
    signature = key.sign(canonicalize(body))
    return {**body, "signature": {"alg": "Ed25519", "verificationMethod": verification_method(key.did),
                                  "value": b64url(signature)}}


def verify_document(document: dict[str, Any], did: str, expected_type: str) -> bool:
    if document.get("type") != expected_type:
        return False
    sig = document.get("signature") or {}
    body = {k: v for k, v in document.items() if k != "signature"}
    try:
        public = Ed25519PublicKey.from_public_bytes(parse_did_key(did))
        public.verify(b64url_decode(str(sig.get("value", ""))), canonicalize(body))
        return True
    except (InvalidSignature, AwrError, ValueError, TypeError):
        return False


class Logbook:
    def __init__(self, store: Store, key: SigningKey, marker_path: Path | None = None) -> None:
        self.store = store
        self.key = key
        self.marker_path = marker_path

    def append(self, tx: Tx, document: dict[str, Any], *, target_id: str | None) -> dict[str, Any]:
        """Append one secured label inside *tx*; returns the row as stored."""
        tx.lock_log()
        subject = document["credentialSubject"]
        detail = subject["mcpTrustLabel"]
        index = Store.append_leaf(tx, merkle.leaf_hash(canonicalize(document)))
        row = {
            "id": document["id"],
            "target_id": target_id,
            "method": subject["method"]["id"],
            "verdict": subject["verdict"],
            "subject": subject["verifiedWork"]["digestSRI"],
            "observed_at": detail["observedAt"],
            "issued_at": document["validFrom"],
            "digest": canonical_sri(document),
            "leaf_index": index,
        }
        tx.execute(
            "INSERT INTO labels(id, target_id, method, verdict, subject, observed_at, issued_at, digest, leaf_index, body) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*row.values(), dumps(document)),
        )
        return row

    def publish_sth(self, *, timestamp: str | None = None) -> dict[str, Any] | None:
        """Sign a head over the current tree if it grew. Returns the latest head either way."""
        with self.store.db.transaction() as tx:
            tx.lock_log()
            size = Store.log_size(tx)
            last = tx.one("SELECT tree_size, body FROM sths ORDER BY tree_size DESC LIMIT 1")
            if size == 0:
                return None
            if last and int(last["tree_size"]) == size:
                return json.loads(last["body"])
            root = merkle.tree_root(size, Store.reader(tx))
            sth = sign_document(self.key, {
                "type": STH_TYPE,
                "log": self.key.did,
                "treeSize": size,
                "rootHash": root.hex(),
                "timestamp": timestamp or utcnow(),
            })
            tx.execute("INSERT INTO sths(tree_size, root, timestamp, body) VALUES(?, ?, ?, ?)",
                       (size, root.hex(), sth["timestamp"], dumps(sth)))
        if self.marker_path is not None:
            # After the commit: the marker may lag the database, never lead it.
            write_head_marker(self.marker_path, size, root.hex())
        return sth

    # -- proofs ---------------------------------------------------------------------

    def inclusion(self, leaf_index: int, tree_size: int) -> list[str]:
        with self.store.db.read() as tx:
            if tree_size > Store.log_size(tx):
                raise ValueError("tree_size is larger than the log")
            return [h.hex() for h in merkle.inclusion_proof(leaf_index, tree_size, Store.reader(tx))]

    def consistency(self, first: int, second: int) -> list[str]:
        with self.store.db.read() as tx:
            if second > Store.log_size(tx):
                raise ValueError("second is larger than the log")
            return [h.hex() for h in merkle.consistency_proof(first, second, Store.reader(tx))]

    def root_at(self, tree_size: int) -> str:
        with self.store.db.read() as tx:
            return merkle.tree_root(tree_size, Store.reader(tx)).hex()
