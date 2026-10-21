"""A restart does not cost the cycle: the next run continues it and dials nobody twice."""

from __future__ import annotations

import json

from tests.conftest import make_target, tool


def dialled(mcp) -> list[str]:
    return [path for path, method in mcp.calls if method == "tools/list"]


def three(world):
    for p in ("/a", "/b", "/c"):
        world["mcp"].tools[p] = [tool("t" + p[1:], "Tool " + p)]
        world["targets"].append(make_target("io.example" + p, p))


def interrupt_after_first(world):
    """Run a crawl that is killed after its first committed batch, as a restart would leave it."""
    s, clock = world["services"], world["clock"]
    from dataclasses import replace

    s.crawler.settings = replace(s.crawler.settings, crawl_batch=1)
    original = s.crawler._observe
    calls = {"n": 0}

    class Killed(BaseException):
        pass

    def observe(batch):
        calls["n"] += 1
        if calls["n"] == 2:
            raise Killed()
        return original(batch)

    s.crawler._observe = observe
    try:
        s.crawler.run()
    except Killed:
        pass
    s.crawler._observe = original
    # The process died: the row has no finish time until the next start marks it.
    with s.store.db.transaction() as tx:
        tx.execute("UPDATE runs SET finished_at=NULL WHERE id=(SELECT MAX(id) FROM runs)")
    clock.advance(minutes=5)
    s.store.mark_interrupted_runs(clock())


def test_the_next_run_continues_the_cycle_and_dials_nobody_twice(world):
    s, mcp = world["services"], world["mcp"]
    three(world)
    interrupt_after_first(world)
    first = s.store.last_run()
    assert "interrupted" in first["stats"]["error"]
    assert first["stats"]["cycle_started_at"] == first["started_at"]  # written at the start, kept by the mark
    assert len(dialled(mcp)) == 1
    mcp.calls.clear()

    stats = s.crawler.run()

    assert len(dialled(mcp)) == 2  # the one read before the restart is not dialled again
    assert stats["resumed_from"] == first["id"]
    assert stats["cycle_started_at"] == first["started_at"]
    assert stats["skipped_already_attempted"] == 1
    assert all(t["last_ok"] for t in s.store.all_targets())


def test_a_chain_of_restarts_keeps_the_first_start(world):
    s, mcp = world["services"], world["mcp"]
    three(world)
    interrupt_after_first(world)
    first = s.store.last_run()
    interrupt_after_first(world)  # the resumed run is killed too
    second = s.store.last_run()
    assert second["stats"]["cycle_started_at"] == first["started_at"]
    assert second["stats"]["resumed_from"] == first["id"]
    mcp.calls.clear()
    stats = s.crawler.run()
    assert stats["cycle_started_at"] == first["started_at"]
    assert len(dialled(mcp)) == 1  # two of three were read before the restarts


def test_a_cycle_older_than_the_interval_starts_afresh(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    three(world)
    interrupt_after_first(world)
    clock.advance(days=1, minutes=1)
    mcp.calls.clear()
    stats = s.crawler.run()
    assert "resumed_from" not in stats
    assert stats["cycle_started_at"] == stats["started_at"]
    assert len(dialled(mcp)) == 3


def test_a_finished_crawl_is_never_resumed(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    three(world)
    s.crawler.run()
    clock.advance(hours=2)
    mcp.calls.clear()
    stats = s.crawler.run()
    assert "resumed_from" not in stats
    assert len(dialled(mcp)) == 3


def test_after_a_restart_the_scheduler_continues_at_once(world):
    import threading
    import time
    from dataclasses import replace

    from histor.service import scheduler

    s, mcp = world["services"], world["mcp"]
    three(world)
    interrupt_after_first(world)
    first = s.store.last_run()
    mcp.calls.clear()
    s.settings = replace(s.settings, crawl_interval_s=86400, crawl_on_start=False)
    t = threading.Thread(target=scheduler, args=(s,), kwargs={"first_wait": 0.01, "tick": 0.05}, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while s.store.last_successful_run() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    s.stop.set()
    t.join(timeout=5)
    done = s.store.last_successful_run()
    assert done is not None, "an interrupted cycle must continue without waiting the retry hour"
    assert done["stats"]["resumed_from"] == first["id"]
    assert len(dialled(mcp)) == 2


def test_runs_written_by_older_code_still_chain_to_the_earliest_start(world):
    """2026-10-21: the run that read 34,000 servers overnight and the one after it were written
    by code that recorded no cycle start; both were interrupted. The cycle is the first one's."""
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    three(world)
    interrupt_after_first(world)
    night = s.store.last_run()
    with s.store.db.transaction() as tx:  # older code: no stats at all
        tx.execute("UPDATE runs SET stats=? WHERE id=?", (json.dumps({"error": "interrupted: x"}), night["id"]))
        clock.advance(hours=10)
        tx.execute("INSERT INTO runs(started_at, finished_at, stats) VALUES(?, ?, ?)",
                   (clock(), clock(), json.dumps({"error": "interrupted: x"})))
    clock.advance(hours=1)
    mcp.calls.clear()
    stats = s.crawler.run()
    assert stats["cycle_started_at"] == night["started_at"]
    assert len(dialled(mcp)) == 2


def test_a_successful_run_ends_the_chain(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    three(world)
    s.crawler.run()  # success
    clock.advance(hours=1)
    interrupt_after_first(world)
    interrupted = s.store.last_run()
    mcp.calls.clear()
    stats = s.crawler.run()
    assert stats["cycle_started_at"] == interrupted["started_at"]
    assert len(dialled(mcp)) == 2
