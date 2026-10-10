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
