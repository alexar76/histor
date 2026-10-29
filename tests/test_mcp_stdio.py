"""Exercise the actual CLI over live pipes, with no HTTP server or operator data."""

import asyncio
import io
import json
import os
import select
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from histor import stdio

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "histor"


@contextmanager
def session(tmp_path, *, legacy=True, **extra_env):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AIMARKET_", "AIFACTORY_", "HISTOR_"))
           and k not in ("DATABASE_URL", "PYTHONPATH")}
    env.update(PYTHONPATH=str(ROOT), AIMARKET_AUTO_CRAWL="0", AIMARKET_PQC="0",
               AIMARKET_SKIP_SEED="1", AIMARKET_UPDATE_CHECK="0",
               HISTOR_DATA_DIR=str(tmp_path / "data"), **extra_env)
    with (tmp_path / "stderr.log").open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", PACKAGE, "mcp"] + (["--init"] if PACKAGE == "aimarket_hub" else []),
            cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0,
        )
        def send(message):
            proc.stdin.write((json.dumps(message) + "\n").encode())
            proc.stdin.flush()

        def read():
            ready, _, _ = select.select([proc.stdout], [], [], 20)
            assert ready, "MCP did not answer while stdin stayed open"
            raw = proc.stdout.readline()
            assert raw, (tmp_path / "stderr.log").read_text()
            return json.loads(raw)

        def call(req_id, method, **params):
            send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            reply = read()
            assert reply["id"] == req_id
            return reply

        try:
            if legacy:
                initialized = call(0, "initialize", protocolVersion="2024-11-05", capabilities={},
                                   clientInfo={"name": "stdio-test", "version": "1"})
                assert initialized["result"]["protocolVersion"] == "2024-11-05"
                assert "tools" in initialized["result"]["capabilities"]
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            yield call, send, read, proc
            proc.stdin.close()
            assert proc.wait(timeout=15) == 0, (tmp_path / "stderr.log").read_text()
            assert proc.stdout.read() == b"", "Extra stdout after EOF (or notification reply)"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
            if not proc.stdin.closed:
                proc.stdin.close()


def test_framing_errors_notifications_and_oversize_recovery(monkeypatch):
    monkeypatch.setattr(stdio, "MAX_MESSAGE_BYTES", 256)
    messages = [
        b"{broken", b"[]", b'{"jsonrpc":"1.0","id":1,"method":"ping"}',
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
        b'{"jsonrpc":"2.0","id":1,"result":{}}',
        b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":2,"method":"ping","params":[]}',
        b"x" * 600,
        b'{"jsonrpc":"2.0","id":"last","method":"ping"}',
    ]
    async def dispatch(msg):
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
    output = io.StringIO()
    asyncio.run(stdio.serve(dispatch, output=output, source=io.BytesIO(b"\n".join(messages) + b"\n")))
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["error"]["code"] for r in replies[:-1]] == [-32700, -32600, -32600, -32600, -32602, -32600]
    assert replies[-1] == {"jsonrpc": "2.0", "id": "last", "result": {}}


def test_cli_protocol_and_tools(tmp_path):
    with session(tmp_path) as (call, send, read, proc):
        tools = call(1, "tools/list")["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert call(2, "ping")["result"] == {}
        assert call(3, "nonexistent")["error"]["code"] == -32601
        assert call(4, "tools/call", name="nonexistent")["error"]["code"] == -32602
        proc.stdin.write(b"not json\n")
        proc.stdin.flush()
        assert read()["error"]["code"] == -32700
        assert names == {"histor_changes", "histor_check", "histor_server"}
        changes = call(5, "tools/call", name="histor_changes", arguments={"limit": 5})["result"]
        assert not changes["isError"]
        payload = json.loads(changes["content"][0]["text"])
        assert payload["result"]["changes"] == []
        assert payload["signature"] and payload["receipt"]["signature"]
        bad = call(6, "tools/call", name="histor_changes", arguments={"limit": True})["result"]
        assert bad["isError"]
        missing = call(7, "tools/call", name="histor_server", arguments={"name": "missing"})["result"]
        assert missing["isError"]
        check = call(8, "tools/call", name="histor_check", arguments={
            "endpoint": "https://example.com/mcp", "toolSetDigest": "sha256-" + "A" * 43 + "=",
        })["result"]
        assert not check["isError"], check
        assert json.loads(check["content"][0]["text"])["result"]["match"] == "not-listed"


def test_modern_stdio_discovery_and_calls_are_self_contained(tmp_path):
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}}
    with session(tmp_path, legacy=False) as (call, _send, _read, _proc):
        discovered = call(1, "server/discover", _meta=meta)["result"]
        assert discovered["resultType"] == "complete"
        assert "2026-07-28" in discovered["supportedVersions"]
        assert "2024-11-05" in discovered["supportedVersions"]
        assert discovered["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "histor"
        tools = call(2, "tools/list", _meta=meta)["result"]
        assert tools["resultType"] == "complete" and tools["ttlMs"] == 0
        assert tools["cacheScope"] == "private"
        assert [t["name"] for t in tools["tools"]] == sorted(t["name"] for t in tools["tools"])
        result = call(3, "tools/call", _meta=meta, name="histor_changes", arguments={"limit": 5})["result"]
        assert result["resultType"] == "complete" and not result["isError"]
        assert json.loads(result["content"][0]["text"])["result"]["changes"] == []
        # Omitting all modern metadata cannot silently change the connection's era.
        assert call(4, "ping")["error"]["code"] == -32602
        assert call(5, "ping", _meta=meta)["error"]["code"] == -32601
        assert call(6, "initialize", protocolVersion="2025-06-18")["result"]["protocolVersion"] == "2025-06-18"
        assert call(7, "ping")["result"] == {}


def test_modern_metadata_is_validated_on_every_request(tmp_path):
    version = "io.modelcontextprotocol/protocolVersion"
    caps = "io.modelcontextprotocol/clientCapabilities"
    with session(tmp_path, legacy=False) as (call, _send, _read, _proc):
        assert call(1, "server/discover")["error"]["code"] == -32602
        assert call(2, "tools/list", _meta={version: "2026-07-28"})["error"]["code"] == -32602
        assert call(3, "tools/list", _meta={version: "2026-07-28", caps: []})["error"]["code"] == -32602
        mismatch = call(4, "tools/list", _meta={version: "2099-01-01", caps: {}})["error"]
        assert mismatch["code"] == -32022 and "2026-07-28" in mismatch["data"]["supported"]
        # A valid request works after malformed/unsupported ones on the same process.
        assert call(5, "tools/list", _meta={version: "2026-07-28", caps: {}})["result"]["tools"]



def _spawn_mcp(tmp_path, *cli_args, env_extra=None):
    """Start `python -m histor mcp <args>` over pipes, with no local data and no network expectation."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AIMARKET_", "AIFACTORY_", "HISTOR_")) and k not in ("DATABASE_URL", "PYTHONPATH")}
    env.update(PYTHONPATH=str(ROOT), AIMARKET_AUTO_CRAWL="0", AIMARKET_PQC="0", AIMARKET_SKIP_SEED="1",
               AIMARKET_UPDATE_CHECK="0", HISTOR_DATA_DIR=str(tmp_path / "data"), **(env_extra or {}))
    return subprocess.Popen([sys.executable, "-m", PACKAGE, "mcp", *cli_args], cwd=tmp_path, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)


def test_remote_mode_names_the_public_log_and_fails_closed_without_network(tmp_path):
    """`--remote URL` talks to a public instance instead of the local store: the instructions say so, the
    tool list is the same, and an unreachable instance is an isError result, not a crash. Port 1 on
    loopback refuses at once, so the test needs no network and no local log."""
    proc = _spawn_mcp(tmp_path, "--remote", "http://127.0.0.1:1")
    messages = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "histor_changes", "arguments": {"limit": 1}}},
    ]
    out, err = proc.communicate("".join(json.dumps(m) + "\n" for m in messages).encode(), timeout=60)
    assert proc.returncode == 0, err.decode()
    replies = {json.loads(line)["id"]: json.loads(line)["result"] for line in out.decode().splitlines() if line.strip()}
    assert "http://127.0.0.1:1" in replies[0]["instructions"]
    assert "fresh log" not in replies[0]["instructions"]  # that caveat belongs to the local mode only
    assert sorted(t["name"] for t in replies[1]["tools"]) == ["histor_changes", "histor_check", "histor_server"]
    assert replies[2]["isError"] is True
    assert "did not answer" in replies[2]["content"][0]["text"]
    assert not (tmp_path / "data").exists(), "remote mode must not create a local log"


def test_mcp_rejects_unknown_arguments(tmp_path):
    proc = _spawn_mcp(tmp_path, "--nope")
    out, err = proc.communicate(b"", timeout=30)
    assert proc.returncode == 2
    assert out == b""
    assert b"usage: histor mcp" in err


def test_parse_args_defaults():
    from histor.mcp_stdio import DEFAULT_REMOTE, parse_args
    assert parse_args([], default_remote=False) is None
    assert parse_args([], default_remote=True) == DEFAULT_REMOTE          # `aimarket-histor`
    assert parse_args(["--local"], default_remote=True) is None
    assert parse_args(["--remote"], default_remote=False) == DEFAULT_REMOTE
    assert parse_args(["--remote", "https://log.example"], default_remote=False) == "https://log.example"
    assert parse_args(["--remote=https://log.example/"], default_remote=False) == "https://log.example/"
    assert parse_args(["--remote", "ftp://x"], default_remote=False) is False
    assert parse_args(["--bogus"], default_remote=True) is False
