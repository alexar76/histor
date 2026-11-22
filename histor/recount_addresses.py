"""Recount the "new outside addresses" of recorded changes with the current detector.

    python -m histor.recount_addresses status                   # how many records would change; writes nothing
    python -m histor.recount_addresses apply --backup FILE      # rewrite them, old values kept in FILE (JSON lines)

A change record carries ``newAddresses``: hosts, e-mail addresses and IPs the new tool set names and the old one
did not. It is computed when the change is recorded, so a record keeps the reading of the detector of its day.
Until 2026-11-22 that detector also listed any dotted word ("versions.kitVersion", "data.company"). This reads
the two tool sets of every record that lists an address and writes the list the current detector gives.

Nothing signed is touched: the list is part of the change summary, which is derived from two content-addressed
tool sets and is not in the label, the log or the security events. Records are read by id in small pages, one
short transaction per page, with a pause between pages, so a running crawl is not held up. Running it again
changes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import TYPE_CHECKING, Any

from histor import diffing
from histor.store import dumps

if TYPE_CHECKING:
    from pathlib import Path

    from histor.db import Backend, Tx


def _tool_set(tx: Tx, digest: str | None) -> list[dict[str, Any]] | None:
    if not digest:
        return None
    row = tx.one("SELECT body FROM blobs WHERE digest=?", (digest,))
    return json.loads(row["body"]) if row else None


def _recounted(tx: Tx, row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(summary as stored, summary recounted) when they differ, else None."""
    summary = json.loads(row["summary"])
    if not isinstance(summary, dict) or not summary.get("newAddressesTotal"):
        return None
    old, new = _tool_set(tx, row["from_toolset"]), _tool_set(tx, row["to_toolset"])
    if old is None or new is None:
        return None  # a tool set that is no longer kept: the record stays as it is
    listed, total = diffing.new_addresses(old, new)
    if listed == summary.get("newAddresses") and total == summary.get("newAddressesTotal"):
        return None
    return summary, {**summary, "newAddresses": listed, "newAddressesTotal": total}


def recount(backend: Backend, *, apply: bool, backup: Path | None = None, page: int = 200,
            pause_s: float = 0.2, say: Any = None) -> dict[str, int]:
    """Go through every change that lists an address; with ``apply`` rewrite the ones that differ."""
    counts = {"listing": 0, "differ": 0, "cleared": 0, "written": 0}
    last = 0
    out = backup.open("a", encoding="utf-8") if (apply and backup) else None
    try:
        while True:
            with (backend.transaction() if apply else backend.read()) as tx:
                # Only records that list an address are read: about one in fifteen on 2026-11-22.
                rows = tx.execute(
                    "SELECT id, from_toolset, to_toolset, summary FROM changes WHERE id>? AND summary LIKE ? "
                    "AND summary NOT LIKE ? ORDER BY id LIMIT ?",
                    (last, '%"newAddressesTotal":%', '%"newAddressesTotal":0%', page),
                )
                for row in rows:
                    counts["listing"] += 1
                    changed = _recounted(tx, row)
                    if changed is None:
                        continue
                    before, after = changed
                    counts["differ"] += 1
                    counts["cleared"] += after["newAddressesTotal"] == 0
                    if apply:
                        if out:
                            out.write(json.dumps({"id": row["id"], "newAddresses": before.get("newAddresses"),
                                                  "newAddressesTotal": before.get("newAddressesTotal")},
                                                 ensure_ascii=False) + "\n")
                        tx.execute("UPDATE changes SET summary=? WHERE id=?", (dumps(after), row["id"]))
                        counts["written"] += 1
            if out:
                out.flush()
            if not rows:
                return counts
            last = rows[-1]["id"]
            if say and counts["listing"] % (page * 10) == 0:
                say(f"listing {counts['listing']}, differ {counts['differ']}")
            time.sleep(pause_s)
    finally:
        if out:
            out.close()


def main(argv: list[str] | None = None) -> int:
    from pathlib import Path

    from histor.config import load_settings
    from histor.db import open_backend

    parser = argparse.ArgumentParser(prog="python -m histor.recount_addresses", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "apply"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--page", type=int, default=200)
        cmd.add_argument("--pause", type=float, default=0.2)
        if name == "apply":
            cmd.add_argument("--backup", type=Path, required=True, help="JSON lines of the values replaced")
    args = parser.parse_args(argv)

    settings = load_settings()
    backend = open_backend(settings.db_path, settings.database_url)
    try:
        print(json.dumps(recount(
            backend, apply=args.command == "apply", backup=getattr(args, "backup", None), page=args.page,
            pause_s=args.pause, say=lambda m: print(m, file=sys.stderr),
        )))
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
