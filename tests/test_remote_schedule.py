"""Adaptive remote checks must keep running while package planning/sandboxes are busy."""

from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from histor.remote_schedule import RemoteSchedule, after
from histor.service import remote_worker
from tests.conftest import make_target, tool
from tests.test_packages import FakePackages, package_target


def enable(world):
    s = world["services"]
    s.settings = s.crawler.settings = replace(s.settings, remote_interval_s=21600, crawl_min_gap_s=0)
    s.crawler.remote_schedule = RemoteSchedule(s.store, s.settings)
    return s


def test_admin_resume_can_leave_remotes_on_the_independent_schedule(world, monkeypatch):
    from fastapi.testclient import TestClient

    from histor.app import create_app

    s = enable(world)
    seen = []
    monkeypatch.setattr("histor.app.crawl_in_background", lambda _s, **kwargs: seen.append(kwargs) or True)
    client = TestClient(create_app(s))
    headers = {"x-histor-operator": "t" * 32}
    assert client.post("/api/v1/admin/crawl?include_remote=false", headers=headers).status_code == 202
    assert client.post("/api/v1/admin/crawl", headers=headers).status_code == 202
    assert seen == [{"include_remote": False}, {"include_remote": True}]


def state(s, target):
    with s.store.db.read() as tx:
        return tx.one("SELECT * FROM remote_schedule WHERE target_id=?", (target.id,))


def test_independent_checks_detect_changes_and_accelerate_then_expire(world):
    s = enable(world)
    target = make_target("io.example/live", "/a")
    world["targets"].append(target)
    world["mcp"].tools["/a"] = [tool("a")]
    baseline = s.crawler.run(include_remote=False)
    assert baseline["attempted"] == 0
    assert s.store.target(target.id)["last_attempt"] is None
    assert s.crawler.poll_remote()["attempted"] == 1
    full_run = s.store.last_run()
    assert s.crawler.poll_remote() is None  # not due, even if a tool changes now
    for name in ("b", "c"):
        world["clock"].advance(hours=6)
        world["mcp"].tools["/a"] = [tool(name)]
        assert s.crawler.poll_remote()["changes"] == 1
    assert state(s, target)["next_due"] == after(world["clock"](), 1800)
    assert state(s, target)["last_gap_s"] == 21600
    assert s.store.last_run()["id"] == full_run["id"]
    assert s.store.last_successful_run()["id"] == full_run["id"]
    assert s.store.last_run(kind="remote")["stats"]["changes"] == 1
    # A new scheduler object sees the same due time after a restart.
    assert not RemoteSchedule(s.store, s.settings).due(world["clock"](), 20)
    world["clock"].advance(minutes=30)
    world["mcp"].tools["/a"] = [tool("d")]
    assert s.crawler.poll_remote()["changes"] == 1
    assert state(s, target)["last_gap_s"] == 1800
    assert s.store.target(target.id)["changes"] == 3
    world["clock"].advance(days=8)
    s.crawler.poll_remote()
    assert state(s, target)["hot_until"] is None
    assert state(s, target)["next_due"] == after(world["clock"](), 21600)


def test_rate_limit_pauses_the_host_and_errors_back_off_durably(world):
    s = enable(world)
    a, b = make_target("io.example/a", "/a"), make_target("io.example/b", "/b")
    other = replace(make_target("io.other/c", "/c"), endpoint="https://other.example/c")
    world["targets"].extend([a, b, other])
    s.crawler.run(include_remote=False)
    queue = s.crawler.remote_schedule
    now = world["clock"]()
    queue.record(a.id, now, "http-429")
    assert {t.id for t in queue.due(now, 20)} == {other.id}
    world["clock"].advance(hours=6)
    assert {t.id for t in queue.due(world["clock"](), 20)} == {a.id, b.id, other.id}
    queue.record(a.id, world["clock"](), "http-429")
    assert state(s, a)["next_due"] == after(world["clock"](), 43200)
    queue.record(other.id, now, "timeout")
    queue.record(other.id, after(now, 21600), "timeout")
    assert state(s, other)["next_due"] == after(now, 21600 + 43200)
    queue.record(other.id, after(now, 64800), "ok")
    assert state(s, other)["failures"] == 0


@pytest.mark.parametrize("independent", [True, False])
def test_completed_remote_is_saved_while_another_endpoint_is_still_waiting(world, independent):
    s = enable(world)
    fast, slow = make_target("io.example/fast", "/fast"), make_target("io.example/slow", "/slow")
    world["targets"].extend([fast, slow])
    world["mcp"].tools.update({"/fast": [tool("fast")], "/slow": [tool("slow")]})
    s.crawler.run(include_remote=False)
    world["clock"].advance(hours=1)
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    original_reader = s.crawler.reader.list_tools
    original_record = s.crawler.remote_schedule.record

    def read(url):
        if url.endswith("/slow"):
            entered.set()
            assert release.wait(10)
        return original_reader(url)

    def record(target_id, *args):
        original_record(target_id, *args)
        if target_id == fast.id:
            saved.set()

    s.crawler.reader.list_tools = read
    s.crawler.remote_schedule.record = record
    errors, results = [], []

    def run():
        try:
            results.append(s.crawler.poll_remote() if independent else s.crawler.run())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        assert saved.wait(5), "the completed response waited for the slow server"
        assert worker.is_alive()
        assert s.store.target(fast.id)["last_status"] == "ok"
        assert s.store.target(slow.id)["last_attempt"] is None
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and not errors
    assert results[0]["attempted"] == 2 and results[0]["statuses"] == {"ok": 2}
    assert s.store.target(slow.id)["last_status"] == "ok"


@pytest.mark.parametrize("normal_interval", [21600, 172800])
def test_rate_limit_never_shortens_normal_interval_even_for_frequent_servers(world, normal_interval):
    s = enable(world)
    s.crawler.remote_schedule.settings = replace(s.settings, remote_interval_s=normal_interval)
    target = make_target("io.example/frequent", "/a")
    world["targets"].append(target)
    s.crawler.run(include_remote=False)
    now = world["clock"]()
    with s.store.db.transaction() as tx:
        for offset in (60, 120):
            tx.execute("INSERT INTO changes(target_id, observed_at, summary) VALUES(?, ?, '{}')",
                       (target.id, after(now, -offset)))
    for _ in range(3):
        s.crawler.remote_schedule.record(target.id, now, "http-429")
        assert state(s, target)["hot_until"] is not None
        assert state(s, target)["next_due"] >= after(now, normal_interval)


def test_package_planning_does_not_block_remote_worker(world):
    s = enable(world)
    remote = make_target("io.example/live", "/a")
    world["targets"].extend([remote, package_target()])
    world["mcp"].tools["/a"] = [tool("live")]
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()
    fake = FakePackages()
    original_plan = s.crawler._cycle_packages

    def slow_plan(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_plan(*args, **kwargs)

    s.crawler._cycle_packages = slow_plan
    s.crawler.packages = fake
    original_record = s.crawler.remote_schedule.record

    def record(*args):
        original_record(*args)
        observed.set()

    s.crawler.remote_schedule.record = record
    errors = []

    def crawl():
        try:
            s.crawler.run(include_remote=False)
        except BaseException as exc:
            errors.append(exc)

    baseline = threading.Thread(target=crawl)
    worker = threading.Thread(target=remote_worker, args=(s,), kwargs={"idle_wait": 0.01})
    baseline.start()
    try:
        assert entered.wait(5)
        worker.start()
        assert observed.wait(5), "remote observation waited for package planning"
        assert baseline.is_alive()
        assert s.store.target(remote.id)["last_status"] == "ok"
    finally:
        s.stop.set()
        release.set()
        baseline.join(5)
        if worker.ident:
            worker.join(5)
    assert not errors
    assert not baseline.is_alive() and not worker.is_alive()


def test_remote_runs_do_not_skip_targets_in_a_resumed_full_crawl(world):
    s = enable(world)
    target = make_target("io.example/live", "/a")
    world["targets"].append(target)
    world["mcp"].tools["/a"] = [tool("a")]
    s.crawler.run(include_remote=False)
    since = world["clock"]()
    s.crawler.poll_remote()
    assert target.id not in s.store.attempted_since(since)
    assert s.store.cycle_totals(since)["attempted"] == 0


def test_skipped_delisted_and_packages_are_not_polled(world):
    s = enable(world)
    a = make_target("io.example/live", "/a")
    world["targets"].extend([a, make_target("io.example/skip", "/skip", skip="unsupported"), package_target()])
    s.crawler._sync_targets(world["targets"], world["clock"]())
    assert [t.id for t in s.crawler.remote_schedule.due(world["clock"](), 20)] == [a.id]
    world["clock"].advance(hours=1)
    s.crawler._sync_targets([], world["clock"]())
    assert not s.crawler.remote_schedule.due(world["clock"](), 20)


def test_new_opt_out_takes_effect_without_a_harvest_and_does_not_stall_the_queue(world):
    s = enable(world)
    target = make_target("io.example/live", "/a")
    world["targets"].append(target)
    world["mcp"].tools["/a"] = [tool("a")]
    s.crawler.run(include_remote=False)
    s.settings.opt_out_path.write_text("127.0.0.1\n")
    assert s.crawler.poll_remote() == {"attempted": 0}
    assert not world["mcp"].calls
    assert s.crawler.poll_remote() is None
    assert s.store.target(target.id)["skip_reason"] == "operator-opt-out"


def test_classifier_budget_is_shared_by_short_runs_and_survives_restart(world):
    from tests.test_classifier import make

    s = enable(world)
    s.crawler.settings = replace(s.crawler.settings, classifier_max_per_crawl=1)
    calls = []
    s.crawler.classifier = make([], calls)
    a = make_target("io.example/live", "/a")
    world["targets"].append(a)
    world["mcp"].tools["/a"] = [tool("a")]
    s.crawler.run(include_remote=False)
    s.crawler.poll_remote()
    assert len(calls) == 1
    world["clock"].advance(hours=6)
    world["mcp"].tools["/a"] = [tool("b")]
    s.crawler.poll_remote()
    assert len(calls) == 1
    with s.store.db.read() as tx:
        assert tx.one("SELECT used FROM classifier_daily_budget")["used"] == 1
    world["clock"].advance(days=1)
    s.crawler.poll_remote()
    assert len(calls) == 2
