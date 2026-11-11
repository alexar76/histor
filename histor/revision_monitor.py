"""SUPP-06 monitor events (e) and (f) for publisher-signed revisions.

(e) ``forked_current`` — an index (or the store) shows two tip revisions for one name.
(f) ``unsigned_digest`` — tools HISTOR or a client just saw do not match any signed revision.

Events go into ``security_events`` (RSS ``/security-feed.xml``) and are summarised on ``/check``
as ``signedRevision.alerts``.
"""

from __future__ import annotations

import logging
from typing import Any

from histor import revision
from histor.logbook import utcnow
from histor.security_events import append_event
from histor.store import Store

log = logging.getLogger("histor.revision_monitor")

KIND_FORKED = "revision-forked-current"
KIND_UNSIGNED = "revision-unsigned-digest"


def forked_tips(entries: list[Any]) -> list[dict[str, Any]]:
    """Sibling tips: two revisions that share the same ``previousManifestDigest`` (case e)."""
    by_prev: dict[str | None, list[dict[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        prev = entry.get("previousManifestDigest")
        if prev is None and entry.get("revision") == 1:
            # First revisions have no previous; two "revision 1" digests are also a fork.
            prev = ""
        if not isinstance(prev, str) and prev is not None:
            continue
        by_prev.setdefault(prev, []).append(entry)
    forks: list[dict[str, Any]] = []
    for prev, group in by_prev.items():
        digests = {g.get("manifestDigest") for g in group if isinstance(g.get("manifestDigest"), str)}
        if len(digests) < 2:
            continue
        forks.append({
            "previousManifestDigest": prev or None,
            "tips": [
                {"revision": g.get("revision"), "manifestDigest": g.get("manifestDigest")}
                for g in group if isinstance(g.get("manifestDigest"), str)
            ],
        })
    return forks


def signed_digests(store: Store, server: str) -> set[str]:
    with store.db.read() as tx:
        rows = tx.execute(
            "SELECT manifest_digest FROM signed_revisions WHERE server=?",
            (server,),
        )
    return {r["manifest_digest"] for r in rows if r["manifest_digest"]}


def record_forked_current(store: Store, key, *, server: str, target_id: str | None,
                          forks: list[dict[str, Any]]) -> None:
    if not forks or not target_id:
        return
    evidence = {"case": "e", "server": server, "forks": forks}
    with store.db.transaction() as tx:
        append_event(tx, key, target_id, utcnow(), {"kind": KIND_FORKED}, evidence, kind=KIND_FORKED)
    log.warning("revision monitor (e) forked current for %s: %s", server, forks)


def record_unsigned_digest(store: Store, key, *, server: str, target_id: str | None,
                           served: str, known: set[str]) -> None:
    if not target_id or not known or served in known:
        return
    evidence = {
        "case": "f",
        "server": server,
        "servedManifestDigest": served,
        "signedManifestDigests": sorted(known),
    }
    with store.db.transaction() as tx:
        append_event(
            tx, key, target_id, utcnow(),
            {"kind": KIND_UNSIGNED, "signed": sorted(known)},
            evidence,
            kind=KIND_UNSIGNED,
        )
    log.warning("revision monitor (f) unsigned digest for %s: %s not in %s", server, served, known)


def alerts_for_check(*, match: str | None, forks: list[dict[str, Any]] | None = None) -> list[str]:
    """Short alert tags for the ``signedRevision`` block on ``/check``."""
    out: list[str] = []
    if forks:
        out.append("forked_current")
    if match == "different":
        out.append("unsigned_digest")
    return out


def observe_tools(store: Store, key, *, server: str, target_id: str | None,
                  tools: list[Any]) -> list[str]:
    """Case (f) when the caller sent tools: record and return alert tags."""
    known = signed_digests(store, server)
    if not known:
        return []
    try:
        served, _ = revision.digests(tools)
    except revision.RevisionError:
        return []
    record_unsigned_digest(store, key, server=server, target_id=target_id, served=served, known=known)
    return ["unsigned_digest"] if served not in known else []


def inspect_index(store: Store, key, *, server: str, target_id: str | None,
                  entries: list[Any]) -> list[str]:
    """Case (e) over a publisher index; record and return alert tags."""
    forks = forked_tips(entries)
    if not forks:
        return []
    record_forked_current(store, key, server=server, target_id=target_id, forks=forks)
    return ["forked_current"]
