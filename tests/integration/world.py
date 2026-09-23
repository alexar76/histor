"""A small real world on loopback: an MCP registry and the MCP servers it lists.

Real sockets, real HTTP, real streamable-http framing (JSON and SSE) — so the integration suite
exercises the parts the unit tests replace: the pinned connection, uvicorn, the registry
pagination, and the WARDEN sidecar reading real definitions.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class World:
    def __init__(self) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        # server name -> {"tools": [...], "sse": bool, "status": int | None, "page": int | None}
        self.servers: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        app = Starlette(routes=[
            Route("/v0/servers", self.registry),
            Route("/mcp/{name:path}", self.mcp, methods=["POST"]),
        ])
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> World:
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake world did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    def add(self, name: str, tools: list[dict[str, Any]], **opts: Any) -> str:
        self.servers[name] = {"tools": tools, **opts}
        return f"{self.base}/mcp/{name}"

    async def registry(self, request: Request) -> JSONResponse:
        # Two per page, so the harvester's cursor loop is exercised for real.
        names = sorted(self.servers)
        start = int(request.query_params.get("cursor") or 0)
        page = names[start:start + 2]
        items = [{
            "server": {"name": n, "version": "1.0.0", "title": n.split("/")[-1],
                       "remotes": [{"type": "streamable-http", "url": f"{self.base}/mcp/{n}"}]},
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "isLatest": True}},
        } for n in page]
        meta = {"count": len(items)}
        if start + 2 < len(names):
            meta["nextCursor"] = str(start + 2)
        return JSONResponse({"servers": items, "metadata": meta})

    async def mcp(self, request: Request) -> Response:
        name = request.path_params["name"]
        spec = self.servers.get(name)
        body = json.loads(await request.body())
        method = body.get("method", "")
        self.calls.append((name, method))
        if spec is None:
            return Response(status_code=404)
        if spec.get("status"):
            return Response(status_code=spec["status"])
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": name, "version": "1.0.0"}}
        elif method == "tools/list":
            tools = spec["tools"]
            size = spec.get("page")
            if size:
                start = int((body.get("params") or {}).get("cursor") or 0)
                result = {"tools": tools[start:start + size]}
                if start + size < len(tools):
                    result["nextCursor"] = str(start + size)
            else:
                result = {"tools": tools}
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": "nope"}})
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"Mcp-Session-Id": f"sess-{name}"}
        if spec.get("sse"):
            text = f"event: message\ndata: {json.dumps(message)}\n\n"
            return Response(text, media_type="text/event-stream", headers=headers)
        return JSONResponse(message, headers=headers)
