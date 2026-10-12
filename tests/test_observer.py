"""Synthetic boundary checks; no third-party code, Docker or external network."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "sandbox_observer", Path(__file__).parents[1] / "sandbox/histor_observe.py"
)
obs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(obs)


@pytest.mark.parametrize('body', [None, {}, {'seed': 'ok', 'index': 72},
                                  {'seed': '../escape', 'index': 0}, {'seed': 'ok', 'index': True},
                                  {'seed': 'ok', 'index': 0, 'source': 'arbitrary code'}])
def test_campaign_endpoint_never_accepts_source_or_unbounded_selectors(body):
    with pytest.raises(obs.Refused):
        obs.campaign_case(body)


def test_campaign_shares_slot_and_releases_it_on_failure(monkeypatch):
    import sys
    released = []
    monkeypatch.setitem(sys.modules, 'histor_fixtures', SimpleNamespace(
        generate=lambda seed: [{'id': 'fixture'}] * 72,
        observe_case=lambda *args: (_ for _ in ()).throw(RuntimeError('unavailable'))))
    monkeypatch.setitem(sys.modules, obs.__name__, obs)
    monkeypatch.setattr(obs, 'slot', lambda: SimpleNamespace(close=lambda: released.append(True)))
    with pytest.raises(RuntimeError):
        obs.campaign_case({'seed': 'test', 'index': 0})
    assert released == [True]


def test_missing_trace_and_missing_source_are_never_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "TRACE_ROOT", tmp_path)
    r = obs.read_trace("none", [("startup", 0, 9999999999)], "entry", None)["startup"]
    assert not r["complete"] and "trace-missing" in r["incompleteReasons"]
    dns = object.__new__(obs.DnsLogger)
    dns.records = [(2, "a", "one.test"), (2, "b", "two.test")]
    assert dns.names(None, 0, 3) == []
    assert dns.names("a", 0, 3) == ["one.test"]


def test_canary_uses_trap_before_defaults_and_prioritizes_required_fields():
    props = {f"p{i}": {"type": "string"} for i in range(25)}
    props["to"] = {"type": "string", "default": "actual@external.test"}
    args = obs.canary_args({"properties": props, "required": ["to"]}, "test")
    assert args["to"] == "canary-test@histor-trap.invalid" and len(args) == 20
    assert obs.canary_value("destination", {"format": "email"}, "test") == args["to"]
    with pytest.raises(obs.CanaryUnsupported):
        obs.canary_value("to", {"enum": ["real@external.test"]}, "test")
    with pytest.raises(obs.CanaryUnsupported):
        obs.canary_args({"properties": props, "required": list(props)}, "test")
    with pytest.raises(obs.CanaryUnsupported):
        obs.canary_value("x", {"pattern": "(a+)+$"}, "test")


def test_lifecycle_runs_without_entrypoint(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "STATE", tmp_path)
    monkeypatch.setattr(obs, "resolve", lambda *a: {"version": "1", "integrity": "sha256:fixture"})
    monkeypatch.setattr(obs, "image_digest", lambda *a: "image-fixture")
    monkeypatch.setattr(obs, "slot", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(obs, "install", lambda *a: (True, ""))
    monkeypatch.setattr(obs, "npm_entry", lambda *a: None)
    monkeypatch.setattr(obs, "traced_available", lambda: True)
    monkeypatch.setattr(obs, "lifecycle_packages", lambda *a: ["fixture"])
    called = []

    def scripts(*args):
        called.append(args)
        return {"complete": True, "decoys": [".ssh/id_rsa"], "exitCode": 0}

    monkeypatch.setattr(obs, "run_scripts", scripts)
    result = obs.observe("npm", "fixture", "1")
    assert called and result["status"] == "no-entry-point"
    assert result["behaviour"]["installScripts"]["decoys"] == [".ssh/id_rsa"]
    assert result["behaviour"]["complete"] is False


def test_call_errors_timeouts_and_limit_are_not_success(monkeypatch):
    tools = [{"name": f"t{i}", "inputSchema": {"type": "object"}} for i in range(17)]

    class Session:
        def __init__(self, *a):
            pass

        def send(self, *a):
            pass

        def call(self, rid, method, params, wait):
            if method == "initialize":
                return {"result": {}}
            if method == "tools/list":
                return {"result": {"tools": tools}}
            if rid == 10000:
                return {"error": {"code": 1}}
            if rid == 10001:
                return {"result": {"isError": True, "content": []}}
            if rid == 10002:
                raise TimeoutError
            return {"result": {"content": []}}

    monkeypatch.setattr(obs, "Session", Session)
    marks = {}
    result = obs.talk(None, obs.time.monotonic(), "test", True, marks)
    assert result["status"] == "ok"
    assert marks["coverage"]["successful"] == 12
    assert marks["coverage"]["errors"] == 2 and marks["coverage"]["timeouts"] == 1
    assert marks["coverage"]["skipped"] == 2 and not marks["callsComplete"]


def test_failed_open_does_not_report_secret_read(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "TRACE_ROOT", tmp_path)
    trace = tmp_path / "c"
    trace.mkdir()
    log = trace / "runsc.log.fixture.boot.txt"
    log.write_text(
        "I1010 12:00:00.000000 ] [ 2: 2] X openat(AT_FDCWD, 0x123 /home/histor/.ssh/id_rsa, O_RDONLY) = -1 ENOENT\n"
    )
    r = obs.read_trace("c", [("startup", 0, 9999999999)], "entry", "1")["startup"]
    assert not r.get("decoys")
    log.write_text(log.read_text().replace("= -1 ENOENT", "= 3 OK"))
    assert obs.read_trace("c", [("startup", 0, 9999999999)], "entry", "1")["startup"]["decoys"] == [
        ".ssh/id_rsa"
    ]


def test_real_gvisor_dns_netlink_and_direct_udp_destinations(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "TRACE_ROOT", tmp_path)
    trace = tmp_path / "c"
    trace.mkdir()
    prefix = "I1010 12:00:00.000000 1 strace.go:607] [ 1: 1] npm X "
    calls = [
        "socket(AF_NETLINK, SOCK_RAW|SOCK_CLOEXEC, NETLINK_ROUTE) = 17 (0x11) (43µs)",
        "sendto(0x11 socket:[1], 0xabc, 0x14, 0x0, null, 0x0) = 20 (0x14) (1µs)",
        "sendto(0x12 socket:[4], 0xabc, 0x28, 0x4000, 0xdef {Family: AF_INET, Addr: 10.231.0.1, Port: 53}, 0x10) = 40 (0x28) (1µs)",
        "sendto(0x13 socket:[5], 0xabc, 0x28, 0x4000, 0xdef {Family: AF_INET, Addr: 203.0.113.9, Port: 9000}, 0x10) = 40 (0x28) (1µs)",
        "open(0xabc /home/histor/.ssh/id_rsa, O_RDONLY) = 19 (0x13) (1µs)",
    ]
    log = trace / "runsc.log.fixture.boot.txt"
    log.write_text("\n".join(prefix + c for c in calls))
    evidence = obs.read_trace("c", [("startup", 0, 9999999999)], "npm", "1")["startup"]
    assert evidence["complete"] and evidence["network"] == ["203.0.113.9:9000"]
    assert evidence["decoys"] == [".ssh/id_rsa"]  # PID 1 is never blanket-excluded
    log.write_text(prefix + calls[1])
    evidence = obs.read_trace("c", [("startup", 0, 9999999999)], "npm", "1")["startup"]
    assert not evidence["complete"] and "socket-destination-unresolved" in evidence["incompleteReasons"]


def test_container_address_retries_daemon_timeout_and_empty_network(tmp_path, monkeypatch):
    import subprocess

    cidfile = tmp_path / "cid"
    cidfile.write_text("container")
    replies = iter([
        subprocess.TimeoutExpired("docker inspect", 10),
        SimpleNamespace(returncode=0, stdout=""),
        SimpleNamespace(returncode=0, stdout="10.231.0.2\n"),
    ])

    def inspect(*args, **kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(obs, "docker", inspect)
    monkeypatch.setattr(obs.time, "sleep", lambda _: None)
    assert obs.container_ip(cidfile) == ("container", "10.231.0.2")


@pytest.mark.parametrize("env,args", [
    (["PATH"], []), (["NODE_OPTIONS"], []), (["LD_PRELOAD"], []), (["HISTOR_SANDBOX_TOKEN"], []),
    (["lower_case"], []), (["NOUNDERSCORE"], []), (["A_KEY=1"], []), ([f"K{i}_KEY" for i in range(9)], []),
    ([], ["; rm -rf /"]), ([], ["$(id)"]), ([], ["--x=$HOME"]), ([], ["serve"] * 7), ([1], []),
])
def test_configuration_is_names_and_plain_arguments_only(env, args):
    with pytest.raises(obs.Refused):
        obs.check_config(env, args)


def test_placeholders_are_made_here_and_recognisable():
    assert obs.check_config(["HASDATA_API_KEY", "HASDATA_API_KEY"], ["serve", "--transport=stdio"]) == (
        ["HASDATA_API_KEY"], ["serve", "--transport=stdio"], [], None)
    assert obs.env_value("HASDATA_API_KEY", "r1") == "histor-envkey-r1"
    assert obs.env_value("ACME_BASE_URL", "r1").endswith(".histor-trap.invalid")
    assert obs.env_value("NOTES_DIR", "r1") == obs.APP_IN
    assert "histor-canary-" not in obs.env_value("X_TOKEN", "r1"), "a placeholder key is not a decoy"


def test_the_published_test_conditions_are_the_observers_own_numbers():
    import re as _re
    page = (Path(__file__).parents[1] / "docs/landing/index.html").read_text(encoding="utf-8")
    text = {k: v for k, v in _re.findall(r'data-i="(c\dp)">([^<]+)<', page)}
    src = (Path(__file__).parents[1] / "sandbox/histor_observe.py").read_text(encoding="utf-8")
    assert '"--memory", "512m"' in src and '"--cpus", "1"' in src and "/tmp:rw,size=64m" in src
    assert "512 MB" in text["c6p"] and "one CPU" in text["c6p"] and "64 MB" in text["c6p"]
    assert f"{obs.INSTALL_TIMEOUT_S} s to install" in text["c6p"] and f"{obs.RUN_TIMEOUT_S} s to start" in text["c6p"]
    assert f"up to {obs.CALL_LIMIT} tools" in text["c6p"] and f"{obs.CALL_TIMEOUT_S} s per call" in text["c6p"]
    assert obs.IMAGES["npm"] in text["c2p"] and obs.IMAGES["pypi"] in text["c2p"]


@pytest.mark.parametrize("pins,registry", [(["mcp<3"], "pypi"), (["requests==2.0"], "pypi"), (["mcp<2"], "npm"),
                                            (["mcp<2; os.system('id')"], "pypi"), (["--index-url=https://x.invalid"], "pypi")])
def test_only_listed_pins_reach_pip(pins, registry):
    with pytest.raises(obs.Refused):
        obs.check_config([], [], pins, registry)


def test_the_mcp_1x_pin_goes_to_pip_next_to_the_package(tmp_path, monkeypatch):
    assert obs.check_config([], [], ["mcp<2"], "pypi")[2] == ["mcp<2"]
    seen = []
    monkeypatch.setattr(obs, "docker", lambda *a, **k: seen.append(a) or SimpleNamespace(returncode=0, stderr="", stdout=""))
    obs.install("pypi", "model-radar-mcp", "0.12.0", tmp_path, "r1", ["mcp<2"])
    cmd = seen[0]
    assert cmd[-2:] == ("model-radar-mcp==0.12.0", "mcp<2") and "--only-binary=:all:" in cmd


@pytest.mark.parametrize("runtime,registry", [("node25", "npm"), ("node24", "pypi"), ("../node", "npm"), (["node24"], "npm")])
def test_only_listed_runtimes(runtime, registry):
    with pytest.raises(obs.Refused):
        obs.check_config([], [], [], registry, runtime)


def test_a_node24_run_uses_the_lts_image_and_says_so():
    assert obs.check_config([], [], [], "npm", "node24")[3] == "node24"
    assert obs.image_for("npm", "node24") == "histor-node:24" and obs.image_for("npm") == obs.IMAGES["npm"]


def test_a_failure_keeps_the_message_above_the_stack():
    text = "Error: Node 24+ required, found 22.23.3\n" + "    at frame (file:///x.js:1:1)\n" * 200
    kept = obs.head_tail(text)
    assert kept.startswith("Error: Node 24+ required") and " … " in kept and len(kept) < 1300


@pytest.mark.parametrize("spec,expected", [
    (None, (None, True)), ("", (None, True)), (">=3.10", (None, True)), (">=3.13", ("py313", True)),
    (">=3.14", ("py314", True)), ("<3.14,>=3.13", ("py313", True)), ("~=3.13", ("py313", True)),
    ("==3.13.*", ("py313", True)), (">=3.12.4", (None, True)), ("!=3.12.*", ("py313", True)),
    ("<3.11", (None, False)), (">=3.15", (None, False)), ("===weird", (None, True)),
])
def test_the_python_a_package_requires_is_the_image_it_runs_in(spec, expected):
    assert obs.python_for(spec) == expected


def test_a_package_that_admits_no_sandbox_python_says_so_without_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "STATE", tmp_path)
    monkeypatch.setattr(obs, "resolve", lambda *a: {"version": "1", "has_wheel": True, "requires_python": ">=3.15"})
    monkeypatch.setattr(obs, "slot", lambda: (_ for _ in ()).throw(AssertionError("no slot taken")))
    result = obs.observe("pypi", "future-mcp", "1")
    assert result["status"] == "install-failed" and "3.12, 3.13, 3.14" in result["detail"]


def test_a_newer_python_package_installs_and_runs_in_its_image(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "STATE", tmp_path)
    monkeypatch.setattr(obs, "resolve", lambda *a: {"version": "1", "has_wheel": True, "requires_python": ">=3.13"})
    monkeypatch.setattr(obs, "image_digest", lambda registry, image=None: f"{image}@sha256:x")
    monkeypatch.setattr(obs, "slot", lambda: SimpleNamespace(close=lambda: None))
    images = []
    monkeypatch.setattr(obs, "install", lambda *a: images.append(a[-1]) or (False, "stop here"))
    result = obs.observe("pypi", "steam-mcp", "1")
    assert images == ["histor-python:3.13"] and result["imageTag"] == "histor-python:3.13"
    assert result["supplied"] == {"env": [], "args": [], "pins": []}, "the observer's own choice is not a supplied runtime"


@pytest.mark.parametrize("names,own,expected", [
    (["memory", "memory-mcp"], {"memory"}, "memory-mcp"),
    (["outrank-cli", "outrank-mcp"], {"outrank-cli"}, "outrank-mcp"),
    (["foo-mcp", "foo-mcp-server"], {"foo-mcp"}, "foo-mcp"),
    (["foo", "foo-mcp-setup"], {"foo"}, "foo"),
    (["mcp"], {"mcp"}, "mcp"),
    (["autonomous", "autonomousmcp", "autonomous-win"], {"autonomousmcp"}, "autonomousmcp"),
    (["gh-mcp", "gh-mcp-auth"], {"gh"}, "gh-mcp"),
])
def test_the_command_that_starts_the_server_is_preferred(names, own, expected):
    assert obs.pick_command(names, own) == expected


def test_npm_runs_the_mcp_bin_and_says_which(tmp_path):
    pkg = tmp_path / "pkg/node_modules/@aictx/memory"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"bin": {"memory": "dist/cli/main.js", "memory-mcp": "dist/mcp/server.js"}}')
    assert obs.npm_entry(tmp_path, "@aictx/memory") == (
        ["node", "/work/pkg/node_modules/@aictx/memory/dist/mcp/server.js"], "bin memory-mcp")


def test_a_pypi_package_without_a_command_runs_as_python_m(tmp_path):
    info = tmp_path / "pkg/vault_mcp-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "RECORD").write_text("vault_mcp/__init__.py,sha256=a,1\nvault_mcp/__main__.py,sha256=b,2\n"
                                 "vault_mcp/sub/__main__.py,sha256=c,3\n../../bin/evil/__main__.py,sha256=d,4\n")
    command, label = obs.pypi_entry(tmp_path, "vault-mcp")
    assert label == "python -m vault_mcp" and command[:2] == ["python", "-c"] and "run_module('vault_mcp'" in command[2]
    (info / "RECORD").write_text("vault_mcp/__init__.py,sha256=a,1\n")
    assert obs.pypi_entry(tmp_path, "vault-mcp") is None


def test_a_pypi_console_script_still_wins(tmp_path):
    info = tmp_path / "pkg/vault_mcp-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "RECORD").write_text("vault_mcp/__main__.py,sha256=b,2\n")
    (info / "entry_points.txt").write_text("[console_scripts]\nvault = vault_mcp.cli:main\nvault-mcp = vault_mcp.server:run\n")
    command, label = obs.pypi_entry(tmp_path, "vault-mcp")
    assert label == "script vault-mcp" and "'vault_mcp.server'" in command[2]


def test_an_exit_keeps_its_code_stderr_and_stdout():
    class Proc:
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        def communicate(self, timeout=None):
            return b"", b"warning: no config\n"

    session = SimpleNamespace(noise=b"Usage: tool [OPTIONS] COMMAND\n", buf=b"  serve  Start the server.")
    text = obs.ended_with(Proc(), session, exited=True)
    assert text.startswith("[exit code 0] warning: no config stdout: Usage: tool") and "serve  Start the server." in text
    assert not obs.ended_with(Proc(), SimpleNamespace(noise=b"", buf=b""), exited=False).startswith("[exit code")


def test_the_session_keeps_non_protocol_stdout_bounded():
    s = obs.Session.__new__(obs.Session)
    s.noise = b""
    for _ in range(1000):
        s.keep(b"x" * 100)
    assert len(s.noise) == obs.MAX_NOISE
    s.noise = b""
    s.keep(b"   ")
    assert s.noise == b""


def test_the_run_names_its_user_and_writes_only_its_own_package(tmp_path):
    flags = obs.sandbox_flags("r1", tmp_path, False, tmp_path / "cid")
    assert f"{tmp_path}/etc/passwd:/etc/passwd:ro" in flags and "USER=histor" in flags
    passwd = (tmp_path / "etc/passwd").read_text()
    assert f"histor:x:{obs.os.getuid()}:{obs.os.getgid()}:" in passwd and "--read-only" in flags
    src = (Path(__file__).parents[1] / "sandbox/histor_observe.py").read_text(encoding="utf-8")
    assert 'sandbox_flags(run_id + "-r", work, traced, cidfile, package_rw=True)' in src


def test_an_image_built_on_the_host_is_named_by_its_id(monkeypatch):
    monkeypatch.setattr(obs, "docker", lambda *a, **k: SimpleNamespace(returncode=0, stdout="[] sha256:abc\n", stderr=""))
    assert obs.image_digest("npm") == "histor-node:22@sha256:abc"
    monkeypatch.setattr(obs, "docker", lambda *a, **k: SimpleNamespace(returncode=0, stdout='["node@sha256:d"] sha256:abc\n', stderr=""))
    assert obs.image_digest("npm") == "node@sha256:d"
    monkeypatch.setattr(obs, "docker", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no such image"))
    assert obs.image_digest("npm") is None


def test_every_image_the_observer_names_is_built_by_install_sh():
    script = (Path(__file__).parents[1] / "sandbox/install.sh").read_text(encoding="utf-8")
    for image in [*obs.IMAGES.values(), *(i for by in obs.RUNTIMES.values() for i in by.values())]:
        assert f"build_image {image} " in script, image
    for name in ("node", "python"):
        dockerfile = (Path(__file__).parents[1] / f"sandbox/images/{name}.Dockerfile").read_text(encoding="utf-8")
        assert "git" in dockerfile and "temurin:21-jre" in dockerfile
        assert not any(p in dockerfile for p in ("docker.io", "docker-ce", "docker.sock")), "never Docker in the sandbox"
