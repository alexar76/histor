"""Publisher-signed tool-manifest revisions: ingest, /check, and HISTOR's revision tree.

The publisher signs a SUPP-06 statement in CI (``histor revision`` + Sigstore keyless); Sigstore
records it in Rekor. HISTOR reads the publisher's ``manifest-revisions`` index, verifies the
envelope, answers ``/check`` with a ``signedRevision`` block, and appends a short, HISTOR-signed
leaf to a second Merkle tree (same pattern as ``histor.receipts``) so the fact that HISTOR accepted
the revision is itself logged under HISTOR's key and STHs.

The main label log stays labels-only (histor-tlog/2). Rekor remains the public log SUPP-06 names;
HISTOR's revision tree is the operator journal that binds the registry name to that Rekor entry
and to what clients ask ``/check`` about.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from awr import SigningKey, canonicalize, canonical_sri

from histor import merkle, revision
from histor.db import Tx
from histor.logbook import sign_document, utcnow
from histor.mcpclient import USER_AGENT
from histor.netguard import BlockedAddress, resolve_public
from histor.store import Store, dumps

log = logging.getLogger("histor.signed_revisions")

LEAF_TYPE = "histor.publisher-revision/v1"
STH_TYPE = "histor.revisions-sth/v1"
NODES = "revision_log_nodes"
REVISION_LOG_LOCK = 0x4849_5256  # "HIRV"
MAX_INDEX_BYTES = 256 * 1024
MAX_ENVELOPE_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 15.0

# Pilot: the first signed index. Operators add more with HISTOR_REVISION_SOURCES.
DEFAULT_SOURCES: tuple[tuple[str, str], ...] = (
    ("io.github.alexar76/histor",
     "https://raw.githubusercontent.com/alexar76/histor/manifest-revisions/index.json"),
)


class RevisionIngestError(ValueError):
    pass


def parse_sources(raw: str | None) -> tuple[tuple[str, str], ...]:
    """``server=url;server=url``. Empty → the pilot default. ``-`` → none."""
    text = (raw or "").strip()
    if text == "-":
        return ()
    if not text:
        return DEFAULT_SOURCES
    out: list[tuple[str, str]] = []
    for part in text.replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise RuntimeError(f"HISTOR_REVISION_SOURCES entry needs server=url, got {part!r}")
        server, url = part.split("=", 1)
        server, url = server.strip(), url.strip()
        if not server or not url.startswith("https://"):
            raise RuntimeError(f"HISTOR_REVISION_SOURCES needs https URL for {server!r}")
        out.append((server, url))
    return tuple(out)


def public(row: dict[str, Any], *, match: str | None = None) -> dict[str, Any]:
    """What ``/check`` and the server page show for one accepted revision."""
    body = {k: v for k, v in (
        ("revision", row["revision"]),
        ("manifestDigest", row["manifest_digest"]),
        ("serverVersion", row.get("server_version")),
        ("signer", row["signer"]),
        ("log", "sigstore-rekor"),
        ("logIndex", row["log_index"]),
        ("createdAt", row.get("created_at")),
        ("source", row.get("source_url")),
        ("signatureVerified", bool(row.get("verified_at"))),
        ("leafIndex", row.get("leaf_index")),
        ("match", match),
    ) if v is not None}
    return body


def match_client_tools(envelope: dict[str, Any], tools: list[Any] | None) -> str | None:
    """``same`` / ``different`` when the client sent tools; None when it sent only an MTL digest."""
    if tools is None:
        return None
    served, _ = revision.digests(tools)
    return "same" if served == envelope.get("manifestDigest") else "different"


def _fetch_bytes(url: str, *, max_bytes: int, allow_private: bool = False,
                 transport: httpx.BaseTransport | None = None) -> bytes:
    try:
        pinned = resolve_public(url, allow_private=allow_private)
    except BlockedAddress as exc:
        raise RevisionIngestError(f"refused: {exc}") from exc
    headers = {"Host": pinned.host_header, "Accept": "application/json", "User-Agent": USER_AGENT}
    extensions = {"sni_hostname": pinned.sni_hostname} if pinned.sni_hostname else {}
    try:
        with httpx.Client(timeout=httpx.Timeout(FETCH_TIMEOUT_S, connect=min(FETCH_TIMEOUT_S, 5.0)),
                          follow_redirects=False, transport=transport) as client:
            request = client.build_request("GET", pinned.url, headers=headers, extensions=extensions)
            response = client.send(request, stream=True)
            try:
                if 300 <= response.status_code < 400:
                    raise RevisionIngestError(f"http-{response.status_code}: redirects are not followed")
                if response.status_code != 200:
                    raise RevisionIngestError(f"http-{response.status_code}")
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise RevisionIngestError(f"larger than {max_bytes // 1024} KiB")
            finally:
                response.close()
    except httpx.HTTPError as exc:
        raise RevisionIngestError(f"unreachable: {type(exc).__name__}") from exc
    return body


def envelope_url(index_url: str, relative: str) -> str:
    base = index_url.rsplit("/", 1)[0] + "/"
    return urljoin(base, relative)


def leaf_document(*, server: str, revision_n: int, manifest_digest: str, log_index: str,
                   envelope_digest: str, signer: str, created_at: str | None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "type": LEAF_TYPE,
        "server": server,
        "revision": revision_n,
        "manifestDigest": manifest_digest,
        "logIndex": str(log_index),
        "envelopeDigest": envelope_digest,
        "signer": signer,
    }
    if created_at:
        doc["createdAt"] = created_at
    return doc


class RevisionJournal:
    """Second Merkle tree: one leaf per accepted publisher revision HISTOR verified."""

    def __init__(self, store: Store, key: SigningKey) -> None:
        self.store = store
        self.key = key

    def append(self, tx: Tx, document: dict[str, Any]) -> int:
        """Caller holds ``REVISION_LOG_LOCK``. Returns the new leaf index."""
        return Store.append_leaf(tx, merkle.leaf_hash(canonicalize(document)), NODES)

    def publish_sth(self, *, timestamp: str | None = None) -> dict[str, Any] | None:
        with self.store.db.transaction() as tx:
            tx.lock_log(REVISION_LOG_LOCK)
            size = Store.log_size(tx, NODES)
            if size == 0:
                return None
            last = tx.one("SELECT tree_size, body FROM revision_sths ORDER BY tree_size DESC LIMIT 1")
            if last and int(last["tree_size"]) == size:
                return json.loads(last["body"])
            root = merkle.tree_root(size, Store.reader(tx, NODES))
            sth = sign_document(self.key, {
                "type": STH_TYPE,
                "log": self.key.did,
                "treeSize": size,
                "rootHash": root.hex(),
                "timestamp": timestamp or utcnow(),
            })
            tx.execute(
                "INSERT INTO revision_sths(tree_size, root, timestamp, body) VALUES(?, ?, ?, ?)",
                (size, root.hex(), sth["timestamp"], dumps(sth)),
            )
            return sth


class RevisionIngest:
    def __init__(self, store: Store, key: SigningKey, sources: tuple[tuple[str, str], ...],
                 *, allow_private: bool = False, transport: httpx.BaseTransport | None = None,
                 verify_signatures: bool = True) -> None:
        self.store = store
        self.journal = RevisionJournal(store, key)
        self.sources = sources
        self.allow_private = allow_private
        self.transport = transport
        self.verify_signatures = verify_signatures

    def sync(self) -> dict[str, Any]:
        """Pull every configured index; return counts for the crawl / CLI."""
        stats = {"sources": 0, "accepted": 0, "duplicate": 0, "failed": 0, "errors": []}
        for server, index_url in self.sources:
            stats["sources"] += 1
            try:
                result = self.sync_one(server, index_url)
            except RevisionIngestError as exc:
                stats["failed"] += 1
                stats["errors"].append({"server": server, "error": str(exc)})
                log.warning("revision index %s: %s", server, exc)
                continue
            stats["accepted"] += result["accepted"]
            stats["duplicate"] += result["duplicate"]
            stats["failed"] += result["failed"]
            stats["errors"].extend(result["errors"])
        if stats["accepted"]:
            self.journal.publish_sth()
        return stats

    def sync_one(self, server: str, index_url: str) -> dict[str, Any]:
        raw = _fetch_bytes(index_url, max_bytes=MAX_INDEX_BYTES,
                           allow_private=self.allow_private, transport=self.transport)
        try:
            index = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RevisionIngestError("index is not JSON") from exc
        if not isinstance(index, dict) or index.get("format") != revision.INDEX_FORMAT:
            raise RevisionIngestError(f"not a {revision.INDEX_FORMAT} index")
        if index.get("server") != server:
            raise RevisionIngestError(f"index is for {index.get('server')!r}, configured as {server!r}")
        entries = index.get("revisions")
        if not isinstance(entries, list):
            raise RevisionIngestError("index.revisions must be a list")
        accepted = duplicate = failed = 0
        errors: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                failed += 1
                continue
            rel = entry.get("envelope")
            if not isinstance(rel, str) or not rel or ".." in rel or rel.startswith("/"):
                failed += 1
                errors.append({"server": server, "error": f"bad envelope path {rel!r}"})
                continue
            try:
                status = self._accept(server, envelope_url(index_url, rel), index_url, entry)
            except RevisionIngestError as exc:
                failed += 1
                errors.append({"server": server, "revision": str(entry.get("revision")), "error": str(exc)})
                continue
            if status == "accepted":
                accepted += 1
            else:
                duplicate += 1
        return {"accepted": accepted, "duplicate": duplicate, "failed": failed, "errors": errors}

    def accept_envelope(self, envelope: dict[str, Any], *, source_url: str,
                        check_signature: bool | None = None) -> str:
        """Ingest one envelope (tests and offline). Returns ``accepted`` or ``duplicate``."""
        return self._store(envelope, source_url=source_url,
                           check_signature=self.verify_signatures if check_signature is None else check_signature)

    def _accept(self, server: str, env_url: str, index_url: str, entry: dict[str, Any]) -> str:
        raw = _fetch_bytes(env_url, max_bytes=MAX_ENVELOPE_BYTES,
                           allow_private=self.allow_private, transport=self.transport)
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RevisionIngestError("envelope is not JSON") from exc
        if not isinstance(envelope, dict):
            raise RevisionIngestError("envelope is not an object")
        if envelope.get("server") != server:
            raise RevisionIngestError(f"envelope server {envelope.get('server')!r} ≠ {server!r}")
        if entry.get("revision") is not None and envelope.get("revision") != entry.get("revision"):
            raise RevisionIngestError("envelope revision ≠ index entry")
        if entry.get("manifestDigest") and envelope.get("manifestDigest") != entry.get("manifestDigest"):
            raise RevisionIngestError("envelope digest ≠ index entry")
        return self._store(envelope, source_url=index_url, check_signature=self.verify_signatures)

    def _store(self, envelope: dict[str, Any], *, source_url: str, check_signature: bool) -> str:
        if envelope.get("format") != revision.FORMAT:
            raise RevisionIngestError(f"not a {revision.FORMAT} envelope")
        server = envelope.get("server")
        if not isinstance(server, str) or not server:
            raise RevisionIngestError("envelope has no server")
        n = envelope.get("revision")
        if not isinstance(n, int) or n < 1:
            raise RevisionIngestError("envelope.revision must be a positive int")
        verdict = revision.verify(envelope, check_signature=check_signature)
        if not verdict.get("ok"):
            raise RevisionIngestError(f"verify {verdict.get('reason')}: {verdict.get('detail')}")
        manifest = envelope["manifestDigest"]
        log_index = str((envelope.get("transparencyLog") or {}).get("logIndex") or "")
        signer = (envelope.get("signer") or {}).get("identity") or ""
        created = envelope.get("createdAt")
        env_digest = canonical_sri(envelope)
        targets = self.store.targets_by(name=server)
        target_id = targets[0]["id"] if targets else None
        leaf = sign_document(self.journal.key, leaf_document(
            server=server, revision_n=n, manifest_digest=manifest, log_index=log_index,
            envelope_digest=env_digest, signer=signer,
            created_at=created if isinstance(created, str) else None,
        ))
        now = utcnow()
        with self.store.db.transaction() as tx:
            existing = tx.one(
                "SELECT revision FROM signed_revisions WHERE server=? AND revision=?",
                (server, n),
            )
            if existing is not None:
                return "duplicate"
            tx.lock_log(REVISION_LOG_LOCK)
            leaf_index = self.journal.append(tx, leaf)
            tx.execute(
                "INSERT INTO signed_revisions(server, revision, target_id, manifest_digest, server_version, "
                "signer, log_index, created_at, source_url, envelope_digest, body, leaf_index, verified_at, "
                "ingested_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (server, n, target_id, manifest, envelope.get("serverVersion"), signer, log_index,
                 created if isinstance(created, str) else None, source_url, env_digest, dumps(envelope),
                 leaf_index, now if check_signature else None, now),
            )
        return "accepted"


def latest_for_server(store: Store, server: str) -> dict[str, Any] | None:
    with store.db.read() as tx:
        row = tx.one(
            "SELECT server, revision, target_id, manifest_digest, server_version, signer, log_index, "
            "created_at, source_url, envelope_digest, leaf_index, verified_at, ingested_at, body "
            "FROM signed_revisions WHERE server=? ORDER BY revision DESC LIMIT 1",
            (server,),
        )
    return dict(row) if row else None


def latest_for_target(store: Store, target_id: str) -> dict[str, Any] | None:
    with store.db.read() as tx:
        row = tx.one(
            "SELECT server, revision, target_id, manifest_digest, server_version, signer, log_index, "
            "created_at, source_url, envelope_digest, leaf_index, verified_at, ingested_at, body "
            "FROM signed_revisions WHERE target_id=? ORDER BY revision DESC LIMIT 1",
            (target_id,),
        )
    return dict(row) if row else None


def envelope_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["body"])
