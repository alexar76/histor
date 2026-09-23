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
    monkeypatch.setattr(m, "TARGET_COLUMNS", m.TARGET_COLUMNS[:-1])
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
