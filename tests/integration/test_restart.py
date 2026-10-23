"""A HISTOR process killed with SIGKILL in the middle of a crawl, then started again.

What a redeploy does to the live service, with nothing simulated about the kill: no exception is
raised in the crawler, no ``finally`` runs, the process is simply gone, with work in flight. The
next process must continue the cycle: read nothing again that was committed, ask no registry for
versions again, keep to the cycle's package budget, and report the whole cycle.

    pytest -m integration tests/integration/test_restart.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from histor.db import open_backend
from histor.store import Store
from tests.conftest import PG_URL, SCANNER_READY, _reset_postgres
from tests.integration.world import World, free_port

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not SCANNER_READY, reason="needs node and scanner/node_modules (npm ci --prefix scanner)"),
]

RUNNER = Path(__file__).with_name("restart_runner.py")
OP = {"x-histor-operator": "t" * 32}
PACKAGES = [f"npm:p{i}" for i in range(8)]
ENDPOINTS = 12


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    if request.param == "postgresql":
        if not PG_URL:
            pytest.skip("HISTOR_TEST_DATABASE_URL not set — Postgres half not exercised")
        _reset_postgres(PG_URL)
        return PG_URL
    return ""


class Service:
    """One HISTOR process; ``start`` again after ``kill`` is the next deploy."""

    def __init__(self, tmp: Path, registry: str, database_url: str) -> None:
        self.tmp, self.port, self.proc = tmp, free_port(), None
        self.env = {**os.environ, "HISTOR_DATA_DIR": str(tmp / "data"), "HISTOR_PORT": str(self.port),
                    "HISTOR_OPERATOR_TOKEN": "t" * 32, "HISTOR_REGISTRY_URL": registry,
                    "HISTOR_ALLOW_PRIVATE_TARGETS": "1", "HISTOR_CRAWL_ON_START": "0",
                    "HISTOR_CRAWL_INTERVAL_S": "86400", "HISTOR_CRAWL_BATCH": "2", "HISTOR_CRAWL_WORKERS": "2",
                    "HISTOR_CRAWL_PER_HOST": "2", "HISTOR_CRAWL_TIMEOUT_S": "10", "HISTOR_DATABASE_URL": database_url,
                    "RUNNER_STATE": str(tmp), "RUNNER_PACKAGE_DELAY": "0.6"}
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)

    def start(self, budget: int) -> None:
        env = {**self.env, "HISTOR_SANDBOX_MAX_PER_CRAWL": str(budget)}
        self.proc = subprocess.Popen([sys.executable, str(RUNNER)], env=env, cwd=str(RUNNER.parents[2]))
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if self.http.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"HISTOR exited with {self.proc.returncode}")
            time.sleep(0.2)
        raise RuntimeError("HISTOR did not start")

    def stats(self) -> dict:
        return self.http.get("/api/v1/stats").json()

    def crawl(self) -> None:
        assert self.http.post("/api/v1/admin/crawl", headers=OP).status_code == 202

    def wait_finished(self, after_run: int | None) -> dict:
        deadline = time.time() + 240
        while time.time() < deadline:
            st = self.stats()
            last = st.get("lastRun") or {}
            if not st["crawl"]["running"] and last.get("id") and last["id"] != after_run:
                return last
            time.sleep(0.25)
        raise AssertionError("the crawl did not finish")

    def kill(self) -> None:
        os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.kill()
        self.http.close()


def sandbox_log(tmp: Path) -> list[str]:
    path = tmp / "sandbox.log"
    return path.read_text().splitlines() if path.exists() else []


def test_a_killed_process_continues_its_crawl_where_it_stopped(tmp_path, database):
    (tmp_path / "versions.json").write_text(json.dumps(dict.fromkeys(PACKAGES, "1.0.0")))
    with World() as world:
        for i in range(ENDPOINTS):
            world.add(f"io.example/s{i:02d}", [{"name": f"t{i}", "description": f"Tool {i}.",
                                                "inputSchema": {"type": "object"}}], delay=0.3)
        svc = Service(tmp_path, world.base, database)
        try:
            # Cycle 1, undisturbed: every endpoint read, every package observed at 1.0.0.
            svc.start(budget=len(PACKAGES))
            svc.crawl()
            first = svc.wait_finished(None)
            assert first["error"] is None and first["attempted"] == ENDPOINTS + len(PACKAGES)

            # Cycle 2: every package has a new version; the budget lets 5 of the 8 run.
            (tmp_path / "versions.json").write_text(json.dumps(dict.fromkeys(PACKAGES, "2.0.0")))
            svc.kill()
            svc.start(budget=5)
            log_before, calls_before = len(sandbox_log(tmp_path)), len(world.calls)
            svc.crawl()
            deadline = time.time() + 120
            while True:   # kill it once some endpoints and some packages are committed, the rest in flight
                progress = svc.stats()["crawl"].get("progress") or {}
                packages_done = (progress.get("packages") or {}).get("done", 0)
                if packages_done >= 2 and progress.get("done", 0) - packages_done >= 4:
                    break
                assert time.time() < deadline, f"no progress to interrupt: {progress}"
                time.sleep(0.05)
            svc.kill()

            backend = open_backend(tmp_path / "data" / "histor.sqlite3", database)
            store = Store(backend)
            killed = store.last_run(finished_only=False)
            cycle = killed["stats"]["cycle_started_at"]
            committed = store.attempted_since(cycle)
            killed_stats = killed["stats"]
            backend.close()
            assert killed["finished_at"] is None, "SIGKILL: nothing in the process could close the run"
            assert sum(killed_stats.get("statuses", {}).values()) >= 1 and killed_stats.get("progress_at"), \
                "the counts were saved while it ran"
            lookups_cycle2 = [x for x in sandbox_log(tmp_path)[log_before:] if x.startswith("lookup")]
            assert len(lookups_cycle2) == len(PACKAGES), "cycle 2 asked for every known version once"
            ran_before = {x.split()[1] for x in sandbox_log(tmp_path)[log_before:] if x.startswith("run")}
            read_before = {name for name, method in world.calls[calls_before:] if method == "tools/list"}
            log_mark, calls_mark = len(sandbox_log(tmp_path)), len(world.calls)

            # The next deploy: same data, a new process.
            svc.start(budget=5)
            svc.crawl()
            last = svc.wait_finished(killed["id"])
            after = sandbox_log(tmp_path)[log_mark:]
            read_after = [name for name, method in world.calls[calls_mark:] if method == "tools/list"]
        finally:
            svc.stop()

    assert not [x for x in after if x.startswith("lookup")], "the cycle's package plan was reused: no version lookups"
    ran_after = [x.split()[1] for x in after if x.startswith("run")]
    committed_packages = {t for t in committed if t in _package_ids()}
    assert len(committed_packages) + len(ran_after) == 5, "the cycle's budget, not a new one per run"
    assert not committed_packages & {_package_id(k) for k in ran_after}, "no committed package ran again"
    committed_endpoints = {n for n in read_before if _endpoint_id(world, n) in committed}
    assert not committed_endpoints & set(read_after), "no committed endpoint was read again"
    assert len(read_before | set(read_after)) == ENDPOINTS
    assert ran_before, "packages ran before the kill"
    assert last["error"] is None and last["cycle"]["runs"] == 2
    assert last["attempted"] == ENDPOINTS + 5 and last["statuses"] == {"ok": ENDPOINTS + 5}


def _package_id(key: str) -> str:
    from tests.test_packages import package_target
    return package_target(f"io.p/{key}", key).id


def _package_ids() -> set[str]:
    return {_package_id(k) for k in PACKAGES}


def _endpoint_id(world: World, name: str) -> str:
    from histor.registry import target_id
    return target_id(name, f"{world.base}/mcp/{name}")
