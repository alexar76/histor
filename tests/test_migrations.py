"""Migrations apply once, twice is a no-op, and a database from the future is refused."""

from __future__ import annotations

import pytest

from histor.db import open_backend
from histor.migrations import (
    MIGRATIONS,
    TARGET_COLUMNS,
    MigrationError,
    apply_migrations,
    current_version,
    target_columns,
)


@pytest.fixture
def backend(settings):
    b = open_backend(settings.db_path, settings.database_url)
    yield b
    b.close()


def test_apply_is_idempotent_and_meets_the_column_contract(backend):
    head = apply_migrations(backend)
    assert head == MIGRATIONS[-1][0] == current_version(backend)
    assert apply_migrations(backend) == head
    assert target_columns(backend) == TARGET_COLUMNS


def test_a_revision_this_build_does_not_know_is_refused(backend):
    apply_migrations(backend)
    with backend.transaction() as tx:
        tx.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES(?, ?, ?)",
                   (999, "999_future", "2026-01-01T00:00:00Z"))
    with pytest.raises(MigrationError, match="does not know"):
        apply_migrations(backend)


def test_identity_columns_generate_keys_on_both_engines(backend):
    apply_migrations(backend)
    with backend.transaction() as tx:
        a = tx.one("INSERT INTO runs(started_at) VALUES(?) RETURNING id", ("2026-01-01T00:00:00Z",))["id"]
        b = tx.one("INSERT INTO runs(started_at) VALUES(?) RETURNING id", ("2026-01-01T00:00:01Z",))["id"]
    assert int(b) == int(a) + 1


def test_remote_queue_upgrade_preserves_existing_run_membership(backend, monkeypatch):
    import histor.migrations as m

    monkeypatch.setattr(m, "MIGRATIONS", m.MIGRATIONS[:12])
    apply_migrations(backend)
    with backend.transaction() as tx:
        run_id = tx.one("INSERT INTO runs(started_at) VALUES(?) RETURNING id", ("2026-10-20T00:00:00Z",))["id"]
        for at in ("2026-10-20T01:00:00Z", "2026-10-20T02:00:00Z"):
            tx.execute("INSERT INTO observations(target_id, run_id, observed_at, status) VALUES(?, ?, ?, 'ok')",
                       ("previous-target", run_id, at))
    monkeypatch.undo()
    apply_migrations(backend)
    with backend.read() as tx:
        assert tx.one("SELECT kind FROM runs WHERE id=?", (run_id,))["kind"] == "crawl"
        rows = tx.execute("SELECT * FROM crawl_attempts")
        assert len(rows) == 1 and rows[0]["attempted_at"] == "2026-10-20T02:00:00Z"


def test_014_adds_package_versions_and_leaves_older_rows_unknown(backend, monkeypatch):
    """An upgraded log keeps its rows; what they ran was not recorded, so it stays NULL, not guessed."""
    import histor.migrations as m

    monkeypatch.setattr(m, "MIGRATIONS", m.MIGRATIONS[:13])
    apply_migrations(backend)
    with backend.transaction() as tx:
        tx.execute("INSERT INTO observations(target_id, observed_at, status, toolset) VALUES(?, ?, 'ok', ?)",
                   ("pkg", "2026-10-25T01:00:00Z", "sha256-a"))
        tx.execute("INSERT INTO changes(target_id, observed_at, from_toolset, to_toolset, summary) VALUES(?, ?, ?, ?, ?)",
                   ("pkg", "2026-10-25T01:00:00Z", "sha256-a", "sha256-b", "{}"))
    monkeypatch.undo()
    assert apply_migrations(backend) == m.MIGRATIONS[-1][0]
    with backend.read() as tx:
        assert tx.one("SELECT package_version FROM observations WHERE target_id='pkg'")["package_version"] is None
        row = tx.one("SELECT from_package_version, to_package_version FROM changes WHERE target_id='pkg'")
    assert row["from_package_version"] is None and row["to_package_version"] is None


def test_015_adds_the_launch_record_and_leaves_older_runs_unknown(backend, monkeypatch):
    import histor.migrations as m

    monkeypatch.setattr(m, "MIGRATIONS", m.MIGRATIONS[:14])
    apply_migrations(backend)
    with backend.transaction() as tx:
        tx.execute("INSERT INTO observations(target_id, observed_at, status, package_version) VALUES(?, ?, 'ok', ?)",
                   ("pkg", "2026-10-26T10:00:00Z", "1.0.0"))
    monkeypatch.undo()
    assert apply_migrations(backend) == m.MIGRATIONS[-1][0]
    with backend.read() as tx:
        row = tx.one("SELECT package_version, launch FROM observations WHERE target_id='pkg'")
    assert row["package_version"] == "1.0.0" and row["launch"] is None


def test_a_failed_transaction_leaves_nothing_behind(backend):
    apply_migrations(backend)
    with pytest.raises(RuntimeError), backend.transaction() as tx:
        tx.execute("INSERT INTO meta(key, value) VALUES(?, ?)", ("k", "v"))
        raise RuntimeError("boom")
    with backend.read() as tx:
        assert tx.one("SELECT value FROM meta WHERE key=?", ("k",)) is None


def test_002_backfills_scan_sets_so_an_upgrade_reissues_nothing(backend, monkeypatch):
    """A log already in production must not re-issue 22 000 scan labels because a column appeared."""
    import histor.migrations as m

    monkeypatch.setattr(m, "MIGRATIONS", m.MIGRATIONS[:1])
    # v1 set: migration 2 added scan_sets, migration 3 the two classifier columns, 6 package_version,
    # 7 package_signals, 11 current_extras
    monkeypatch.setattr(m, "TARGET_COLUMNS", m.TARGET_COLUMNS[:-6])
    apply_migrations(backend)
    with backend.transaction() as tx:
        tx.execute("INSERT INTO meta(key, value) VALUES('pattern_set', 'sha256-P'), ('record_set', 'sha256-R')")
        for tid, toolset in (("pinned", "sha256-T"), ("never-read", None)):
            tx.execute(
                "INSERT INTO targets(id, name, endpoint, transport, registry, first_listed, last_listed, current_toolset) "
                "VALUES(?, ?, ?, 'streamable-http', 'r', 'x', 'x', ?)", (tid, tid, "https://" + tid, toolset))
    monkeypatch.undo()
    assert apply_migrations(backend) == MIGRATIONS[-1][0]
    with backend.read() as tx:
        rows = {r["id"]: r["scan_sets"] for r in tx.execute("SELECT id, scan_sets FROM targets")}
    assert rows == {"pinned": "sha256-P|sha256-R", "never-read": None}
