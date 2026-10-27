"""Protocol changes stay visible even when both observations have the same tool definitions."""

from __future__ import annotations

import gzip
import json
from dataclasses import replace

from fastapi.testclient import TestClient

from histor import dataset
from histor.app import create_app
from tests.conftest import make_target, tool
from tests.test_packages import FakePackages, package_target


def test_timeline_does_not_collapse_different_protocols(world, monkeypatch):
    services = world["services"]
    target = make_target("io.example/protocol", "/a")
    world["targets"].append(target)
    world["mcp"].tools["/a"] = [tool("read")]
    services.crawler.run()
    original = services.crawler.reader.list_tools
    monkeypatch.setattr(services.crawler.reader, "list_tools",
                        lambda url: replace(original(url), protocol_version="2026-07-28"))
    for _ in range(2):
        world["clock"].advance(days=1)
        services.crawler.run()
    with TestClient(create_app(services)) as client:
        timeline = client.get(f"/api/v1/servers/{target.id}").json()["timeline"]
    assert [row["protocolVersion"] for row in timeline] == ["2026-07-28", "2025-06-18"]
    assert [row["count"] for row in timeline] == [2, 1]
    assert timeline[0]["toolSetDigest"] == timeline[1]["toolSetDigest"]


def test_package_dataset_preserves_observed_protocol(world, tmp_path):
    class Packages(FakePackages):
        def observe(self, *args, **kwargs):
            return replace(super().observe(*args, **kwargs), protocol_version="2026-07-28")

    packages = Packages()
    target = package_target()
    packages.latest[target.endpoint] = "1.0.0"
    packages.tools[(target.endpoint, "1.0.0")] = [tool("read")]
    world["targets"].append(target)
    services = world["services"]
    services.crawler.packages = packages
    services.crawler.run()
    dataset.export(services.store, services.key, tmp_path / "export")
    with gzip.open(tmp_path / "export/package_observations.jsonl.gz", "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert rows[0]["protocolVersion"] == "2026-07-28"
    assert rows[0]["packageVersion"] == "1.0.0"
