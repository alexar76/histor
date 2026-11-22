"""Recounting the new outside addresses of recorded changes (histor.recount_addresses)."""

from __future__ import annotations

import json

from histor import recount_addresses
from histor.db import open_backend
from histor.migrations import apply_migrations
from histor.store import dumps


def _change(tx, change_id, old, new, listed, *, keep_old=True):
    for digest, body in ((f"sha256-old{change_id}", old), (f"sha256-new{change_id}", new)):
        if keep_old or "new" in digest:
            tx.execute("INSERT INTO blobs(digest, kind, body) VALUES(?, 'toolset', ?)", (digest, dumps(body)))
    summary = {"added": [], "removed": [], "modified": [], "detail": "full",
               "newAddresses": listed, "newAddressesTotal": len(listed)}
    tx.execute(
        "INSERT INTO changes(target_id, observed_at, from_toolset, to_toolset, summary) VALUES(?, ?, ?, ?, ?)",
        ("t1", f"2026-11-22T10:0{change_id}:00Z", f"sha256-old{change_id}", f"sha256-new{change_id}", dumps(summary)),
    )


def _summaries(backend):
    with backend.read() as tx:
        return [json.loads(r["summary"]) for r in tx.execute("SELECT summary FROM changes ORDER BY id")]


def test_recount_rewrites_only_what_the_detector_now_reads_differently(settings, tmp_path):
    backend = open_backend(settings.db_path, settings.database_url)
    try:
        apply_migrations(backend)
        before = [{"name": "a", "description": "Returns rows.", "inputSchema": {}}]
        with backend.transaction() as tx:
            # 1: a field name and a real host were both listed; 2: only field names; 3: already right;
            # 4: the old tool set is no longer kept; 5: no address at all.
            _change(tx, 1, before, [{"name": "a", "description": "Returns data.company; docs at tiles.example.com",
                                     "inputSchema": {}}], ["data.company", "tiles.example.com"])
            _change(tx, 2, before, [{"name": "a", "description": "Fills versions.kitVersion and asm.part.",
                                     "inputSchema": {}}], ["asm.part", "versions.kitversion"])
            _change(tx, 3, before, [{"name": "a", "description": "See https://docs.example.org", "inputSchema": {}}],
                    ["docs.example.org"])
            _change(tx, 4, before, [{"name": "a", "description": "Returns prep.total", "inputSchema": {}}],
                    ["prep.total"], keep_old=False)
            _change(tx, 5, before, [{"name": "a", "description": "Returns more rows.", "inputSchema": {}}], [])

        dry = recount_addresses.recount(backend, apply=False, pause_s=0)
        assert dry == {"listing": 4, "differ": 2, "cleared": 1, "written": 0}
        assert [s["newAddresses"] for s in _summaries(backend)][:2] == [["data.company", "tiles.example.com"],
                                                                        ["asm.part", "versions.kitversion"]]

        backup = tmp_path / "replaced.jsonl"
        done = recount_addresses.recount(backend, apply=True, backup=backup, page=2, pause_s=0)
        assert done == {"listing": 4, "differ": 2, "cleared": 1, "written": 2}
        after = _summaries(backend)
        assert [(s["newAddresses"], s["newAddressesTotal"]) for s in after] == [
            (["tiles.example.com"], 1), ([], 0), (["docs.example.org"], 1), (["prep.total"], 1), ([], 0)]
        assert all(s["detail"] == "full" and s["added"] == [] for s in after)  # the rest of a summary is untouched
        kept = [json.loads(line) for line in backup.read_text(encoding="utf-8").splitlines()]
        assert kept == [{"id": 1, "newAddresses": ["data.company", "tiles.example.com"], "newAddressesTotal": 2},
                        {"id": 2, "newAddresses": ["asm.part", "versions.kitversion"], "newAddressesTotal": 2}]

        again = recount_addresses.recount(backend, apply=True, backup=backup, pause_s=0)
        assert again["differ"] == 0 and again["written"] == 0 and len(backup.read_text().splitlines()) == 2
    finally:
        backend.close()
