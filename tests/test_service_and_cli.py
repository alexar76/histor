"""Scheduler cadence, background crawl bookkeeping and the command line."""

from __future__ import annotations

import threading
from dataclasses import replace

from histor import __main__ as cli
from histor.migrations import main as migrate_main
from histor.service import crawl_in_background, scheduler
from tests.conftest import make_target, tool


def wait_for(cond, timeout=10.0):
    ev = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        ev.wait(0.02)
    return False


def test_background_crawl_runs_once_at_a_time_and_records_errors(world):
    s = world["services"]
    world["mcp"].tools["/a"] = [tool("x", "x")]
    world["targets"].append(make_target("io.example/x", "/a"))
    assert crawl_in_background(s) is True
    # The run row is finished a moment before the crawl lock is released; wait for both.
    assert wait_for(lambda: s.store.last_run() is not None and s.crawler.running_since is None)
    assert s.last_error is None

    def boom(_url):
        raise RuntimeError("registry down")

    s.crawler.harvest_fn = boom
    assert crawl_in_background(s) is True
    assert wait_for(lambda: s.last_error is not None)
    assert "registry down" in s.last_error
    s.crawler.running_since = "now"
    assert crawl_in_background(s) is False
    s.crawler.running_since = None


def test_the_scheduler_crawls_when_due_and_stops(world):
    s = world["services"]
    world["mcp"].tools["/a"] = [tool("x", "x")]
    world["targets"].append(make_target("io.example/x", "/a"))
    s.settings = replace(s.settings, crawl_interval_s=3600, crawl_on_start=True)
    t = threading.Thread(target=scheduler, args=(s,), kwargs={"first_wait": 0.01, "tick": 0.05}, daemon=True)
    t.start()
    assert wait_for(lambda: s.store.last_run() is not None)
    s.stop.set()
    t.join(timeout=5)
    assert not t.is_alive()


def test_without_crawl_on_start_a_fresh_instance_waits(world):
    s = world["services"]
    s.settings = replace(s.settings, crawl_interval_s=3600, crawl_on_start=False)
    t = threading.Thread(target=scheduler, args=(s,), kwargs={"first_wait": 0.01, "tick": 0.02}, daemon=True)
    t.start()
    assert not wait_for(lambda: s.store.last_run(finished_only=False) is not None, timeout=0.3)
    s.stop.set()
    t.join(timeout=5)


def test_interval_zero_disables_the_scheduler(world):
    s = world["services"]
    s.settings = replace(s.settings, crawl_interval_s=0)
    scheduler(s, first_wait=0, tick=0)  # returns immediately


def test_cli_commands(settings, monkeypatch, capsys):
    assert cli.main(["issuer"]) == 0
    assert capsys.readouterr().out.startswith("did:key:z6Mk")
    assert cli.main(["nonsense"]) == 2
    assert migrate_main(["status"]) == 0
    assert "backend=" in capsys.readouterr().out
    assert migrate_main(["up"]) == 0
    assert migrate_main(["sideways"]) == 2


def test_an_interrupted_run_is_closed_and_retried_soon(world):
    from dataclasses import replace

    s = world["services"]
    with s.store.db.transaction() as tx:
        tx.execute("INSERT INTO runs(started_at) VALUES(?)", ("2026-09-01T00:00:00Z",))
    assert s.store.mark_interrupted_runs("2026-09-01T00:10:00Z") == 1
    last = s.store.last_run()
    assert "interrupted" in last["stats"]["error"] and s.store.last_successful_run() is None
    # Failed an hour+ ago with no success since: due now, even with a 24 h interval.
    world["mcp"].tools["/a"] = [tool("x", "x")]
    world["targets"].append(make_target("io.example/x", "/a"))
    s.settings = replace(s.settings, crawl_interval_s=86400, crawl_on_start=False)
    t = threading.Thread(target=scheduler, args=(s,), kwargs={"first_wait": 0.01, "tick": 0.05}, daemon=True)
    t.start()
    assert wait_for(lambda: s.store.last_successful_run() is not None)
    s.stop.set()
    t.join(timeout=5)
