"""Why a package exited, and starting it again with what it asked for (placeholders, never values)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from awr import canonical_sri

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
    ("story-vault-mcp requires Node.js >= 18 (with npx). Install Node.js", {"kind": "needs-program"}),
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
    r, calls = reader([(200, {"status": "ok", "version": "1.0.0", "observer": "4", "tools": [], "image": "node@sha256:ab",
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
        return Observation(status="exited", detail="Traceback: ZeroDivisionError", package_version=version, package_observer=fake.observer)

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
    """The record said nothing where 0 was the finding. Since MTL/1.1 an empty set is digested too."""
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
    assert "subjectError" not in legacy
    assert legacy["toolSetDigest"] == canonical_sri([])


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


class ObserverPackages(FakePackages):
    """Packages named ``bad*`` exit with an error of their own under any observer."""

    def observe(self, registry, name, version, **config):
        version = version or self.latest.get(f"{registry}:{name}")
        if name.startswith("bad"):
            self.runs.append((registry, name, version))
            return Observation(status="exited", detail="Traceback: ZeroDivisionError", package_version=version,
                               package_observer=self.observer)
        return FakePackages.observe(self, registry, name, version)


@pytest.mark.parametrize("older_observer", ["3", "4"])
def test_a_newer_observer_keeps_older_successes_and_runs_older_failures_first(world, monkeypatch, older_observer):
    import histor.crawler as crawler_mod

    s = world["services"]
    fake = ObserverPackages()
    fake.observer = older_observer
    s.crawler.packages = fake
    good, bad = package_target("io.g/good", "npm:good"), package_target("io.b/bad", "npm:bad")
    for t in (good, bad):
        world["targets"].append(t)
        fake.latest[t.endpoint] = "1.0.0"
        fake.tools[(t.endpoint, "1.0.0")] = [tool("t", "A tool.")]
    s.crawler.run()
    assert s.store.target(bad.id)["last_status"] == "exited"
    fresh = [package_target(f"io.f/p{n}", f"npm:p{n}") for n in range(3)]
    for t in fresh:
        world["targets"].append(t)
        fake.latest[t.endpoint] = "1.0.0"
        fake.tools[(t.endpoint, "1.0.0")] = [tool("t", "A tool.")]
    monkeypatch.setattr(crawler_mod, "curated_rank", lambda: {t.endpoint: n for n, t in enumerate(fresh)})
    s.crawler.settings = replace(s.crawler.settings, sandbox_max_per_crawl=2)
    fake.observer = "5"
    fake.runs.clear()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    # The old complete success stands; its failure retries under 5 beside a new package.
    assert {r[1] for r in fake.runs} == {"bad", "p0"}
    assert stats["packages"]["renewed"] == 1 and stats["packages"]["renewable"] == 1 and stats["packages"]["unchanged"] == 1
    record = json.loads(s.store.target(bad.id)["package_signals"])
    assert record["observer"] == "5" and record["exit"] == {"kind": "crashed"}
    fake.runs.clear()
    world["clock"].advance(days=1)
    s.crawler.run()
    assert "bad" not in {r[1] for r in fake.runs}, "a failure the current observer saw is settled"


def test_renewed_failures_take_half_the_room_and_the_rest_when_nothing_new_waits():
    from histor.crawler import RENEWABLE, SETTLED

    assert {"install-failed", "timeout"} <= RENEWABLE and "no-wheel" not in RENEWABLE and "exited" in SETTLED
    left = 10
    assert min(8, max(left // 2, left - 3)) == 7, "three new packages waiting leave seven to renewed failures"
    assert min(8, max(left // 2, left - 40)) == 5, "a long new queue gets half"


@pytest.mark.parametrize("detail,expected", [
    ("[exit code 0] ", {"kind": "silent", "code": 0}),
    ("[exit code 0] stdout: dgmo - diagrams as code. Export PNG or SVG.", {"kind": "not-a-server", "code": 0}),
    ("[exit code 2] stdout: Usage: tdnet [OPTIONS] COMMAND\n\nCommands:\n  latest  Get latest.\n  serve   Start the server.",
     {"kind": "needs-arguments", "args": ["serve"], "code": 2}),
    ("[exit code 1] Error relocating /work/pkg/node_modules/x/bin/x: __memcpy_chk: symbol not found", {"kind": "needs-glibc", "code": 1}),
    ("Unsupported platform: linux-x64 (musl libc). Published Linux packages are glibc-only.", {"kind": "needs-glibc"}),
    ("mcpsnoop: could not open a new TTY: open /dev/tty: no such device or address", {"kind": "needs-terminal"}),
    ("Error: spawn git ENOENT at ChildProcess._handle.onexit", {"kind": "needs-program"}),
    ("[vitaminmcp] No Java found. VitaminMCP runs on the JVM, so it needs Java 21 or later on this machine.", {"kind": "needs-program"}),
    ("Error: connect ECONNREFUSED 127.0.0.1:5432", {"kind": "needs-service"}),
    ("Error [ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING]: Stripping types is unsupported for files under node_modules",
     {"kind": "broken-package"}),
    ("SyntaxError: Unexpected token 'export'", {"kind": "broken-package"}),
    ("Could not connect: unrecognized_keys: \"ErrorCode\", \"Message\", \"MessageID\", \"ok\"", {"kind": "needs-network"}),
    ("AttributeError: 'Server' object has no attribute 'list_tools'", {"kind": "sdk-incompatible"}),
    ('  File "/work/pkg/m/server.py", line 131, in <module>\n    @mcp.list_tools()\n', {"kind": "sdk-incompatible"}),
    ("excel-mcp: ExcelMcp is Windows only.", {"kind": "needs-other-os"}),
    ("[exit code 1] ⚠ Not logged in.", {"kind": "needs-config", "code": 1}),
])
def test_observer_4_exits(detail, expected):
    assert classify("exited", detail) == expected


def test_a_server_that_serves_http_is_asked_for_stdio_and_other_timeouts_are_not_read():
    assert classify("timeout", "INFO: Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)") == {
        "kind": "starts-http", "args": ["--transport=stdio"]}
    assert classify("timeout", "") is None and classify("timeout", "loading model…") is None


def test_the_stored_failure_leads_with_its_error_line():
    from histor.exit_reasons import excerpt

    trace = "Traceback (most recent call last):\n" + '  File "/work/pkg/x.py", line 1, in <module>\n' * 40 + "ValueError: MINIMAX_API_KEY is empty\n"
    short = excerpt(trace, 300)
    assert short.startswith("ValueError: MINIMAX_API_KEY is empty … Traceback") and len(short) <= 300
    assert excerpt("[exit code 1] " + trace, 300).startswith("[exit code 1] ValueError: MINIMAX_API_KEY")
    assert excerpt("short", 300) == "short"
    node = "Error: GHOST_API_URL environment variable is not set.\n" + "    at f (file:///x.js:1:1)\n" * 50
    assert excerpt(node, 300) == node[:300], "the error already comes first"


def test_a_new_version_gets_what_started_the_last_one():
    from histor.crawler import _config_for

    t = package_target("io.r/runbios", "npm:runbios-mcp")
    assert _config_for(t, {"supplied": {"env": ["RUNBIOS_API_KEY"]}}) == {"env": ["RUNBIOS_API_KEY"]}
    assert _config_for(t, {"supplied": {"env": ["A_KEY"]}, "exit": {"kind": "needs-config", "env": ["B_TOKEN"]}}) == {
        "env": ["A_KEY", "B_TOKEN"]}, "a name asked for later adds to the ones given"
    assert _config_for(t, {"supplied": {"args": ["--transport=stdio"]}, "exit": {"kind": "needs-arguments", "args": ["serve"]}}) == {
        "args": ["serve"]}, "what it asks for now wins over what it was given"
    assert _config_for(t, {"supplied": {"runtime": "node24", "pins": ["mcp<2"]}}) == {"runtime": "node24"}


@pytest.mark.parametrize("detail,arg", [
    ("[exit code 1] Usage: imagine [options] [command]\n\nOptions:\n  -V, --version  output the version number\n\nCommands:\n"
     "  mcp-serve      Start MCP server with specified transport\n  help [command] display help for command\n", "mcp-serve"),
    ("Commands:\n  mcp-serve [options]      Start MCP server with stdio transport\n  serve [options]          Start a modern-only MCP server over stdio or HTTP\n"
     "  check [options] [paths...]  Batch review files\n", "mcp-serve"),
    ('[exit code 0] stdout: Troxy: a secure control layer. "troxy mcp" starts the MCP server an AI agent connects to.', "mcp"),
    ("Commands:\n  mcp-install   Add the server to Claude Desktop config\n  status        Show status\n", None),
])
def test_a_usage_text_names_its_server_command(detail, arg):
    from histor.exit_reasons import start_argument

    assert start_argument(detail) == arg


def test_a_package_an_older_trace_reader_left_with_unplaced_sends_is_traced_again_once():
    from histor.crawler import _unplaced_sends

    old = {"observer": "gvisor-trace/2", "startup": {"network": ["sendto(0x5 socket:[2], 0x7e, 0x1, 0x0, null, 0x0) = 1 (0x1)"]}}
    assert _unplaced_sends(old)
    assert _unplaced_sends({**old, "observer": "gvisor-trace/5"}), "5 kept a closed socket's family for its fd number"
    assert _unplaced_sends({**old, "observer": "gvisor-trace/6"}), "6 could not place a send on an accepted connection"
    assert not _unplaced_sends({**old, "observer": "gvisor-trace/7"}), "7's verdict on sends stands"
    inherited = {"observer": "gvisor-trace/7", "startup": {"unexpectedRecipients": ["canary-aaaaaaaaaaaa@histor-trap.invalid"]}}
    assert _unplaced_sends(inherited), "8 no longer lets a run inherit the previous holder's canary"
    assert not _unplaced_sends({**inherited, "observer": "gvisor-trace/8"}), "8 read inherited canaries right"
    current = {"observer": "gvisor-trace/9", "startup": {"writes": ["cache.json"], "network": old["startup"]["network"]}}
    assert not _unplaced_sends(current), "the current reader's verdict stands, relative writes included"
    assert not _unplaced_sends({**current, "observer": "gvisor-trace/8"}), "8 cannot show what 9 adds"
    assert not _unplaced_sends({"observer": "gvisor-trace/2", "startup": {"network": ["api.example.com:443"]}})
    assert not _unplaced_sends(None)


def test_a_relative_write_the_old_reader_called_persistence_is_traced_again():
    from histor.crawler import _unplaced_sends

    old = {"observer": "gvisor-trace/2", "installScripts": {"writes": ["buildcheck.gypi"]}}
    assert _unplaced_sends(old)
    assert not _unplaced_sends({"observer": "gvisor-trace/2", "startup": {"writes": ["/root/.bashrc"]}}), \
        "an absolute persistence write stands"


def test_evidence_that_cannot_be_the_runs_own_is_traced_again():
    from histor.crawler import _unplaced_sends

    foreign = {"observer": "gvisor-trace/3", "startup": {"unexpectedRecipients": ["canary-51bf4b4c645e@histor-trap.invalid"]}}
    assert _unplaced_sends(foreign)
    fixture = {"observer": "gvisor-trace/3", "startup": {"lookups": ["api.momus-trap.invalid"], "requests": []}}
    assert _unplaced_sends(fixture)
    real = {"observer": "gvisor-trace/3", "startup": {"unexpectedRecipients": ["ops@example.com"], "lookups": ["api.example.com"]}}
    assert not _unplaced_sends(real), "a real extra recipient is a finding, not contamination"


def test_a_run_that_could_not_place_its_start_is_traced_again():
    from histor.crawler import _unplaced_sends

    assert _unplaced_sends({"observer": "gvisor-trace/4", "startup": {"incompleteReasons": ["start-time-unknown"]}})


def test_reobserve_runs_one_package_now_through_the_crawl_path(world):
    s = world["services"]
    fake = KeyedPackages()
    s.crawler.packages = fake
    t = package_target()
    world["targets"].append(t)
    fake.latest["npm:@a/mem"] = "1.0.0"
    fake.tools[("npm:@a/mem", "1.0.0")] = [tool("search", "Search.")]
    s.crawler.run()  # exits asking for HASDATA_API_KEY
    assert s.store.target(t.id)["last_status"] == "exited"
    fake.configs.clear()
    world["clock"].advance(minutes=5)
    rows = s.crawler.reobserve(["npm:@a/mem"])
    assert fake.configs == [{"env": ["HASDATA_API_KEY"]}], "it is given what its record says it asked for"
    assert rows[0]["before"]["status"] == "exited" and rows[0]["after"]["status"] == "ok"
    assert rows[0]["run"] == s.store.last_run(finished_only=False)["id"], "no run of its own"
    with s.store.db.read() as tx:
        assert tx.one("SELECT count(*) AS n FROM observations WHERE target_id=?", (t.id,))["n"] == 2
    with pytest.raises(KeyError):
        s.crawler.reobserve(["npm:not-listed"])


def test_a_wrong_old_record_is_traced_again_first_and_with_what_started_it(world, monkeypatch):
    import histor.crawler as crawler_mod

    s = world["services"]
    fake = KeyedPackages()
    s.crawler.packages = fake
    keyed = package_target("io.k/keyed", "npm:keyed")
    world["targets"].append(keyed)
    fake.latest["npm:keyed"] = "1.0.0"
    fake.tools[("npm:keyed", "1.0.0")] = [tool("t", "A tool.")]
    s.crawler.run()
    world["clock"].advance(days=1)
    s.crawler.run()  # second run: started with the placeholder
    assert s.store.target(keyed.id)["last_status"] == "ok"
    # An older reader left an unplaced send in its startup evidence.
    signals = json.loads(s.store.target(keyed.id)["package_signals"])
    signals["behaviour"] = {"observer": "gvisor-trace/2", "complete": False,
                            "startup": {"network": ["sendto(0x5 socket:[2], 0x7e, 0x1, 0x0, null, 0x0) = 1 (0x1)"]}}
    with s.store.db.transaction() as tx:
        tx.execute("UPDATE targets SET package_signals=? WHERE id=?", (json.dumps(signals), keyed.id))
    fresh = [package_target(f"io.f/p{n}", f"npm:p{n}") for n in range(3)]
    for t in fresh:
        world["targets"].append(t)
        fake.latest[t.endpoint] = "1.0.0"
        fake.tools[(t.endpoint, "1.0.0")] = [tool("t", "A tool.")]
    monkeypatch.setattr(crawler_mod, "curated_rank", lambda: {t.endpoint: n for n, t in enumerate(fresh)} | {"npm:keyed": 99})
    s.crawler.settings = replace(s.crawler.settings, sandbox_max_per_crawl=1)
    fake.runs.clear()
    fake.configs.clear()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert [r[1] for r in fake.runs] == ["keyed"], "the wrong record goes before every new package"
    assert fake.configs == [{"env": ["HASDATA_API_KEY"]}], "and with the key that started it"
    assert stats["packages"]["retraced"] == 1 and s.store.target(keyed.id)["last_status"] == "ok"


def test_a_failing_package_an_older_reader_left_with_unplaced_sends_is_traced_again(world):
    s = world["services"]
    fake = KeyedPackages()

    def observe(registry, name, version, **config):
        version = version or fake.latest.get(f"{registry}:{name}")
        fake.runs.append((registry, name, version))
        return Observation(status="timeout", detail="no answer to initialize", package_version=version, package_observer="4")

    fake.observe = observe
    s.crawler.packages = fake
    slow = package_target()
    world["targets"].append(slow)
    fake.latest["npm:@a/mem"] = "1.0.0"
    s.crawler.run()
    signals = json.loads(s.store.target(slow.id)["package_signals"])
    signals["behaviour"] = {"observer": "gvisor-trace/2", "complete": False,
                            "startup": {"network": ["sendto(0x5 socket:[2], 0x7e, 0x1, 0x0, null, 0x0) = 1 (0x1)"]}}
    with s.store.db.transaction() as tx:
        tx.execute("UPDATE targets SET package_signals=? WHERE id=?", (json.dumps(signals), slow.id))
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert len(fake.runs) == 2 and stats["packages"]["retraced"] == 1, "a timeout keeps no wrong evidence either"
    assert s.store.target(slow.id)["last_status"] == "timeout"



def test_an_ok_page_from_a_reader_before_renames_and_links_is_traced_once_more(world, monkeypatch):
    # Owner, 2026-10-26: ~3,900 ok, complete pages came from gvisor-trace/2-8, which did not record
    # renames, links or creat (Vulnetix GCVE-110-PYPI-2026-022091). Each is traced once more,
    # sharing half of a crawl, so never-observed packages keep the rest.
    import histor.crawler as crawler_mod

    s = world["services"]
    fake = FakePackages()
    s.crawler.packages = fake
    old = [package_target(f"io.o/o{n}", f"npm:o{n}") for n in range(4)]
    for t in old:
        world["targets"].append(t)
        fake.latest[t.endpoint] = "1.0.0"
        fake.tools[(t.endpoint, "1.0.0")] = [tool("t", "A tool.")]
    s.crawler.run()
    readers = {old[0]: "gvisor-trace/9", old[1]: "gvisor-trace/7", old[2]: "gvisor-trace/2", old[3]: "gvisor-trace/8"}
    with s.store.db.transaction() as tx:
        for t, reader_ in readers.items():
            signals = json.loads(s.store.target(t.id)["package_signals"])
            signals["behaviour"] = {**signals["behaviour"], "observer": reader_}
            tx.execute("UPDATE targets SET package_signals=? WHERE id=?", (json.dumps(signals), t.id))
    fresh = [package_target(f"io.f/p{n}", f"npm:p{n}") for n in range(3)]
    for t in fresh:
        world["targets"].append(t)
        fake.latest[t.endpoint] = "1.0.0"
        fake.tools[(t.endpoint, "1.0.0")] = [tool("t", "A tool.")]
    monkeypatch.setattr(crawler_mod, "curated_rank",
                        lambda: {t.endpoint: n for n, t in enumerate([*fresh, *old])})
    s.crawler.settings = replace(s.crawler.settings, sandbox_max_per_crawl=4)
    fake.runs.clear()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    ran = {r[1] for r in fake.runs}
    assert ran == {"o1", "o2", "p0", "p1"}, "half for the older pages, by rank; half for new packages"
    assert stats["packages"]["reread"] == 2 and stats["packages"]["rereadable"] == 3
    assert stats["packages"]["unchanged"] == 1, "the current reader's page stands"
    fake.runs.clear()
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    ran = {r[1] for r in fake.runs}
    assert "o3" in ran and not ran & {"o0", "o1", "o2"}, "each page is traced once"
    assert stats["packages"]["rereadable"] == 1


def test_only_readers_before_renames_and_links_are_traced_again():
    from histor.crawler import _missed_renames_and_links

    assert _missed_renames_and_links({"observer": "gvisor-trace/8", "complete": True})
    assert _missed_renames_and_links({"observer": "gvisor-trace/2"})
    assert not _missed_renames_and_links({"observer": "gvisor-trace/9"})
    assert not _missed_renames_and_links({"observer": "gvisor-trace/10"}), "a later reader needs its own decision"
    assert not _missed_renames_and_links({"complete": True}), "no reader named: nothing to compare"
    assert not _missed_renames_and_links(None)
