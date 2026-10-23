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


# -- 2026-10-23: what a restart still cost ----------------------------------------------------------
# Live, 20 to 22 October: one cycle restarted six times over 42 hours, then was thrown away whole
# because its START was more than a day old, and every server was dialled again. Each resumed run
# also asked npm and PyPI for every known version again (59 minutes), planned a new package
# budget, and an interrupted run kept no counts, so the desk showed an empty last run.


def test_a_long_cycle_that_kept_moving_is_continued(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    three(world)
    interrupt_after_first(world)                  # /a read at the cycle's start
    first = s.store.last_run()
    clock.advance(hours=20)
    interrupt_after_first(world)                  # /b read 20 hours in
    clock.advance(hours=10)                       # the cycle is 30 hours old; its last progress 10
    mcp.calls.clear()
    stats = s.crawler.run()
    assert stats["cycle_started_at"] == first["started_at"]
    assert dialled(mcp) == ["/c"]
    assert stats["cycle"]["attempted"] == 3 and stats["cycle"]["statuses"] == {"ok": 3}
    assert stats["cycle"]["runs"] == 3


def test_an_interrupted_run_keeps_its_counts_and_the_desk_shows_the_cycle(world, monkeypatch):
    from fastapi.testclient import TestClient

    from histor.app import create_app
    from histor.crawler import Crawler

    monkeypatch.setattr(Crawler, "CHECKPOINT_S", 0.0)
    s = world["services"]
    three(world)
    interrupt_after_first(world)
    row = s.store.last_run()
    assert row["stats"]["statuses"] == {"ok": 1} and row["stats"]["done"] == 1 and row["stats"]["progress_at"]
    assert row["stats"]["cycle"]["statuses"] == {"ok": 1}
    with TestClient(create_app(s)) as client:
        last = client.get("/api/v1/stats").json()["lastRun"]
        assert last["statuses"] == {"ok": 1} and last["attempted"] == 1 and "interrupted" in last["error"]
        s.crawler.run()
        last = client.get("/api/v1/stats").json()["lastRun"]
    assert last["statuses"] == {"ok": 3} and last["attempted"] == 3 and last["error"] is None
    assert last["cycle"] == {"startedAt": row["started_at"], "runs": 2}


def test_without_saved_counts_the_cycle_is_read_back_from_the_database(world):
    """Runs written by older code saved nothing while running; marking them still says what the cycle did."""
    s = world["services"]
    three(world)
    interrupt_after_first(world)
    stats = s.store.last_run()["stats"]
    assert stats["cycle"]["attempted"] == 1 and stats["cycle"]["statuses"] == {"ok": 1}


def test_progress_counts_the_cycle_not_the_run(world):
    from histor.crawler import Crawler

    s = world["services"]
    three(world)
    interrupt_after_first(world)
    seen = []
    original = Crawler._checkpoint

    def spy(self, run_id, tally):
        seen.append(dict(self.progress))
        return original(self, run_id, tally)

    s.crawler._checkpoint = spy.__get__(s.crawler)
    s.crawler.run()
    assert seen[0]["done"] == 2 and seen[0]["of"] == 3       # one before the restart, one now
    assert seen[0]["thisRun"] == {"done": 1, "of": 2}


def test_the_classifier_budget_is_the_cycles(world, monkeypatch):
    from dataclasses import replace

    from histor.crawler import Crawler
    from tests.test_classifier import make

    monkeypatch.setattr(Crawler, "CHECKPOINT_S", 0.0)
    s = world["services"]
    three(world)
    calls: list = []
    s.crawler.classifier = make([], calls)
    s.crawler.settings = replace(s.crawler.settings, classifier_max_per_crawl=2)
    interrupt_after_first(world)
    assert len(calls) == 1
    s.crawler.run()
    assert len(calls) == 2, "a restart must not hand the cycle a second budget of model calls"


# -- packages: one plan and one budget per cycle ------------------------------------------------------

def _package_cycle(world, monkeypatch, *, keep_plan=True):
    """Four known packages each publish a new version; the cycle's budget is 3; the lane is killed on
    its second package. Returns the fake sandbox and how often each run asked for versions."""
    from dataclasses import replace

    from tests.test_packages import FakePackages, package_target

    class Killed(BaseException):
        pass

    class Lane(FakePackages):
        capacity = 1

        def __init__(self):
            super().__init__()
            self.asked = 0
            self.kill_on = None
            self.dead = False   # a killed process runs nothing more, however many jobs were queued

        def latest_version(self, registry, name):
            self.asked += 1
            return super().latest_version(registry, name)

        def observe(self, registry, name, version):
            if self.dead or (self.kill_on is not None and len(self.runs) == self.kill_on):
                self.kill_on, self.dead = None, True
                raise Killed()
            return super().observe(registry, name, version)

    s, clock = world["services"], world["clock"]
    fake = Lane()
    s.crawler.packages = fake
    keys = [f"npm:p{i}" for i in range(4)]
    for i, key in enumerate(keys):
        world["targets"].append(package_target(f"io.p/{i}", key))
        for v in ("1.0.0", "2.0.0"):
            fake.tools[(key, v)] = [tool("t", f"Tool {v}.")]
        fake.latest[key] = "1.0.0"
    s.crawler.run()                                   # every package observed once, at 1.0.0
    for key in keys:
        fake.latest[key] = "2.0.0"
    clock.advance(days=1)
    s.crawler.settings = replace(s.crawler.settings, sandbox_max_per_crawl=3)
    fake.runs.clear()
    fake.asked = 0
    fake.kill_on = 1
    try:
        s.crawler.run()
    except Killed:
        pass
    with s.store.db.transaction() as tx:
        tx.execute("UPDATE runs SET finished_at=NULL WHERE id=(SELECT MAX(id) FROM runs)")
        if not keep_plan:
            tx.execute("DELETE FROM crawl_plans")     # a cycle started by code that kept no plan
    clock.advance(minutes=5)
    s.store.mark_interrupted_runs(clock())
    asked_first = fake.asked
    fake.asked = 0
    planned = [r[1] for r in fake.runs]
    fake.runs.clear()
    fake.dead = False
    stats = s.crawler.run()
    return fake, asked_first, planned, stats


def test_a_continued_cycle_takes_the_rest_of_its_package_plan(world, monkeypatch):
    fake, asked_first, before, stats = _package_cycle(world, monkeypatch)
    assert asked_first == 4 and len(before) == 1
    assert fake.asked == 0, "the plan is reused: no hour of version lookups after a restart"
    assert len(before) + len(fake.runs) == 3, "the cycle's budget, not a new one per run"
    assert stats["packages"]["fromPlan"] is True and stats["packages"]["doneBefore"] == 1


def test_a_cycle_without_a_plan_is_planned_with_the_budget_it_has_left(world, monkeypatch):
    fake, _, before, stats = _package_cycle(world, monkeypatch, keep_plan=False)
    assert fake.asked > 0
    assert len(before) + len(fake.runs) == 3
    assert stats["packages"]["doneBefore"] == 1 and stats["packages"]["due"] == 2


def test_the_live_chain_of_2026_10_22_resumes_its_own_cycle(world):
    """Runs 62-65 as they are in production: 62 belongs to the 20 October cycle; 63 began a new one
    (the old rule had given up on 62's); 64 and 65 continued 63's. A restart now continues 63's cycle,
    and does not reach back into the older one."""
    s = world["services"]
    now = "2026-10-23T07:30:00Z"
    rows = [("2026-10-21T19:30:47Z", "2026-10-22T14:58:52Z", "2026-10-20T20:24:17Z"),
            ("2026-10-22T14:59:08Z", "2026-10-22T17:05:44Z", "2026-10-22T14:59:08Z"),
            ("2026-10-22T17:06:00Z", "2026-10-22T17:21:29Z", "2026-10-22T14:59:08Z"),
            ("2026-10-22T17:21:32Z", None, "2026-10-22T14:59:08Z")]
    with s.store.db.transaction() as tx:
        for started, finished, cycle in rows:
            stats = {"started_at": started, "cycle_started_at": cycle}
            if finished:
                stats["error"] = "interrupted: the process restarted before the crawl finished"
            tx.execute("INSERT INTO runs(started_at, finished_at, stats) VALUES(?, ?, ?)",
                       (started, finished, json.dumps(stats)))
        # /a was read in the new cycle, an hour before the restart
        tx.execute("INSERT INTO targets(id, name, endpoint, transport, registry, first_listed, last_listed, "
                   "last_attempt, last_status) VALUES('seen', 'io.example/a', '/a', 'streamable-http', 'r', "
                   "'2026-10-22T15:00:00Z', '2026-10-22T15:00:00Z', '2026-10-23T06:30:00Z', 'ok')")
    s.store.mark_interrupted_runs(now)
    newest = s.store.last_run()
    assert newest["stats"]["cycle"]["startedAt"] == "2026-10-22T14:59:08Z"
    point = s.store.resume_point(newest["id"] + 1, now, 86400)
    assert point["cycle_started_at"] == "2026-10-22T14:59:08Z"
    assert len(point["runs"]) == 3
    # The same chain a day after its last progress: a fresh cycle.
    assert s.store.resume_point(newest["id"] + 1, "2026-10-24T06:31:00Z", 86400) is None
