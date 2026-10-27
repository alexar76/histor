"""Real local transports connect modern observation, capture and the personal report."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from histor import __main__ as cli
from histor.toolcost_cli import _Stdio


def test_modern_http_capture_to_personal_report(tmp_path):
    calls = []
    state = {"version": 1}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body["method"])
            assert self.headers["Mcp-Method"] == body["method"]
            assert self.headers["MCP-Protocol-Version"] == "2026-07-28"
            assert body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {}
            result = {"resultType": "complete", "ttlMs": 0, "cacheScope": "public", "_meta": {
                "io.modelcontextprotocol/serverInfo": {"name": "local-fixture", "version": str(state["version"])}}}
            if body["method"] == "server/discover":
                result.update(supportedVersions=["2026-07-28"], capabilities={"tools": {}})
            else:
                assert body["method"] == "tools/list"
                required = ["query"] if state["version"] == 1 else ["query", "workspace"]
                result["tools"] = [{"name": "search", "inputSchema": {"type": "object", "properties": {
                    "query": {"type": "string"}, "workspace": {"type": "string"}}, "required": required}}]
            data = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name, version in (("before", 1), ("after", 2)):
            state["version"] = version
            assert cli.main(["upgrade-brief", "capture", "--url", f"http://127.0.0.1:{server.server_port}/mcp",
                "--target-id", "local-fixture", "--allow-private", "--out", str(tmp_path / f"{name}.json")]) == 0
        manifest = {"format": "histor.upgrade-watchlist/v1", "dependencies": [{"id": "local-fixture",
            "before": "before.json", "after": "after.json", "tools": ["search"], "workflows": [{
                "id": "weekly", "description": "Еженедельный обзор", "tools": ["search"]}]}]}
        watchlist = tmp_path / "watchlist.json"
        watchlist.write_text(json.dumps(manifest))
        assert cli.main(["upgrade-brief", "--watchlist", str(watchlist), "--out-dir", str(tmp_path / "report")]) == 0
        report = json.loads((tmp_path / "report/report.json").read_text())
        dependency = report["dependencies"][0]
        assert dependency["status"] == "changed" and dependency["comparability"] == "comparable"
        assert dependency["selectedTools"][0]["requiredAdded"] == ["workspace"]
        assert dependency["workflows"][0]["status"] == "retest"
        assert calls == ["server/discover", "tools/list"] * 2
        assert "Еженедельный обзор" in (tmp_path / "report/report.md").read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_toolcost_client_and_histor_server_interoperate_modern_without_initialize(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("HISTOR_", "AIMARKET_", "AIFACTORY_")) or name == "DATABASE_URL":
            monkeypatch.delenv(name)
    monkeypatch.setenv("HISTOR_DATA_DIR", str(tmp_path / "local-data"))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    client = _Stdio([sys.executable, "-m", "histor", "mcp"], time.monotonic() + 20)
    try:
        tools = client.tools()
        assert client.modern and client.protocol_version == "2026-07-28"
        assert {tool["name"] for tool in tools} == {"histor_check", "histor_changes", "histor_server"}
    finally:
        client.close()
