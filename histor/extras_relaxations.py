"""Give the extras changes recorded before 2026-11-23 their ``relaxations`` (histor.extras.extras_diff).

    python -m histor.extras_relaxations count      # what fill would write; writes nothing
    python -m histor.extras_relaxations fill

Until 2026-11-23 a change summary named only ``escalations``: tools that stopped saying they are
read-only or started saying they are destructive, the direction in which a client asks more. The
other direction, a tool already listed that starts saying ``readOnlyHint: true`` or
``destructiveHint: false`` so that a client which skips the prompt on those hints stops asking, was
not named. This recomputes it for every recorded change from the two stored extras bodies and the
tool set of the target's previous read, the same inputs the crawler uses now, and adds the list to
the summary. Nothing else in a summary is touched; a change whose list is empty is left as it is.
A change whose earlier body is not stored is counted as ``unreadable`` and left alone.

The work is paged by ``seq`` (one read and at most one write transaction per page), so it runs
beside the service. Running it again writes nothing new.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from histor.extras import extras_diff, tool_names
from histor.store import dumps

if TYPE_CHECKING:
    from collections.abc import Callable

    from histor.db import Backend


class _Cache:
    """Body texts by digest, bounded by their total size: the same bodies recur across changes, and
    some are megabytes (a production run with a count bound was killed at 1.5 GB)."""

    def __init__(self, max_chars: int = 64_000_000) -> None:
        self.max_chars, self.chars, self.items = max_chars, 0, {}

    def get(self, tx, digest: str, kind: str) -> Any:
        key = (kind, digest)
        if key not in self.items:
            row = tx.one("SELECT body FROM blobs WHERE digest=? AND kind=?", (digest, kind))
            text = None if row is None else str(row["body"])
            size = len(text or "")
            if self.chars + size > self.max_chars:
                self.items.clear()
                self.chars = 0
            self.items[key] = text
            self.chars += size
        return self.items[key]


def relaxations(backend: Backend, *, write: bool, page: int = 2_000,
                say: Callable[[str], None] = lambda _m: None) -> dict[str, int]:
    """Recompute ``relaxations`` for every recorded extras change; write them when *write*."""
    result = {"changes": 0, "withRelaxations": 0, "written": 0, "alreadySet": 0, "unreadable": 0,
              "withoutPreviousToolSet": 0}
    bodies, toolsets = _Cache(), _Cache(32_000_000)
    after = 0
    while True:
        updates: list[tuple[str, int, str]] = []
        with backend.read() as tx:
            rows = tx.execute("SELECT seq, target_id, observed_at, from_extras, to_extras, summary FROM extras_changes "
                              "WHERE seq > ? ORDER BY seq LIMIT ?", (after, page))
            for row in rows:
                result["changes"] += 1
                # NULL: no tool carried an extra member before (the crawler stores "" as NULL here).
                old_text = bodies.get(tx, row["from_extras"], "toolextras") if row["from_extras"] else "{}"
                new_text = bodies.get(tx, row["to_extras"], "toolextras") if row["to_extras"] else "{}"
                if old_text is None or new_text is None:
                    result["unreadable"] += 1
                    continue
                old_body, new_body = json.loads(old_text), json.loads(new_text)
                # Only the tools that now claim less can be relaxations; most changes have none, and
                # then the previous tool set (one indexed query per change) is not needed.
                if not extras_diff(old_body, new_body, (new_body.get("tools") or {}).keys()).get("relaxations"):
                    continue
                prior = tx.one("SELECT toolset FROM observations WHERE target_id=? AND observed_at < ? "
                               "AND status='ok' AND toolset IS NOT NULL ORDER BY observed_at DESC LIMIT 1",
                               (row["target_id"], row["observed_at"]))
                before = None
                if prior is not None:
                    text = toolsets.get(tx, prior["toolset"], "toolset")
                    before = tool_names(text) if text is not None else None
                if before is None:
                    result["withoutPreviousToolSet"] += 1
                found = extras_diff(old_body, new_body, before).get("relaxations")
                if not found:
                    continue
                result["withRelaxations"] += 1
                summary = json.loads(row["summary"])
                if summary.get("relaxations") == found:
                    result["alreadySet"] += 1
                    continue
                summary["relaxations"] = found
                updates.append((dumps(summary), int(row["seq"]), str(row["summary"])))
        if not rows:
            break
        after = int(rows[-1]["seq"])
        if write and updates:
            with backend.transaction() as tx:
                for summary, seq, was in updates:
                    result["written"] += len(tx.execute(
                        "UPDATE extras_changes SET summary=? WHERE seq=? AND summary=? RETURNING seq", (summary, seq, was)))
        say(f"seq {after}: {result['changes']} changes read, {result['withRelaxations']} with relaxations, "
            f"{result['written']} written")
    return result


def main(argv: list[str] | None = None) -> int:
    from histor.config import load_settings
    from histor.db import open_backend
    from histor.migrations import apply_migrations

    parser = argparse.ArgumentParser(prog="python -m histor.extras_relaxations", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("count", "fill"))
    parser.add_argument("--page", type=int, default=2_000)
    args = parser.parse_args(argv)

    settings = load_settings()
    backend = open_backend(settings.db_path, settings.database_url)
    try:
        apply_migrations(backend)
        print(json.dumps(relaxations(backend, write=args.command == "fill", page=args.page,
                                     say=lambda m: print(m, file=sys.stderr))))
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
