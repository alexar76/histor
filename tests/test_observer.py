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


def test_sandbox_generation_matches_default_config_without_invalidating_old_successes(monkeypatch):
    from histor.config import load_settings

    monkeypatch.delenv("HISTOR_SANDBOX_OBSERVER", raising=False)
    monkeypatch.delenv("HISTOR_SANDBOX_OBSERVERS_VALID", raising=False)
    settings = load_settings()
    assert settings.sandbox_observer == obs.VERSION == "5"
    assert settings.sandbox_observers_valid == ("3", "4")


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
            if method == "server/discover":
                return {"error": {"code": -32602, "message": "not initialized"}}
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


def _protocol_fixture(monkeypatch, handler):
    """Only synthetic replies: never starts Docker, an installed package or a network client."""
    calls, sends = [], []

    class Session:
        def __init__(self, proc, deadline):
            self.deadline = deadline

        def call(self, rid, method, params, wait):
            calls.append((rid, method, params, wait))
            return handler(rid, method, params, wait)

        def send(self, message):
            sends.append(message)

    monkeypatch.setattr(obs, "Session", Session)
    return calls, sends


def _discover_result(versions=None):
    return {"result": {"resultType": "complete", "supportedVersions": versions or [obs.MODERN_PROTOCOL],
                       "capabilities": {"tools": {}}, "ttlMs": 0, "cacheScope": "public",
                       "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "fixture", "version": "self-report"}}}}


@pytest.mark.parametrize("failure", [-32601, -32602, -32000, -32700, "timeout"])
def test_stdio_discovery_falls_back_for_arbitrary_legacy_errors_or_silence(monkeypatch, failure):
    def handler(rid, method, params, wait):
        if method == "server/discover":
            assert 0 < wait <= obs.INIT_TIMEOUT_S
            if failure == "timeout":
                raise TimeoutError
            return {"error": {"code": failure, "message": "legacy rejection"}}
        if method == "initialize":
            return {"result": {"protocolVersion": obs.PROTOCOL, "serverInfo": {"name": "old", "version": "1"}}}
        assert method == "tools/list" and "_meta" not in params
        return {"result": {"tools": []}}

    calls, sends = _protocol_fixture(monkeypatch, handler)
    result = obs.talk(None, obs.time.monotonic())
    assert result == {"status": "ok", "protocolVersion": obs.PROTOCOL,
                      "serverInfo": {"name": "old", "version": "1"}, "tools": []}
    assert [call[1] for call in calls] == ["server/discover", "initialize", "tools/list"]
    assert len({call[0] for call in calls}) == len(calls)
    assert sends == [{"jsonrpc": "2.0", "method": "notifications/initialized"}]


def test_modern_stdio_paginates_and_canary_calls_with_metadata_and_no_handshake(monkeypatch):
    tool = {"name": "fixture_read", "inputSchema": {"type": "object"}}

    def handler(rid, method, params, wait):
        assert params["_meta"] == {
            "io.modelcontextprotocol/protocolVersion": obs.MODERN_PROTOCOL,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "histor-sandbox", "version": obs.VERSION},
        }
        if method == "server/discover":
            return _discover_result()
        if method == "tools/list":
            return {"result": {"resultType": "complete", "tools": [tool] if rid == 2 else [],
                               **({"nextCursor": "page-2"} if rid == 2 else {})}}
        assert method == "tools/call" and params["name"] == "fixture_read"
        return {"result": {"resultType": "complete", "content": []}}

    calls, sends = _protocol_fixture(monkeypatch, handler)
    marks = {}
    result = obs.talk(None, obs.time.monotonic(), "fixture", True, marks)
    assert result == {"status": "ok", "protocolVersion": obs.MODERN_PROTOCOL,
                      "serverInfo": {"name": "fixture", "version": "self-report"}, "tools": [tool]}
    assert [call[1] for call in calls] == ["server/discover", "tools/list", "tools/list", "tools/call"]
    assert calls[2][2]["cursor"] == "page-2"
    assert not sends and marks["callsComplete"] and marks["coverage"]["successful"] == 1


@pytest.mark.parametrize("reply", [
    {"error": {"code": -32022, "data": {"supported": ["2099-01-01", "2025-06-18"]}}},
    {"error": {"code": -32021, "data": {"requiredCapabilities": {"elicitation": {}}}}},
    {"error": {"code": -32020, "message": "metadata mismatch"}},
    {"result": {"resultType": "complete", "supportedVersions": ["2099-01-01", "2025-06-18"]}},
    {"result": {"resultType": "input_required", "inputRequests": []}},
    {"result": {"resultType": "complete", "supportedVersions": "2026-07-28"}},
])
def test_modern_incompatible_or_incomplete_discovery_never_downgrades(monkeypatch, reply):
    def handler(rid, method, params, wait):
        assert method == "server/discover"
        return reply

    calls, sends = _protocol_fixture(monkeypatch, handler)
    result = obs.talk(None, obs.time.monotonic())
    assert result["status"] == "protocol" and "tools" not in result
    assert len(calls) == 1 and not sends


@pytest.mark.parametrize("repeated_error", [False, True])
def test_modern_advertised_version_retry_is_bounded_and_never_legacy(monkeypatch, repeated_error):
    attempts = []

    def handler(rid, method, params, wait):
        attempts.append(method)
        if method == "server/discover":
            if len(attempts) == 1 or repeated_error:
                return {"error": {"code": -32022, "data": {"supported": [obs.MODERN_PROTOCOL]}}}
            return _discover_result()
        assert method == "tools/list"
        return {"result": {"resultType": "complete", "tools": []}}

    calls, sends = _protocol_fixture(monkeypatch, handler)
    result = obs.talk(None, obs.time.monotonic())
    assert result["status"] == ("protocol" if repeated_error else "ok")
    assert attempts.count("server/discover") == 2 and "initialize" not in attempts and not sends
    assert len({call[0] for call in calls}) == len(calls)


@pytest.mark.parametrize("stage", ["tools/list", "tools/call"])
def test_input_required_never_completes_listing_or_canary_coverage(monkeypatch, stage):
    tool = {"name": "fixture", "inputSchema": {"type": "object"}}

    def handler(rid, method, params, wait):
        if method == "server/discover":
            return _discover_result()
        if method == stage:
            return {"result": {"resultType": "input_required", "content": [], "tools": [tool]}}
        assert method == "tools/list"
        return {"result": {"resultType": "complete", "tools": [tool]}}

    calls, sends = _protocol_fixture(monkeypatch, handler)
    marks = {}
    result = obs.talk(None, obs.time.monotonic(), "fixture", True, marks)
    if stage == "tools/list":
        assert result["status"] == "protocol" and "tools" not in result
        assert "input_required" in result["detail"] and "coverage" not in marks
    else:
        assert result["status"] == "ok" and result["tools"] == [tool]
        assert marks["coverage"]["successful"] == 0 and marks["coverage"]["errors"] == 1
        assert marks["callsComplete"] is False
    assert not sends


def test_modern_session_never_replies_to_server_requests_or_provides_roots():
    import json

    session = obs.Session.__new__(obs.Session)
    session.modern = True
    sent = []
    session.send = sent.append
    session._lines = lambda until: iter([json.dumps({"jsonrpc": "2.0", "id": "server", "method": "roots/list"}).encode()])
    with pytest.raises(obs.ProtocolFailure, match="unexpected modern server request"):
        session.call(1, "server/discover", obs.modern_params(), 1)
    assert len(sent) == 1 and sent[0]["method"] == "server/discover"


@pytest.mark.parametrize("request_id", [None, "missing"])
def test_stdio_recognizes_modern_protocol_errors_without_a_request_id(request_id):
    import json

    session = obs.Session.__new__(obs.Session)
    session.modern = True
    session.send = lambda msg: None
    error = {"jsonrpc": "2.0", "error": {"code": -32022, "data": {"supported": ["2099-01-01"]}}}
    if request_id != "missing":
        error["id"] = request_id
    session._lines = lambda until: iter([json.dumps(error).encode()])
    assert session.call(0, "server/discover", obs.modern_params(), 1) == error


@pytest.mark.parametrize("reply", [
    {"jsonrpc": "2.0", "id": 0, "error": {"code": "-32022"}},
    {"jsonrpc": "2.0", "id": 0, "error": {"code": -32022.0}},
    {"jsonrpc": "2.0", "id": 0, "error": None},
    {"jsonrpc": "2.0", "id": 0, "error": {"code": -32601}, "result": {}},
    {"id": 0, "error": {"code": -32601}},
    {"jsonrpc": "2.0", "id": 999, "error": {"code": -32021}},
    {"jsonrpc": "2.0", "id": False, "error": {"code": -32021}},
])
def test_malformed_or_wrong_id_modern_discovery_never_reaches_initialize(reply):
    import json

    session = obs.Session.__new__(obs.Session)
    session.modern = True
    sent = []
    session.send = sent.append
    session._lines = lambda until: iter([json.dumps(reply).encode()])
    with pytest.raises(obs.ProtocolFailure, match="no downgrade"):
        obs.negotiate(session)
    assert [msg["method"] for msg in sent] == ["server/discover"]


@pytest.mark.parametrize("code", [-32600, -32601, -32602, -32000, -32700])
def test_valid_legacy_errors_without_id_still_initialize_in_same_session(code):
    import json

    session = obs.Session.__new__(obs.Session)
    session.modern = True
    sent = []
    session.send = sent.append

    def lines(until):
        if sent[-1]["method"] == "server/discover":
            return iter([json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": code}}).encode()])
        return iter([json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": obs.PROTOCOL}}).encode()])

    session._lines = lines
    _, version, modern = obs.negotiate(session)
    assert version == obs.PROTOCOL and modern is False
    assert [msg["method"] for msg in sent] == ["server/discover", "initialize"]


@pytest.mark.parametrize("cursor", [False, 0, [], {}, "", 1, ["next"]])
def test_invalid_observer_cursor_never_marks_listing_complete(monkeypatch, cursor):
    def handler(rid, method, params, wait):
        if method == "server/discover":
            return _discover_result()
        return {"result": {"resultType": "complete", "tools": [], "nextCursor": cursor}}

    calls, sends = _protocol_fixture(monkeypatch, handler)
    result = obs.talk(None, obs.time.monotonic())
    assert result["status"] == "protocol" and "tools" not in result
    assert "invalid pagination cursor" in result["detail"]
    assert [call[1] for call in calls] == ["server/discover", "tools/list"] and not sends


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


@pytest.mark.parametrize("record,expected", [
    ("store_mcp/__init__.py,a,1\nstore_mcp/server.py,b,2\nstore_mcp/main.py,c,3\n", "store_mcp.server"),
    ("store_mcp.py,a,1\nstore_mcp/server.py,b,2\n", "store_mcp"),
    ("store_mcp/__init__.py,a,1\nstore_mcp/main.py,c,3\n", "store_mcp.main"),
    ("other/server.py,a,1\ntests/__main__.py,b,2\nstore_mcp/__init__.py,c,3\n", None),
])
def test_a_package_without_a_command_or_main_is_tried_by_its_own_module(tmp_path, record, expected):
    info = tmp_path / "pkg/store_mcp-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "RECORD").write_text(record)
    found = obs.pypi_entry(tmp_path, "store-mcp")
    assert (found[1] if found else None) == (f"python -m {expected}" if expected else None)


def test_a_relative_open_is_resolved_from_the_directory_gvisor_prints(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "TRACE_ROOT", tmp_path)
    (tmp_path / "c").mkdir()
    log = tmp_path / "c/runsc.log.fixture.boot.txt"
    prefix = "I1010 12:00:00.000000 1 strace.go:607] [ 7: 7] python X "
    log.write_text("\n".join(prefix + c for c in [
        "openat(AT_FDCWD /work/app, 0x7ec666061870 ., O_RDONLY|O_CLOEXEC|O_DIRECTORY|O_NONBLOCK, 0o0) = 3 (0x3) (23µs)",
        "openat(0x3 /home/histor/.aws, 0x7ec666061870 credentials, O_RDONLY|O_CLOEXEC, 0o0) = 4 (0x4) (9µs)",
        "openat(AT_FDCWD /work/app, 0x7ec666061870 .env, O_RDONLY|O_CLOEXEC, 0o0) = 5 (0x5) (9µs)",
    ]))
    r = obs.read_trace("c", [("startup", 0, 9999999999)], "python", "1")["startup"]
    assert r["complete"] and r["decoys"] == [".aws/credentials", ".env (working directory)"]
    log.write_text(prefix + "openat(0x5, 0x7ec666061870 notes.txt, O_RDONLY, 0o0) = 6 (0x6) (9µs)")
    r = obs.read_trace("c", [("startup", 0, 9999999999)], "python", "1")["startup"]
    assert not r["complete"] and r["incompleteReasons"] == ["relative-path-unresolved"], "no printed directory: never clean"


def _trace(tmp_path, monkeypatch, lines, pid="7"):
    monkeypatch.setattr(obs, "TRACE_ROOT", tmp_path)
    (tmp_path / "c").mkdir(exist_ok=True)
    prefix = f"I1010 12:00:00.000000 1 strace.go:607] [ {pid}: {pid}] python X "
    (tmp_path / "c/runsc.log.fixture.boot.txt").write_text("\n".join(prefix + line for line in lines))
    return obs.read_trace("c", [("startup", 0, 9999999999)], "python", "1")["startup"]


def test_asyncio_self_pipe_is_not_an_unresolved_destination(tmp_path, monkeypatch):
    pair = "socketpair(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0x0, 0x7eab27a075d0) = 0 (0x0) (94µs)"
    send = "sendto(0x5 socket:[2], 0x7ecb594aa650, 0x1, 0x0, null, 0x0) = 1 (0x1) (63µs)"
    assert _trace(tmp_path, monkeypatch, [pair, send])["complete"]
    r = _trace(tmp_path, monkeypatch, [send])
    assert not r["complete"] and "socket-destination-unresolved" in r["incompleteReasons"], "no socketpair seen: never clean"
    made = "socket(AF_INET, SOCK_STREAM|SOCK_CLOEXEC, IPPROTO_IP) = 5 (0x5) (9µs)"
    r = _trace(tmp_path, monkeypatch, [pair, made, send])
    assert not r["complete"], "a socket() socket with no traced peer stays unresolved, socketpair or not"
    connected = "connect(0x6 socket:[3], 0xabc {Family: AF_INET, Addr: 203.0.113.9, Port: 443}, 0x10) = 0 (0x0) (9µs)"
    later = "sendto(0x6 socket:[3], 0xabc, 0x10, 0x0, null, 0x0) = 16 (0x10) (9µs)"
    assert _trace(tmp_path, monkeypatch, [pair, connected, later])["network"] == ["203.0.113.9:443"]


def test_a_socketpair_end_that_reuses_a_closed_sockets_number_is_still_local(tmp_path, monkeypatch):
    """close() is not traced: fd 5 was a TCP socket to the trap, then one end of asyncio's pair."""
    tcp = "socket(AF_INET, SOCK_STREAM|SOCK_CLOEXEC, IPPROTO_TCP) = 5 (0x5) (9µs)"
    trap = "connect(0x5 socket:[16], 0xabc {Family: AF_INET, Addr: 10.231.0.1, Port: 443}, 0x10) = -1 errno=115 (operation now in progress) (9µs)"
    pair = "socketpair(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0x0, 0x7eab27a075d0) = 0 (0x0) (94µs)"
    wake = "sendto(0x5 socket:[51], 0x7eec5742bfd0, 0x1, 0x0, null, 0x0) = 1 (0x1) (15µs)"
    r = _trace(tmp_path, monkeypatch, [tcp, trap, pair, wake])
    assert r["complete"] and r["network"] == ["10.231.0.1:443"]
    # A docker.sock that was not there, then the same number for the pair.
    unix = "socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0x0) = 5 (0x5) (9µs)"
    dock = 'connect(0x5 socket:[7], 0xabc {Family: AF_UNIX, Addr: "/run/docker.sock"}, 0x6e) = -1 errno=2 (no such file or directory) (9µs)'
    r = _trace(tmp_path, monkeypatch, [unix, dock, pair, wake])
    assert r["complete"] and r["network"] == ["unix:/run/docker.sock"]
    # A socket() nothing used before the pair was made may have handed its number to the pair.
    assert _trace(tmp_path, monkeypatch, [tcp, pair, wake])["complete"]
    # Made after the pair, never connected: still unresolved, as before.
    r = _trace(tmp_path, monkeypatch, [pair, tcp, wake])
    assert not r["complete"] and "socket-destination-unresolved" in r["incompleteReasons"]


def test_a_dns_query_the_logger_never_saw_is_not_clean(tmp_path, monkeypatch):
    class Logger:
        def __init__(self, names):
            self._names = names

        def names(self, src, since, until):
            return self._names

        def name_for(self, ip):
            return None

    query = "sendmmsg(0x3 socket:[3], 0xabc, 0x2, 0x4000) = 2 (0x2) (9µs)"
    connect = "connect(0x3 socket:[3], 0xabc {Family: AF_INET, Addr: 10.231.0.1, Port: 53}, 0x10) = 0 (0x0) (9µs)"
    monkeypatch.setattr(obs, "DNS", Logger([]))
    r = _trace(tmp_path, monkeypatch, [connect, query])
    assert not r["complete"] and r["incompleteReasons"] == ["dns-unrecorded"]
    monkeypatch.setattr(obs, "DNS", Logger(["mail.momus-trap.invalid"]))
    r = _trace(tmp_path, monkeypatch, [connect, query])
    assert r["complete"] and r["lookups"] == ["mail.momus-trap.invalid"]


def test_an_unresolved_relative_write_is_not_persistence(tmp_path, monkeypatch):
    r = _trace(tmp_path, monkeypatch, ["openat(0x9, 0x7e buildcheck.gypi, O_WRONLY|O_CREAT|O_TRUNC, 0o644) = 3 (0x3) (9µs)"])
    assert not r.get("writes") and r["incompleteReasons"] == ["relative-path-unresolved"]
    r = _trace(tmp_path, monkeypatch, ["openat(AT_FDCWD /home/histor, 0x7e .bashrc, O_WRONLY|O_APPEND, 0o644) = 3 (0x3) (9µs)"])
    assert r["writes"] == ["/home/histor/.bashrc"]


def test_a_containers_start_bounds_what_its_address_is_credited_with(monkeypatch):
    monkeypatch.setattr(obs, "docker", lambda *a, **k: SimpleNamespace(returncode=0, stdout="2026-10-13T11:00:00.250000123Z\n", stderr=""))
    assert abs(obs.container_started("c") - (1791889200 + 0.25)) < 1e-3
    section = {"complete": True, "incompleteReasons": []}
    assert obs._attribution(None, 5.0, [section]) == 5.0 and not section["complete"]
    assert section["incompleteReasons"] == ["start-time-unknown"]
    assert obs._attribution(10.0, 5.0, []) == 10.0


def test_the_start_is_read_while_the_container_exists():
    src = (Path(__file__).parents[1] / "sandbox/histor_observe.py").read_text(encoding="utf-8")
    for stage in ('def run_scripts', 'def run('):
        body = src[src.index(stage):]
        body = body[:body.index("\ndef ", 10)]
        assert body.index("container_started(cid)") < body.index('docker("rm", "-f"'), stage


def test_a_send_on_an_accepted_connection_goes_to_the_peer_accept_printed(tmp_path, monkeypatch):
    """The format runsc release-20261005.0 prints, from a loopback server on hunt."""
    listen = "socket(AF_INET, SOCK_STREAM|SOCK_CLOEXEC, IPPROTO_IP) = 3 (0x3) (29µs)"
    loop = "accept4(0x3 socket:[1], 0x7ea884cc6510 {Family: AF_INET, Addr: 127.0.0.1, Port: 41192}, 0x7ea884cc64ec {length=16}, SOCK_CLOEXEC) = 5 (0x5) (17ms)"
    reply = "sendto(0x5 socket:[3], 0xc664d0, 0x315, 0x0, null, 0x0) = 789 (0x315) (182µs)"
    r = _trace(tmp_path, monkeypatch, [listen, loop, reply])
    assert r["complete"] and "network" not in r, "a loopback client is inside the container"
    outside = loop.replace("127.0.0.1, Port: 41192", "203.0.113.7, Port: 41192")
    assert _trace(tmp_path, monkeypatch, [listen, outside, reply])["network"] == ["203.0.113.7:41192"]
    # accept(fd, NULL, NULL) prints no peer: somebody connected, so it is never a pair's end.
    blind = "accept4(0x3 socket:[1], 0x0, 0x0, SOCK_CLOEXEC) = 5 (0x5) (17ms)"
    pair = "socketpair(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0x0, 0x7eab27a075d0) = 0 (0x0) (94µs)"
    r = _trace(tmp_path, monkeypatch, [pair, listen, blind, reply])
    assert not r["complete"] and "socket-destination-unresolved" in r["incompleteReasons"]



def test_the_traps_reasons_join_the_traces_and_a_clean_run_stays_clean(monkeypatch):
    """A run the observer could not fully account for keeps every reason, the DNS one included; a run
    with none is clean. The two never collapse into one 'answered' count."""
    class Foreign:
        def phase(self, ip, since, until, tag):
            return {"complete": False, "incompleteReasons": ["foreign-run-traffic"], "foreignRequests": 1, "requests": []}

    class Clean:
        def phase(self, ip, since, until, tag):
            return {"complete": True, "incompleteReasons": [], "foreignRequests": 0, "requests": []}

    monkeypatch.setattr(obs, "SINK", Foreign())
    section = {"complete": False, "incompleteReasons": ["dns-unrecorded"]}
    obs.merge_sink(section, "10.231.0.7", 0.0, 1.0, "bbbbbbbbbbbb")
    assert not section["complete"] and section["incompleteReasons"] == ["dns-unrecorded", "foreign-run-traffic"]
    monkeypatch.setattr(obs, "SINK", Clean())
    section = {"complete": True, "incompleteReasons": []}
    obs.merge_sink(section, "10.231.0.7", 0.0, 1.0, "bbbbbbbbbbbb")
    assert section["complete"] and section["incompleteReasons"] == []
    monkeypatch.setattr(obs, "SINK", None)
    section = {"complete": True, "incompleteReasons": []}
    obs.merge_sink(section, "10.231.0.7", 0.0, 1.0, "bbbbbbbbbbbb")
    assert not section["complete"] and section["incompleteReasons"] == ["sink-unavailable"]


# -- GCVE-110-PYPI-2026-022091: renames, links, symlinks, creat and writes under /work -----------
# The call shapes below are runsc's own strace output, captured on the sandbox host on
# 2026-10-25 (paths changed to the sandbox's).

def test_a_rename_onto_bashrc_is_a_persistent_write(tmp_path, monkeypatch):
    r = _trace(tmp_path, monkeypatch, [
        "rename(0x7ed079d67ec0 /home/histor/x, 0x7ed079d8d310 /home/histor/.bashrc) = 0 (0x0) (54µs)"])
    assert r["writes"] == ["/home/histor/.bashrc (rename)"] and r["complete"]


def test_renameat_reuses_the_directory_printed_once(tmp_path, monkeypatch):
    r = _trace(tmp_path, monkeypatch, [
        "renameat(0x3 /home/histor, 0x7ed079d67ec0 x, 0x3, 0xb3a380 .bashrc) = 0 (0x0) (85µs)"])
    assert r["writes"] == ["/home/histor/.bashrc (renameat)"] and r["complete"]


def test_renameat2_link_symlink_and_creat_are_judged_like_writes(tmp_path, monkeypatch):
    r = _trace(tmp_path, monkeypatch, [
        "renameat2(AT_FDCWD /tmp, 0xb3a380 c, 0xffffffffffffff9c, 0x7ed0 /home/histor/.profile, 0x0) = 0 (0x0) (75µs)",
        "linkat(0x3 /home/histor, 0xb3a380 c, 0x3, 0x7ed0 .zshrc, 0x400) = 0 (0x0) (19µs)",
        "symlinkat(0xb3a380 /tmp/c, 0x3 /home/histor/.config/autostart, 0x7ed0 run.desktop) = 0 (0x0) (21µs)",
        "creat(0x7ed079d9a850 /work/pkg/evil.pth, 0o644) = 4 (0x4) (132µs)",
        "creat(0x7ed079d9a850 /tmp/scratch.pth, 0o644) = 4 (0x4) (132µs)",
    ])
    assert r["writes"] == ["/home/histor/.config/autostart/run.desktop (symlinkat)",
                           "/home/histor/.profile (renameat2)", "/home/histor/.zshrc (linkat)",
                           "/work/pkg/evil.pth"]
    assert r["complete"]


def test_a_relative_move_is_never_clean_and_a_persistent_name_is_still_reported(tmp_path, monkeypatch):
    r = _trace(tmp_path, monkeypatch, [
        "rename(0xb3a320 a, 0xb3a350 .bashrc) = 0 (0x0) (115µs)",
        "link(0xb3a380 c, 0x7ed079d67ec0 hard) = 0 (0x0) (24µs)",
    ])
    assert not r["complete"] and r["incompleteReasons"] == ["relative-path-unresolved"]
    assert r["writes"] == [".bashrc (rename, directory unknown)"]


def test_a_failed_rename_moved_nothing(tmp_path, monkeypatch):
    r = _trace(tmp_path, monkeypatch, [
        "rename(0x7e /home/histor/x, 0x7e /home/histor/.bashrc) = -1 ENOENT (no such file) (5µs)"])
    assert "writes" not in r and r["complete"]


def test_code_written_into_the_installed_package_after_install_is_reported(tmp_path, monkeypatch):
    line = "openat(AT_FDCWD /work/pkg, 0x7e mod/patched.py, O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC, 0o644) = 5 (0x5) (40µs)"
    assert _trace(tmp_path, monkeypatch, [line])["writes"] == ["/work/pkg/mod/patched.py"]
    data = "openat(AT_FDCWD /work/pkg, 0x7e mod/cache.json, O_WRONLY|O_CREAT, 0o644) = 5 (0x5) (40µs)"
    assert "writes" not in _trace(tmp_path, monkeypatch, [data])


def test_a_trace_runtime_that_does_not_record_file_moves_is_not_a_trace_runtime():
    import json

    old = ("socket,connect,openat,open,execve,execveat,sendto,sendmsg,sendmmsg,bind,unlinkat,renameat,"
           "mkdirat,socketpair,accept,accept4")
    runtimes = {obs.TRACE_RUNTIME: {"runtimeArgs": ["--strace", f"--strace-syscalls={old}"]}}
    gaps = obs.trace_syscall_gaps(json.dumps(runtimes))
    assert set(gaps) == {"creat", "rename", "renameat2", "link", "linkat", "symlink", "symlinkat"}
    installed = (Path(obs.__file__).parent / "install.sh").read_text()
    listed = installed.split("--strace-syscalls=", 1)[1].split('"', 1)[0].split(",")
    assert obs.trace_syscall_gaps(json.dumps(
        {obs.TRACE_RUNTIME: {"runtimeArgs": ["--strace", f"--strace-syscalls={','.join(listed)}"]}})) == []
