"""Regressions for the launch gaps found by the independent-capture comparison."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from histor import crawler as crawler_module
from histor.launch_profiles import PLACEHOLDER_TOKEN, package_launch_defaults
from histor.mcpclient import Observation
from tests.conftest import tool
from tests.test_observer import obs
from tests.test_packages import FakePackages, package_target, reader


@pytest.mark.parametrize("endpoint,expected", [
    ("npm:@modelcontextprotocol/server-filesystem", {"args": ["/work/app"]}),
    ("npm:@azure-devops/mcp", {"env": ["ADO_MCP_AUTH_TOKEN"],
                              "args": ["histor-sandbox", "--authentication", "envvar"]}),
    ("npm:@launchdarkly/mcp-server", {"args": ["start", "--transport=stdio", "--api-key", "histor-placeholder-token"]}),
    ("npm:figma-developer-mcp", {"env": ["FIGMA_API_KEY"], "args": ["--stdio"]}),
])
def test_reviewed_launches_reach_the_observer_and_are_kept_in_history(world, endpoint, expected):
    class ConfiguredPackages(FakePackages):
        def __init__(self):
            super().__init__()
            self.configs = []

        def observe(self, registry, name, version, **config):
            self.configs.append(config)
            obs_ = super().observe(registry, name, version)
            if config != expected:
                return Observation(status="exited", detail="Usage: configuration required", package_version=obs_.package_version,
                                   package_observer=self.observer)
            return replace(obs_, package_supplied=config)

    s = world["services"]
    fake = ConfiguredPackages()
    s.crawler.packages = fake
    t = package_target("io.profile/fixture", endpoint)
    world["targets"].append(t)
    fake.latest[endpoint] = "1.0.0"
    fake.tools[(endpoint, "1.0.0")] = [tool("read", "A fixture tool.")]
    # A failure captured before profiles existed must be retried even with the same observer.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(crawler_module, "package_launch_defaults", lambda _: {})
        s.crawler.run()
    assert fake.configs == [{}]
    world["clock"].advance(days=1)
    stats = s.crawler.run()
    assert fake.configs[-1] == expected and stats["packages"]["withConfig"] == 1
    assert s.store.target(t.id)["last_status"] == "ok"
    history = s.store.history(t.id)
    assert history[0]["package_version"] == "1.0.0"
    assert json.loads(history[0]["launch"])["supplied"] == expected
    assert history[1]["status"] == "exited", "keep the earlier unsuccessful capture"
    world["clock"].advance(days=1)
    s.crawler.run()
    assert len(fake.configs) == 2, "a completed capture is not repeatedly launched"


def test_a_first_capture_uses_the_profile_without_registry_configuration(world):
    class Capture(FakePackages):
        def observe(self, registry, name, version, **config):
            assert config == {"env": ["FIGMA_API_KEY"], "args": ["--stdio"]}
            return replace(super().observe(registry, name, version), package_supplied=config)

    fake = Capture()
    t = package_target("io.fig/fixture", "npm:figma-developer-mcp")
    fake.latest[t.endpoint] = "1.0.0"
    fake.tools[(t.endpoint, "1.0.0")] = [tool("read", "Fixture.")]
    world["targets"].append(t)
    world["services"].crawler.packages = fake
    world["services"].crawler.run()
    assert world["services"].store.target(t.id)["last_status"] == "ok"


def test_profile_identity_is_exact_and_explicit_registry_args_take_precedence():
    assert not package_launch_defaults("npm:figma-developer-mcp-lookalike")
    assert not package_launch_defaults("pypi:figma-developer-mcp")
    assert not package_launch_defaults("npm:@supabase/mcp-server-supabase"), "no invented credentials or network mode"
    t = replace(package_target(key="npm:figma-developer-mcp"), needs={"args": ["stdio"], "env": ["OTHER_API_KEY"]})
    assert crawler_module._config_for(t, {}) == {"env": ["FIGMA_API_KEY", "OTHER_API_KEY"], "args": ["stdio"]}


def test_all_profile_arguments_pass_the_standalone_sandbox_boundary():
    assert obs.PLACEHOLDER_TOKEN == PLACEHOLDER_TOKEN
    for key in ["npm:@modelcontextprotocol/server-filesystem", "npm:@azure-devops/mcp",
                "npm:@launchdarkly/mcp-server", "npm:figma-developer-mcp"]:
        cfg = package_launch_defaults(key)
        obs.check_config(cfg.get("env"), cfg.get("args"), registry="npm")
    for path in ["/", "/home/histor", "/work/pkg", "/work/app/../pkg", "/work/app\n", "../../app"]:
        with pytest.raises(obs.Refused):
            obs.check_config([], [path])


def test_placeholder_argument_is_generated_per_run_without_a_shell(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(obs, "sandbox_flags", lambda *a, **kw: ["--network", "none"])
    monkeypatch.setattr(obs, "container_started", lambda _: None)
    monkeypatch.setattr(obs, "docker", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(obs, "talk", lambda *a: {"status": "ok", "tools": []})

    def popen(command, **kwargs):
        commands.append(command)
        assert "shell" not in kwargs
        return SimpleNamespace(kill=lambda: None)

    monkeypatch.setattr(obs.subprocess, "Popen", popen)
    for run_id in ["first-run", "second-run"]:
        obs.run("npm", tmp_path, ["node", "/work/pkg/entry.js"], run_id,
                args=["start", "--api-key", PLACEHOLDER_TOKEN])
    assert commands[0][-1] == "histor-envkey-first-run"
    assert commands[1][-1] == "histor-envkey-second-run"
    assert all(PLACEHOLDER_TOKEN not in c and "--network" in c for c in commands)


@pytest.mark.parametrize("status", ["not-found", "install-failed", "ok"])
def test_requested_version_is_not_recorded_as_observed_when_the_sandbox_omits_it(status):
    r, _ = reader([(200, {"status": status, "observer": "4", "tools": []})])
    observation = r.observe("npm", "fixture", "9.9.9")
    assert observation.package_version == "", "requested version is not evidence of what ran"
