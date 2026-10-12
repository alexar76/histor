"""The package lane must not keep every observation alive until it ends (2026-10-12).

Crawl 33 grew to the container's 1 GiB limit and was OOM-killed 26 h in, near the end: the lane
held a list of all 8 000 Futures, and each finished Future keeps its result — tools, sandbox
trace, detail — so nothing observed was freed before the lane returned. Here each observation is
weak-referenced, and while the lane is still settling packages, the ones it settled earlier must
already be collectable.
"""

from __future__ import annotations

import gc
import weakref

from tests.conftest import tool
from tests.test_packages import FakePackages, package_target


def test_settled_observations_are_released_while_the_lane_is_still_running(world):
    s = world["services"]
    fake = FakePackages()
    s.crawler.packages = fake
    n = 12
    for i in range(n):
        t = package_target(f"io.m/p{i}", f"npm:p{i}")
        world["targets"].append(t)
        fake.latest[f"npm:p{i}"] = "1.0.0"
        fake.tools[(f"npm:p{i}", "1.0.0")] = [tool("t", "A tool.")]

    seen: list[weakref.ref] = []
    alive_counts: list[int] = []
    settle = s.crawler._settle

    def watching_settle(item, *args, **kwargs):
        if item.target.endpoint.startswith("npm:"):
            gc.collect()
            alive_counts.append(sum(1 for r in seen if r() is not None))
            seen.append(weakref.ref(item.obs))
        return settle(item, *args, **kwargs)

    s.crawler._settle = watching_settle
    s.crawler.run()
    assert len(seen) == n
    # Before the fix every earlier observation was still alive (counts 0, 1, 2, ... n-1).
    assert max(alive_counts) <= 2, alive_counts
