"""Several sandbox hosts behind one reader: spread by free slot, a failing host rests, limits hold."""

from __future__ import annotations

import threading
import time

import pytest

from histor.config import SANDBOX_HOSTS, SANDBOX_SLOTS_PER_HOST, load_settings
from histor.mcpclient import Observation
from histor.packages import SANDBOX_ERROR, SandboxPool
from tests.conftest import tool
from tests.test_config import PROD, set_env
from tests.test_packages import FakePackages, package_target

PIN = "ab" * 32


class Host:
    """A sandbox host that records how many observations it ran at once."""

    def __init__(self, status="ok", hold=0.05):
        self.status, self.hold = status, hold
        self.now = self.peak = 0
        self.seen: list[str] = []
        self.lock = threading.Lock()

    def observe(self, registry, name, version):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.seen.append(name)
        time.sleep(self.hold)
        with self.lock:
            self.now -= 1
        return Observation(status=self.status, detail="sandbox answered 503 busy" if self.status == SANDBOX_ERROR else "")

    def latest_version(self, registry, name):
        return "9.9.9"

    def version_facts(self, registry, name, version):
        return {"version": version}

    def close(self):
        pass


def run_all(pool, names):
    out = {}
    threads = [threading.Thread(target=lambda n=n: out.__setitem__(n, pool.observe("npm", n, None))) for n in names]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return out


def test_packages_spread_over_hosts_and_no_host_runs_more_than_its_slots():
    a, b = Host(), Host()
    pool = SandboxPool([(a, 1), (b, 3)])
    assert pool.capacity == 4
    run_all(pool, [f"p{i}" for i in range(12)])
    assert a.peak <= 1 and b.peak <= 3
    assert a.seen and b.seen and len(a.seen) + len(b.seen) == 12
    assert pool.take_counts() == {"1": {"observed": len(a.seen), "errors": 0},
                                  "2": {"observed": len(b.seen), "errors": 0}}
    assert pool.take_counts()["1"] == {"observed": 0, "errors": 0}, "counting starts again"


def test_a_failing_host_hands_the_package_to_another_and_rests():
    down, up = Host(status=SANDBOX_ERROR, hold=0), Host(hold=0)
    pool = SandboxPool([(down, 2), (up, 2)])
    assert pool.observe("npm", "first", None).status == "ok", "retried on the other host"
    assert down.seen == ["first"] and up.seen == ["first"]
    for i in range(5):
        assert pool.observe("npm", f"next{i}", None).status == "ok"
    assert down.seen == ["first"], "a resting host is passed over while another has a free slot"
    assert pool.take_counts() == {"1": {"observed": 0, "errors": 1}, "2": {"observed": 6, "errors": 0}}


def test_a_package_result_is_never_retried_and_both_hosts_failing_is_ours():
    a, b = Host(status="exited", hold=0), Host(hold=0)
    pool = SandboxPool([(a, 1), (b, 1)])
    assert pool.observe("npm", "x", None).status == "exited" and b.seen == [], "the package failed, not the host"
    both = SandboxPool([(Host(status=SANDBOX_ERROR, hold=0), 1), (Host(status=SANDBOX_ERROR, hold=0), 1)])
    assert both.observe("npm", "x", None).status == SANDBOX_ERROR


def test_registry_questions_go_to_the_first_host_and_capacity_is_capped():
    first, second = Host(), Host()
    second.latest_version = lambda *a: pytest.fail("registry calls are not sandbox work")
    pool = SandboxPool([(first, 8), (second, 8), (Host(), 8)], max_slots=12)
    assert pool.latest_version("npm", "x") == "9.9.9" and pool.version_facts("npm", "x", "1") == {"version": "1"}
    assert pool.capacity == 12
    with pytest.raises(ValueError):
        SandboxPool([])


def sandbox_env(n=None, **kw):
    prefix = "HISTOR_SANDBOX_" if n is None else f"HISTOR_SANDBOX_{n}_"
    return {f"{prefix}URL": kw.get("url", f"https://10.0.0.{n or 1}:9443"), f"{prefix}TOKEN": "t" * 40,
            f"{prefix}CERT_SHA256": PIN, **({f"{prefix}CONCURRENCY": kw["slots"]} if "slots" in kw else {})}


@pytest.fixture
def clean(monkeypatch):
    import os
    for k in [k for k in os.environ if k.startswith("HISTOR_SANDBOX")]:
        monkeypatch.delenv(k)
    set_env(monkeypatch, **PROD)
    return monkeypatch


def test_extra_hosts_load_capped(clean):
    for k, v in {**sandbox_env(), **sandbox_env(2, slots="3"), **sandbox_env(8, slots="50")}.items():
        clean.setenv(k, v)
    s = load_settings()
    assert [(u, c) for u, _, _, c in s.sandbox_extra] == [("https://10.0.0.2:9443", 3), ("https://10.0.0.8:9443", SANDBOX_SLOTS_PER_HOST)]
    assert s.sandbox_max_slots == 12 and SANDBOX_HOSTS == 8
    assert "t" * 40 not in repr(s), "tokens stay out of the settings' repr"


@pytest.mark.parametrize("env,message", [
    ({**sandbox_env(), "HISTOR_SANDBOX_2_URL": "https://10.0.0.2:9443"}, "go together"),
    ({**sandbox_env(), **sandbox_env(9)}, "at most 8"),
    (sandbox_env(2), "need the first host"),
    ({**sandbox_env(), **sandbox_env(2, url="https://10.0.0.1:9443")}, "listed twice"),
])
def test_a_misconfigured_host_stops_the_start(clean, env, message):
    for k, v in env.items():
        clean.setenv(k, v)
    with pytest.raises(RuntimeError, match=message):
        load_settings()


def test_a_crawl_records_what_each_host_did(world):
    s = world["services"]
    one, two = FakePackages(), FakePackages()
    two.fail = True
    for fake in (one, two):
        fake.latest["npm:@a/mem"] = "1.0.0"
        fake.tools[("npm:@a/mem", "1.0.0")] = [tool("read_graph", "Read the knowledge graph.")]
    s.crawler.packages = SandboxPool([(two, 1), (one, 1)])
    world["targets"].append(package_target())
    stats = s.crawler.run()
    assert stats["statuses"] == {"ok": 1} and "internal_errors" not in stats
    assert stats["sandbox_hosts"] == {"1": {"observed": 0, "errors": 1}, "2": {"observed": 1, "errors": 0}}


def test_histor_sandboxes_checks_every_host_and_never_prints_a_token(clean, monkeypatch, capsys):
    from histor.__main__ import main
    from histor.packages import PackageReader

    for k, v in {**sandbox_env(), **sandbox_env(2, slots="4")}.items():
        clean.setenv(k, v)
    good = {"observer": "3", "gvisor": True, "trace": True, "observeNetwork": True}

    def selftest(reader):
        if reader.host == "10.0.0.2":
            raise OSError("connection refused")
        return good

    monkeypatch.setattr(PackageReader, "selftest", selftest)
    assert main(["sandboxes"]) == 1, "one host down fails the check"
    out = capsys.readouterr().out
    assert '"host": 1' in out and '"ok": true' in out and '"host": 2' in out and "OSError" in out
    assert "t" * 40 not in out
    monkeypatch.setattr(PackageReader, "selftest", lambda reader: {**good, "observer": "2"})
    assert main(["sandboxes"]) == 1, "a host on another observer version is not ready"
    monkeypatch.setattr(PackageReader, "selftest", lambda reader: good)
    assert main(["sandboxes"]) == 0
