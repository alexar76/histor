"""Move the log's extras digests to another scheme (histor.extras), and every stored reference with them.

    python -m histor.extras_rekey status
    python -m histor.extras_rekey to rfc8785      # or: to legacy (the way back)
    python -m histor.extras_rekey prune           # drop the bodies kept under the old digests

``to`` first records the new scheme in ``meta``, so every observation from then on is digested the new
way. It then gives every stored extras body its digest under that scheme and rewrites the references:
``observations.extras``, ``targets.current_extras``, ``extras_changes.from_extras`` and ``to_extras``.
A body stored under its old digest stays until ``prune``: a crawl that read a target just before the
switch can still find the body it compares with. ``to`` repeats its passes until one maps nothing, so
an observation written under the old scheme during the run is moved as well; running it again is safe.

No extras change is recorded by any of this: the bodies do not change, only how their digest is written
(``Crawler._settle_extras`` treats one body under two schemes as unchanged). Published dataset snapshots
keep the digests they were exported with; a snapshot's README says which scheme it used.

The work is paged (``--page`` bodies per transaction, references in ranges of ``--range`` rows), so
it can run beside the service; it holds no long transaction and keeps one page of bodies in memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from histor import extras
from histor.store import Store

if TYPE_CHECKING:
    from collections.abc import Callable

    from histor.db import Backend

#: (table, key column, digest columns): every place an extras digest is stored besides ``blobs``.
REFERENCES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("observations", "id", ("extras",)),
    ("targets", "id", ("current_extras",)),
    ("extras_changes", "seq", ("from_extras", "to_extras")),
)
MAX_PASSES = 3


def _bodies(backend: Backend, page: int):
    """Every stored extras body, in digest order, one read transaction per page."""
    after = ""
    while True:
        with backend.read() as tx:
            rows = tx.execute("SELECT digest, body FROM blobs WHERE kind='toolextras' AND digest > ? "
                              "ORDER BY digest LIMIT ?", (after, page))
        if not rows:
            return
        yield rows
        after = rows[-1]["digest"]


def _key_ranges(backend: Backend, table: str, key: str, size: int):
    with backend.read() as tx:
        row = tx.one(f"SELECT MIN({key}) AS lo, MAX({key}) AS hi FROM {table}")
    if not row or row["lo"] is None:
        return
    if isinstance(row["lo"], str):          # targets: text ids, one statement
        yield None
        return
    lo, hi = int(row["lo"]), int(row["hi"])
    start = lo - 1
    while start < hi:
        yield start, start + size
        start += size


def _remap(backend: Backend, size: int) -> int:
    """Rewrite every reference to an old digest in ``extras_rekey``; returns the rows changed."""
    changed = 0
    for table, key, columns in REFERENCES:
        for column in columns:
            sql = (f"UPDATE {table} SET {column} = (SELECT r.new FROM extras_rekey r WHERE r.old = {table}.{column}) "
                   f"WHERE {column} IN (SELECT old FROM extras_rekey)")
            for span in _key_ranges(backend, table, key, size):
                with backend.transaction() as tx:
                    if span is None:
                        rows = tx.execute(sql + f" RETURNING {key}")
                    else:
                        rows = tx.execute(sql + f" AND {key} > ? AND {key} <= ? RETURNING {key}", span)
                    changed += len(rows)
    return changed


def rekey(backend: Backend, to: str, *, page: int = 500, size: int = 50_000,
          say: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Record *to* as the log's extras scheme and move every stored extras digest to it."""
    problem = extras.scheme_problem(to)
    if problem:
        raise ValueError(problem)
    with backend.transaction() as tx:
        before = Store.extras_scheme(tx)
        Store.set_meta(tx, extras.META_KEY, to)
    say(f"scheme {before} -> {to}; new observations use {to} from now on")
    # "moved": bodies that got a digest under *to* in this run; "references": rows that now point at one.
    # A body under its old digest stays until prune(), so every pass maps it again; a pass that moves no
    # body and rewrites no reference is the last.
    result: dict[str, Any] = {"from": before, "to": to, "passes": 0, "moved": 0, "references": 0}
    for _ in range(MAX_PASSES):
        result["passes"] += 1
        moved = mapped = 0
        for rows in _bodies(backend, page):
            pairs = [(row["digest"], new, row["body"]) for row in rows
                     if (new := extras.digest_of(json.loads(row["body"]), to)) != row["digest"]]
            if not pairs:
                continue
            with backend.transaction() as tx:
                for old, new, body in pairs:
                    # The body text as stored: never parsed and written again.
                    moved += len(tx.execute("INSERT INTO blobs(digest, kind, body) VALUES(?, 'toolextras', ?) "
                                            "ON CONFLICT(digest) DO NOTHING RETURNING digest", (new, body)))
                    tx.execute("INSERT INTO extras_rekey(old, new) VALUES(?, ?) "
                               "ON CONFLICT(old) DO UPDATE SET new=excluded.new", (old, new))
            mapped += len(pairs)
        references = _remap(backend, size) if mapped else 0
        if mapped:
            with backend.transaction() as tx:
                tx.execute("DELETE FROM extras_rekey")
        result["moved"] += moved
        result["references"] += references
        say(f"pass {result['passes']}: {moved} bodies got a {to} digest, {references} references rewritten")
        if not moved and not references:
            break
    return result


def stale(backend: Backend, page: int = 500) -> list[str]:
    """Stored extras bodies whose digest is not their digest under the log's scheme."""
    with backend.read() as tx:
        scheme = Store.extras_scheme(tx)
    return [row["digest"] for rows in _bodies(backend, page) for row in rows
            if extras.digest_of(json.loads(row["body"]), scheme) != row["digest"]]


def prune(backend: Backend, page: int = 500) -> int:
    """Delete the bodies kept under old digests that nothing references any more."""
    gone = 0
    for digest in stale(backend, page):
        with backend.transaction() as tx:
            if any(tx.one(f"SELECT 1 AS x FROM {table} WHERE {column}=? LIMIT 1", (digest,))
                   for table, _key, columns in REFERENCES for column in columns):
                continue
            tx.execute("DELETE FROM blobs WHERE digest=? AND kind='toolextras'", (digest,))
            gone += 1
    return gone


def main(argv: list[str] | None = None) -> int:
    from histor.config import load_settings
    from histor.db import open_backend
    from histor.migrations import apply_migrations

    parser = argparse.ArgumentParser(prog="python -m histor.extras_rekey", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    to = sub.add_parser("to")
    to.add_argument("scheme", choices=extras.SCHEMES)
    to.add_argument("--page", type=int, default=500)
    to.add_argument("--range", dest="size", type=int, default=50_000)
    sub.add_parser("prune")
    args = parser.parse_args(argv)

    settings = load_settings()
    backend = open_backend(settings.db_path, settings.database_url)
    try:
        apply_migrations(backend)
        if args.command == "status":
            with backend.read() as tx:
                scheme = Store.extras_scheme(tx)
            print(json.dumps({"scheme": scheme, "bodiesUnderAnotherDigest": len(stale(backend))}))
        elif args.command == "to":
            print(json.dumps(rekey(backend, args.scheme, page=args.page, size=args.size,
                                   say=lambda m: print(m, file=sys.stderr))))
        else:
            print(json.dumps({"pruned": prune(backend)}))
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
