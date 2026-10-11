"""Why a package exited, and starting it again with what it asked for (placeholders, never values)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from histor.exit_reasons import classify, declared_needs
from histor.mcpclient import Observation
from histor.registry import targets_from_servers
from tests.conftest import tool
from tests.test_packages import FakePackages, package_target, reader


@pytest.mark.parametrize("detail,expected", [
    ("HASDATA_API_KEY is not set. Create a free key at https://app.hasdata.com and set HASDATA_API_KEY.",
     {"kind": "needs-config", "env": ["HASDATA_API_KEY"]}),
    ("ERROR: define DOCMOST_URL (see .env.example)", {"kind": "needs-config", "env": ["DOCMOST_URL"]}),
    ("MsgMesh MCP server 無法啟動:環境變數 MQ_API_KEY 未設定", {"kind": "needs-config", "env": ["MQ_API_KEY"]}),
    ("usage: comem [-h] [--version] {eval,serve,dashboard} ... comem: error: the following arguments are required: command",
     {"kind": "needs-arguments", "args": ["serve"]}),
    ("usage: -c [-h] [--transport {http,stdio}] -c: error: Set QZX_API_TOKEN to a random secret",
     {"kind": "needs-arguments", "env": ["QZX_API_TOKEN"], "args": ["--transport=stdio"]}),
    ("ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x, where FastMCP was removed",
     {"kind": "sdk-incompatible"}),
    ("import comtypes  # need 'pip install comtypes'", {"kind": "needs-other-os"}),
    ("getaddrinfo ENOTFOUND api.example.com", {"kind": "needs-network"}),
    ("error: Node 24+ required, found 22.23.3", {"kind": "needs-newer-node", "node": 24}),
    ("story-vault-mcp requires Node.js >= 18 (with npx). Install Node.js", {"kind": "crashed"}),
    ("Run 'xcodebuildmcp mcp' to start the MCP server", {"kind": "needs-arguments", "args": ["mcp"]}),
    ("[supergateway] Error: You must specify one of --stdio, --sse, or --streamableHttp", {"kind": "needs-arguments", "args": ["--stdio"]}),
    ("Commands:\n  index.js stdio   Start ArgoCD MCP server using stdio.\n  index.js sse     Start it using SSE.",
     {"kind": "needs-arguments", "args": ["stdio"]}),
    ("Please provide a database URL as a command-line argument", {"kind": "needs-arguments"}),
    ("Error: no session cookie with x-transcend-active-organization-id. at resolveAuth (file:///x.mjs:1:1)", {"kind": "needs-config"}),
    ("This directory isn't set up yet. Run `ok init` first, then `ok start` again.", {"kind": "needs-config"}),
    ("", {"kind": "silent"}),
    ("Traceback (most recent call last): ZeroDivisionError", {"kind": "crashed"}),
])
def test_exit_reasons(detail, expected):
    assert classify("exited", detail) == expected


def test_names_that_steer_the_runtime_are_never_asked_for():
    got = classify("exited", "NODE_OPTIONS is required; LD_PRELOAD must be set; HISTOR_SANDBOX_TOKEN is not set; ERR_MODULE_NOT_FOUND")
    assert got == {"kind": "needs-config"}
    assert classify("ok", "anything") is None


def test_registry_declares_required_variables_by_name_only():
    pkg = {"environmentVariables": [
        {"name": "AI_2KW_API_KEY", "isRequired": True, "isSecret": True, "value": "do-not-take"},
        {"name": "AI_2KW_BASE_URL"}, {"name": "PATH", "isRequired": True}],
        "packageArguments": [{"type": "positional", "value": "serve"}, {"type": "positional", "value": "; rm -rf /"}]}
    assert declared_needs(pkg) == {"env": ["AI_2KW_API_KEY"], "args": ["serve"]}
    servers = [{"server": {"name": "io.k/key", "packages": [{"registryType": "npm", "identifier": "k-mcp", **pkg}]}}]
    assert targets_from_servers(servers, observe_packages=True)[0].needs == {"env": ["AI_2KW_API_KEY"], "args": ["serve"]}


def test_the_sandbox_is_sent_names_and_reports_what_it_supplied():
    r, calls = reader([(200, {"status": "ok", "version": "1.0.0", "observer": "3", "tools": [], "image": "node@sha256:ab",
                              "runtime": "runsc", "supplied": {"env": ["K_API_KEY"]}})])
    obs = r.observe("npm", "k", "1.0.0", env=["K_API_KEY"])
    assert calls[0]["env"] == ["K_API_KEY"] and "args" not in calls[0]
    assert obs.package_supplied == {"env": ["K_API_KEY"]}
    assert obs.package_conditions == {"image": "node@sha256:ab", "runtime": "runsc"}


class KeyedPackages(FakePackages):
    """A package that exits until it is given HASDATA_API_KEY."""

    def __init__(self):
        super().__init__()
        self.configs = []

    def observe(self, registry, name, version, **config):
        version = version or self.latest.get(f"{registry}:{name}")
        self.runs.append((registry, name, version))
        self.configs.append(config)
        if "HASDATA_API_KEY" not in (config.get("env") or []):
            return Observation(status="exited", detail="HASDATA_API_KEY is not set.", package_version=version,
                               package_observer=self.observer)
        obs = FakePackages.observe(self, registry, name, version)
        self.runs.pop()
        return replace(obs, package_supplied={"env": ["HASDATA_API_KEY"]},
                       package_conditions={"image": "node@sha256:ab", "runtime": "runsc"})


def test_a_package_that_asks_for_a_key_is_run_again_with_a_placeholder_once(world):
    s = world["services"]
    fake = KeyedPackages()
    s.crawler.packages = fake
    t = package_target()
    world["targets"].append(t)
    fake.latest["npm:@a/mem"] = "1.0.0"
    fake.tools[("npm:@a/mem", "1.0.0")] = [tool("search", "Search.")]
    s.crawler.run()
    record = json.loads(s.store.target(t.id)["package_signals"])
    assert record["exit"] == {"kind": "needs-config", "env": ["HASDATA_API_KEY"]}
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert fake.configs[-1] == {"env": ["HASDATA_API_KEY"]} and stats["packages"]["withConfig"] == 1
    state = s.store.target(t.id)
    record = json.loads(state["package_signals"])
    assert state["last_status"] == "ok" and record["supplied"] == {"env": ["HASDATA_API_KEY"]}
    assert record["conditions"]["image"] == "node@sha256:ab" and "exit" not in record


def test_a_settled_failure_is_not_run_again_until_something_changes(world):
    s = world["services"]
    fake = KeyedPackages()
    def observe(registry, name, version, **config):
        version = version or fake.latest.get(f"{registry}:{name}")
        fake.runs.append((registry, name, version))
        return Observation(status="exited", detail="Traceback: ZeroDivisionError", package_version=version, package_observer="3")

    fake.observe = observe
    s.crawler.packages = fake
    world["targets"].append(package_target())
    fake.latest["npm:@a/mem"] = "1.0.0"
    s.crawler.run()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert len(fake.runs) == 1 and stats["packages"]["settled"] == 1
    fake.latest["npm:@a/mem"] = "1.0.1"
    world["clock"].advance(days=1)
    s.crawler.run()
    assert len(fake.runs) == 2, "a new version runs again"


def test_a_registry_declared_key_is_supplied_on_the_first_run(world):
    s = world["services"]
    fake = KeyedPackages()
    s.crawler.packages = fake
    world["targets"].append(replace(package_target(), needs={"env": ["HASDATA_API_KEY"]}))
    fake.latest["npm:@a/mem"] = "1.0.0"
    fake.tools[("npm:@a/mem", "1.0.0")] = [tool("search", "Search.")]
    s.crawler.run()
    assert fake.configs == [{"env": ["HASDATA_API_KEY"]}]


class SdkPackages(FakePackages):
    """A PyPI server written for MCP SDK 1.x: it crashes on import unless pip is held to mcp<2."""

    def __init__(self):
        super().__init__()
        self.configs = []

    def observe(self, registry, name, version, **config):
        version = version or self.latest.get(f"{registry}:{name}")
        self.runs.append((registry, name, version))
        self.configs.append(config)
        if "mcp<2" not in (config.get("pins") or []):
            return Observation(status="exited", package_version=version, package_observer=self.observer,
                               detail="ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x, where FastMCP was renamed")
        obs = FakePackages.observe(self, registry, name, version)
        self.runs.pop()
        return replace(obs, package_supplied={"pins": ["mcp<2"]})


def test_a_server_broken_by_mcp_2_is_also_read_with_mcp_1_beside_the_record(world):
    s = world["services"]
    fake = SdkPackages()
    s.crawler.packages = fake
    t = package_target("io.r/radar", "pypi:model-radar-mcp")
    world["targets"].append(t)
    fake.latest["pypi:model-radar-mcp"] = "0.12.0"
    fake.tools[("pypi:model-radar-mcp", "0.12.0")] = [tool("radar", "Find models.")]
    s.crawler.run()
    def labels():
        with s.store.db.read() as tx:
            return tx.one("SELECT count(*) AS n FROM log_nodes")["n"]

    labels_before = labels()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert fake.configs[-1] == {"pins": ["mcp<2"]} and stats["packages"]["withPins"] == 1
    state = s.store.target(t.id)
    record = json.loads(state["package_signals"])
    assert state["last_status"] == "exited" and record["exit"]["kind"] == "sdk-incompatible", "a fresh install still fails"
    assert state["current_toolset"] is None, "the pinned tools are not the target's tool set"
    legacy = record["legacy"]
    assert legacy["status"] == "ok" and legacy["tools"] == ["radar"] and legacy["supplied"] == {"pins": ["mcp<2"]}
    assert labels() == labels_before, "no label is issued for it"
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert len(fake.runs) == 2 and stats["packages"]["settled"] == 1
    fake.latest["pypi:model-radar-mcp"] = "0.13.0"
    fake.tools[("pypi:model-radar-mcp", "0.13.0")] = [tool("radar", "Find models.")]
    world["clock"].advance(days=1)
    s.crawler.run()
    assert fake.configs[-1] == {}, "a new version runs as a fresh install first"
    assert "legacy" not in json.loads(s.store.target(t.id)["package_signals"])


def test_a_pinned_run_that_lists_no_tools_still_says_how_many(world):
    """An empty tool set cannot be a subject (MTL-SUBJ-002); the record said nothing where 0 was the finding."""
    s = world["services"]
    fake = SdkPackages()
    s.crawler.packages = fake
    t = package_target("io.r/empty", "pypi:empty-mcp")
    world["targets"].append(t)
    fake.latest["pypi:empty-mcp"] = "1.0.0"
    fake.tools[("pypi:empty-mcp", "1.0.0")] = []
    s.crawler.run()
    world["clock"].advance(days=1)
    s.crawler.run()
    legacy = json.loads(s.store.target(t.id)["package_signals"])["legacy"]
    assert legacy["status"] == "ok"
    assert legacy["toolCount"] == 0 and legacy["tools"] == []
    assert legacy["subjectError"] == "MTL-SUBJ-002"
    assert "toolSetDigest" not in legacy


def test_a_rerun_with_what_it_asked_for_goes_before_never_observed_packages(world, monkeypatch):
    import histor.crawler as crawler_mod

    s = world["services"]
    fake = KeyedPackages()
    s.crawler.packages = fake
    keyed = package_target("io.k/keyed", "npm:keyed")
    world["targets"].append(keyed)
    fake.latest["npm:keyed"] = "1.0.0"
    fake.tools[("npm:keyed", "1.0.0")] = [tool("t", "A tool.")]
    s.crawler.run()  # exits asking for HASDATA_API_KEY
    fresh = [package_target(f"io.f/p{n}", f"npm:p{n}") for n in range(5)]
    for t in fresh:
        world["targets"].append(t)
        fake.latest[t.endpoint] = "1.0.0"
        fake.tools[(t.endpoint, "1.0.0")] = [tool("t", "A tool.")]
    monkeypatch.setattr(crawler_mod, "curated_rank", lambda: {t.endpoint: n for n, t in enumerate(fresh)} | {"npm:keyed": 99})
    s.crawler.settings = replace(s.crawler.settings, sandbox_max_per_crawl=2)
    fake.runs.clear()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    # Both fit the cap of two; they run side by side in the package lane, so the set is what is chosen.
    assert {r[1] for r in fake.runs} == {"keyed", "p0"}, "the configured rerun and the most popular new package"
    assert stats["packages"]["withConfig"] == 1


class NodePackages(KeyedPackages):
    """An npm server that needs Node 24 and starts once it gets it."""

    def observe(self, registry, name, version, **config):
        version = version or self.latest.get(f"{registry}:{name}")
        self.runs.append((registry, name, version))
        self.configs.append(config)
        if config.get("runtime") != "node24":
            return Observation(status="exited", detail="error: Node 24+ required, found 22.23.3",
                               package_version=version, package_observer=self.observer)
        obs = FakePackages.observe(self, registry, name, version)
        self.runs.pop()
        return replace(obs, package_supplied={"runtime": "node24"},
                       package_conditions={"image": "node@sha256:24", "imageTag": "node:24-alpine", "runtime": "runsc"})


def test_a_package_that_needs_node_24_runs_again_on_the_lts_image(world):
    s = world["services"]
    fake = NodePackages()
    s.crawler.packages = fake
    t = package_target("io.t/tenjin", "npm:tenjin-cli")
    world["targets"].append(t)
    fake.latest["npm:tenjin-cli"] = "2.0.0"
    fake.tools[("npm:tenjin-cli", "2.0.0")] = [tool("t", "A tool.")]
    s.crawler.run()
    world["clock"].advance(days=1)
    s.crawler.run()
    assert fake.configs == [{}, {"runtime": "node24"}]
    record = json.loads(s.store.target(t.id)["package_signals"])
    assert s.store.target(t.id)["last_status"] == "ok" and record["conditions"]["imageTag"] == "node:24-alpine"
    world["clock"].advance(days=1)
    s.crawler.run()
    assert len(fake.runs) == 2, "an ok run on node 24 is not repeated"


def test_an_exit_recorded_before_reasons_were_read_is_read_from_the_stored_text(world):
    s = world["services"]
    fake = KeyedPackages()
    s.crawler.packages = fake
    t = package_target()
    world["targets"].append(t)
    fake.latest["npm:@a/mem"] = "1.0.0"
    fake.tools[("npm:@a/mem", "1.0.0")] = [tool("search", "Search.")]
    s.crawler.run()
    with s.store.db.transaction() as tx:  # as an older HISTOR stored it: the text, no reason
        signals = json.loads(s.store.target(t.id)["package_signals"])
        signals.pop("exit")
        tx.execute("UPDATE targets SET package_signals=?, last_detail=? WHERE id=?",
                   (json.dumps(signals), "HASDATA_API_KEY is not set.", t.id))
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert fake.configs[-1] == {"env": ["HASDATA_API_KEY"]} and stats["packages"]["withConfig"] == 1
    assert s.store.target(t.id)["last_status"] == "ok"


def test_a_pinned_run_keeps_the_fresh_install_reason_even_when_it_was_read_late(world):
    s = world["services"]
    fake = SdkPackages()
    s.crawler.packages = fake
    t = package_target("io.r/radar", "pypi:model-radar-mcp")
    world["targets"].append(t)
    fake.latest["pypi:model-radar-mcp"] = "0.12.0"
    fake.tools[("pypi:model-radar-mcp", "0.12.0")] = [tool("radar", "Find models.")]
    s.crawler.run()
    with s.store.db.transaction() as tx:  # stored before exit reasons existed
        signals = json.loads(s.store.target(t.id)["package_signals"])
        signals.pop("exit")
        tx.execute("UPDATE targets SET package_signals=? WHERE id=?", (json.dumps(signals), t.id))
    world["clock"].advance(days=1)
    s.crawler.run()
    record = json.loads(s.store.target(t.id)["package_signals"])
    assert record["exit"]["kind"] == "sdk-incompatible" and record["legacy"]["status"] == "ok"
