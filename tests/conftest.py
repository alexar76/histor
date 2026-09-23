"""Shared fixtures.

Every storage test runs twice when ``HISTOR_TEST_DATABASE_URL`` is set — once on SQLite, once on
Postgres — because production is Postgres and the SQL is written once for both. Without the
variable the Postgres half skips, loudly, rather than pretending to pass.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx
import pytest

from histor.config import load_settings
from histor.registry import Target, target_id
from histor.subject import REGISTRY_OFFICIAL

ROOT = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("HISTOR_TEST_DATABASE_URL", "").strip()
SCANNER_READY = (ROOT / "scanner" / "node_modules" / "@aimarket" / "warden").is_dir() and shutil.which("node")


def _reset_postgres(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(params=["sqlite", "postgresql"])
def backend_kind(request) -> str:
    if request.param == "postgresql" and not PG_URL:
        pytest.skip("HISTOR_TEST_DATABASE_URL not set — Postgres half not exercised")
    return request.param


@pytest.fixture
def settings(tmp_path, monkeypatch, backend_kind):
    monkeypatch.setenv("HISTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HISTOR_OPERATOR_TOKEN", "t" * 32)
    monkeypatch.setenv("HISTOR_CRAWL_INTERVAL_S", "0")
    monkeypatch.setenv("HISTOR_CRAWL_WORKERS", "4")
    monkeypatch.setenv("HISTOR_ALLOW_PRIVATE_TARGETS", "1")
    monkeypatch.delenv("HISTOR_DATABASE_URL", raising=False)
    if backend_kind == "postgresql":
        _reset_postgres(PG_URL)
        monkeypatch.setenv("HISTOR_DATABASE_URL", PG_URL)
    return load_settings()


def tool(name: str, description: str = "", **schema_props: Any) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": {k: {"type": v} for k, v in schema_props.items()}},
    }


class FakeMcp:
    """In-process MCP servers keyed by URL path, served through ``httpx.MockTransport``.

    ``tools[path]`` is what tools/list returns (paginated by ``page_size`` when set);
    ``status[path]`` forces an HTTP status; ``sse`` paths answer as event streams.
    """

    def __init__(self) -> None:
        self.tools: dict[str, list[dict[str, Any]]] = {}
        self.status: dict[str, int] = {}
        self.sse: set[str] = set()
        self.page_size: dict[str, int] = {}
        self.raw: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        method = body.get("method", "")
        self.calls.append((path, method))
        if path in self.status:
            return httpx.Response(self.status[path])
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": f"fake{path}", "version": "1.0"}}
            return self._reply(path, {"jsonrpc": "2.0", "id": body["id"], "result": result},
                               headers={"Mcp-Session-Id": "s1"})
        if method == "tools/list":
            if path in self.raw:
                return httpx.Response(200, text=self.raw[path], headers={"content-type": "application/json"})
            tools = self.tools.get(path, [])
            size = self.page_size.get(path)
            if size:
                start = int((body.get("params") or {}).get("cursor") or 0)
                page = tools[start:start + size]
                result: dict[str, Any] = {"tools": page}
                if start + size < len(tools):
                    result["nextCursor"] = str(start + size)
            else:
                result = {"tools": tools}
            return self._reply(path, {"jsonrpc": "2.0", "id": body["id"], "result": result})
        return httpx.Response(400)

    def _reply(self, path: str, message: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
        if path in self.sse:
            text = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}) + \
                   "\n\n" + "event: message\ndata: " + json.dumps(message) + "\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream", **(headers or {})})
        return httpx.Response(200, json=message, headers=headers)


def make_target(name: str, path: str, *, skip: str | None = None) -> Target:
    url = f"https://127.0.0.1{path}"
    return Target(id=target_id(name, url), name=name, endpoint=url, transport="streamable-http",
                  registry=REGISTRY_OFFICIAL, title=name.split("/")[-1], description=f"{name} description",
                  version="1.0.0", repository=None, website=None, skip_reason=skip)


class FakeScanner:
    """Deterministic stand-in for the node sidecar, for crawler tests that must not need node."""

    PATTERN_SET = {"id": "urn:awr:mtl:1:patternset:test", "rules": [{"code": "TOOL_DEF_INJECTION", "tier": "block"}]}
    RECORD_SET = {"id": "urn:awr:mtl:1:recordset:test", "records": [{"pattern": "*drain*wallet*"}]}

    def __init__(self) -> None:
        from awr import canonical_sri

        from histor.scanner import RuleSets

        self._rulesets = RuleSets("@aimarket/warden@test", self.PATTERN_SET, canonical_sri(self.PATTERN_SET),
                                  self.RECORD_SET, canonical_sri(self.RECORD_SET))
        self.jobs: list[dict[str, Any]] = []

    def rulesets(self):
        return self._rulesets

    def scan(self, jobs, timeout=None):
        self.jobs.extend(jobs)
        out = {}
        for job in jobs:
            matches = []
            for t in job["tools"]:
                if "ignore all previous" in t["description"].lower():
                    matches.append({"code": "TOOL_DEF_INJECTION", "severity": "critical", "tier": "block",
                                    "tool": t["name"], "where": "description", "span": "ignore all previous"})
                if "api_key" in json.dumps(t["inputSchema"]):
                    matches.append({"code": "TOOL_DEF_CREDENTIAL_PARAM", "severity": "low", "tier": "advise",
                                    "tool": t["name"], "where": "inputSchema", "span": "api_key"})
            records = []
            if "drain" in job["server"]["id"] and "wallet" in job["server"]["id"]:
                records.append({"code": "THREAT_CRYPTO_DRAINER", "severity": "critical", "pattern": "*drain*wallet*",
                                "reason": "Crypto-drainer keyword in server identity.", "field": "server identity"})
            out[job["id"]] = {"id": job["id"], "patternMatches": matches, "recordMatches": records}
        return out


class Clock:
    """A settable UTC clock so continuity intervals can be crossed without sleeping."""

    def __init__(self, start: str = "2026-09-01T00:00:00Z") -> None:
        from datetime import datetime

        self.now = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    def __call__(self) -> str:
        return self.now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self.now += timedelta(**kwargs)


@pytest.fixture
def fake_mcp() -> FakeMcp:
    return FakeMcp()


@pytest.fixture
def world(settings, fake_mcp):
    """A wired HISTOR with fake MCP servers, a fake registry, a fake scanner and a settable clock."""
    from histor.mcpclient import McpReader
    from histor.service import build

    targets: list[Target] = []
    clock = Clock()
    scanner = FakeScanner()
    services = build(
        replace(settings, crawl_workers=4),
        crawler_kwargs={
            "reader": McpReader(timeout=5, allow_private=True, transport=fake_mcp.transport()),
            "harvest_fn": lambda _url: list(targets),
            "clock": clock,
        },
    )
    services.scanner = scanner
    services.crawler.scanner = scanner
    services.checker.scanner = scanner
    yield {"services": services, "targets": targets, "mcp": fake_mcp, "clock": clock, "scanner": scanner}
    services.close()
