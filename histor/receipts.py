"""The receipts log — public, append-only evidence that a work receipt existed at a time.

HISTOR's first log records what MCP servers *say* about their tools. This one records what
market hubs *did*: every AWR/2 work receipt a hub issues can be anchored here, by digest.

An anchor is four facts signed by the receipt's own issuer::

    {"type": "histor.receipt-anchor/v1", "issuer": <did:key>, "receiptDigest": "sha256-…",
     "issuedAt": "…Z", "signature": {"alg": "Ed25519", "verificationMethod": …, "value": …}}

Nothing else — not the buyer, the price, the input or the output. The receipt stays with the
people it belongs to; what becomes public is that it exists, who vouched for it and when. That
is enough for the two properties a transparency log gives:

* anyone holding a receipt can get an inclusion proof that it was logged before a signed tree
  head, and so show a third party it is not a later fabrication;
* the issuer cannot quietly deny, backdate or replace a receipt once anchored, because the
  heads are signed by HISTOR and consistency between any two of them is provable (RFC 9162).

Anchors are accepted only from issuers the operator lists (``HISTOR_RECEIPT_ISSUERS``) and only
over the issuer's own signature, so HISTOR never has to take a hub's word for anything and an
anonymous caller cannot fill the log. A second tree with its own heads keeps the label log's
promise ("the first N labels are exactly these") unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from awr import SigningKey, canonicalize

from histor import merkle
from histor.db import RECEIPT_LOG_LOCK
from histor.logbook import sign_document, utcnow, verify_document
from histor.store import Store, dumps

ANCHOR_TYPE = "histor.receipt-anchor/v1"
RECEIPT_STH_TYPE = "histor.receipts-sth/v1"
NODES = "receipt_log_nodes"
MAX_BATCH = 100
# An issuer may flush a backlog after an outage, but an anchor is a claim about "when", so the
# window is bounded both ways: nothing from the future, nothing older than a month.
MAX_FUTURE = timedelta(minutes=5)
MAX_AGE = timedelta(days=30)

_SRI = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AnchorRefused(ValueError):
    pass


def anchor_body(*, issuer: str, receipt_digest: str, issued_at: str) -> dict[str, Any]:
    return {"type": ANCHOR_TYPE, "issuer": issuer, "receiptDigest": receipt_digest, "issuedAt": issued_at}


def sign_anchor(key: SigningKey, *, receipt_digest: str, issued_at: str) -> dict[str, Any]:
    """What an issuer submits. Exposed for the issuing side and for tests."""
    return sign_document(key, anchor_body(issuer=key.did, receipt_digest=receipt_digest, issued_at=issued_at))


class ReceiptLog:
    def __init__(self, store: Store, key: SigningKey, issuers: set[str] | frozenset[str]) -> None:
        self.store = store
        self.key = key
        self.issuers = frozenset(issuers)

    @property
    def open(self) -> bool:
        return bool(self.issuers)

    # -- admission ------------------------------------------------------------------

    def _check(self, anchor: Any, now: datetime) -> dict[str, Any]:
        if not isinstance(anchor, dict) or anchor.get("type") != ANCHOR_TYPE:
            raise AnchorRefused(f"not a {ANCHOR_TYPE} document")
        if set(anchor) != {"type", "issuer", "receiptDigest", "issuedAt", "signature"}:
            raise AnchorRefused("an anchor carries exactly type, issuer, receiptDigest, issuedAt and signature")
        issuer = anchor["issuer"]
        if issuer not in self.issuers:
            raise AnchorRefused("issuer is not accepted by this log")
        if not isinstance(anchor["receiptDigest"], str) or not _SRI.match(anchor["receiptDigest"]):
            raise AnchorRefused("receiptDigest must be a sha256- SRI digest")
        stamp = anchor["issuedAt"]
        if not isinstance(stamp, str) or not _TS.match(stamp):
            raise AnchorRefused("issuedAt must be RFC 3339 UTC with whole seconds")
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        if when > now + MAX_FUTURE or when < now - MAX_AGE:
            raise AnchorRefused("issuedAt is outside the window this log accepts")
        if not verify_document(anchor, issuer, ANCHOR_TYPE):
            raise AnchorRefused("the issuer's signature does not verify")
        return anchor

    def submit(self, anchors: list[Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Log a batch. One result per anchor, in order; a refused anchor never blocks the rest."""
        now = now or datetime.now(UTC)
        if not isinstance(anchors, list) or not 1 <= len(anchors) <= MAX_BATCH:
            raise AnchorRefused(f"anchors must be a list of 1-{MAX_BATCH}")
        results: list[dict[str, Any]] = []
        accepted: list[tuple[int, dict[str, Any]]] = []
        for position, anchor in enumerate(anchors):
            try:
                accepted.append((position, self._check(anchor, now)))
                results.append({})
            except AnchorRefused as exc:
                digest = anchor.get("receiptDigest") if isinstance(anchor, dict) else None
                results.append({"receipt_digest": digest, "status": "refused", "reason": str(exc)})
        if accepted:
            logged_at = utcnow()
            with self.store.db.transaction() as tx:
                tx.lock_log(RECEIPT_LOG_LOCK)
                for position, anchor in accepted:
                    digest = anchor["receiptDigest"]
                    existing = tx.one(
                        "SELECT issuer, leaf_index FROM receipt_anchors WHERE receipt_digest=?", (digest,),
                    )
                    if existing is not None:
                        if existing["issuer"] != anchor["issuer"]:
                            results[position] = {"receipt_digest": digest, "status": "refused",
                                                 "reason": "this digest is already anchored by another issuer"}
                        else:
                            results[position] = {"receipt_digest": digest, "status": "duplicate",
                                                 "leaf_index": int(existing["leaf_index"])}
                        continue
                    index = Store.append_leaf(tx, merkle.leaf_hash(canonicalize(anchor)), NODES)
                    tx.execute(
                        "INSERT INTO receipt_anchors(receipt_digest, issuer, issued_at, logged_at, leaf_index, body) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (digest, anchor["issuer"], anchor["issuedAt"], logged_at, index, dumps(anchor)),
                    )
                    results[position] = {"receipt_digest": digest, "status": "logged", "leaf_index": index}
            self.publish_sth()
        return results

    # -- heads ----------------------------------------------------------------------

    def publish_sth(self, *, timestamp: str | None = None) -> dict[str, Any] | None:
        with self.store.db.transaction() as tx:
            tx.lock_log(RECEIPT_LOG_LOCK)
            size = Store.log_size(tx, NODES)
            if size == 0:
                return None
            last = tx.one("SELECT tree_size, body FROM receipt_sths ORDER BY tree_size DESC LIMIT 1")
            if last and int(last["tree_size"]) == size:
                return json.loads(last["body"])
            root = merkle.tree_root(size, Store.reader(tx, NODES))
            sth = sign_document(self.key, {
                "type": RECEIPT_STH_TYPE,
                "log": self.key.did,
                "treeSize": size,
                "rootHash": root.hex(),
                "timestamp": timestamp or utcnow(),
            })
            tx.execute("INSERT INTO receipt_sths(tree_size, root, timestamp, body) VALUES(?, ?, ?, ?)",
                       (size, root.hex(), sth["timestamp"], dumps(sth)))
        return sth

    def latest_sth(self) -> dict[str, Any] | None:
        with self.store.db.read() as tx:
            row = tx.one("SELECT body FROM receipt_sths ORDER BY tree_size DESC LIMIT 1")
        return json.loads(row["body"]) if row else None

    def sth(self, tree_size: int) -> dict[str, Any] | None:
        with self.store.db.read() as tx:
            row = tx.one("SELECT body FROM receipt_sths WHERE tree_size=?", (tree_size,))
        return json.loads(row["body"]) if row else None

    # -- proofs ---------------------------------------------------------------------

    def proof(self, receipt_digest: str, tree_size: int | None = None) -> dict[str, Any] | None:
        with self.store.db.read() as tx:
            row = tx.one("SELECT leaf_index, logged_at, body FROM receipt_anchors WHERE receipt_digest=?",
                         (receipt_digest,))
            if row is None:
                return None
            size = Store.log_size(tx, NODES)
            if tree_size is None:
                head = tx.one("SELECT tree_size FROM receipt_sths ORDER BY tree_size DESC LIMIT 1")
                tree_size = int(head["tree_size"]) if head else size
            if tree_size > size:
                raise ValueError("tree_size is larger than the log")
            index = int(row["leaf_index"])
            if index >= tree_size:
                raise ValueError("the anchor is not yet inside a signed head of that size")
            path = merkle.inclusion_proof(index, tree_size, Store.reader(tx, NODES))
        return {
            "anchor": json.loads(row["body"]),
            "leaf_index": index,
            "logged_at": row["logged_at"],
            "tree_size": tree_size,
            "audit_path": [h.hex() for h in path],
            "sth": self.sth(tree_size),
        }

    def consistency(self, first: int, second: int) -> list[str]:
        with self.store.db.read() as tx:
            if second > Store.log_size(tx, NODES):
                raise ValueError("second is larger than the log")
            return [h.hex() for h in merkle.consistency_proof(first, second, Store.reader(tx, NODES))]
