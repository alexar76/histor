"""A read-only MCP client: ``initialize``, ``notifications/initialized``, ``tools/list``. Nothing else.

It never calls a tool, reads a resource or fetches a prompt, and it has no code path that could:
the method names are literals below. That is the whole contract with the servers HISTOR
observes — it reads what they publish to every client that connects, and no more.

Differences from the survey client in ``warden/scripts/mcp-survey/mcpclient.py``, each one a
requirement of MTL/1 or of running unattended against strangers:

* ``tools/list`` is **drained** across ``nextCursor`` pages (PROFILE.md section 5.1: a partial
  listing must not be digested);
* the connection is pinned to a checked public address (:mod:`histor.netguard`);
* one observation has ONE deadline across every request and page. A per-read timeout alone let
  an SSE keep-alive every few seconds, or a body trickled a byte at a time, hold a worker — and
  with it the whole crawl — for as long as the server liked;
* bodies are decompressed here, from the raw stream, with the size budget passed to zlib: the
  cap counts bytes we would hold, so a few hundred bytes of nested gzip cannot expand into a
  gigabyte first. At most one Content-Encoding, and only gzip or deflate;
* an SSE stream is split into lines as bytes, on CR/LF only (not on U+2028 and friends, which
  are ordinary characters inside a JSON string), with a bounded line length;
* the whole tool set is capped in bytes and in tools, across pages — the largest real set in
  the first production crawl was 1.5 MB and 1 376 tools;
* every value echoed back in a header is checked first, and anything the server does that we
  did not foresee becomes a status, never an exception out of :meth:`McpReader.list_tools`;
* redirects are not followed: the observation is of the endpoint the registry names.
"""

from __future__ import annotations

import json
import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

import httpx

from histor import __version__
from histor.netguard import BlockedAddress, PinnedTarget, resolve_public
from histor.untrusted import TooDeep, loads_limited

PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = f"histor/{__version__} (+https://histor.modelmarket.dev/#crawler; read-only: initialize + tools/list)"
MAX_BODY_BYTES = 4 * 1024 * 1024   # one response, decompressed
MAX_SET_BYTES = 3 * 1024 * 1024    # every tools/list page of one observation together
MAX_TOOLS = 5000
MAX_PAGES = 50
MAX_SSE_LINE = MAX_BODY_BYTES
_PROTOCOL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SESSION_RE = re.compile(r"^[\x21-\x7e]{1,256}$")  # MCP: visible ASCII only


@dataclass
class Observation:
    """What one connection attempt produced. ``status == "ok"`` means a fully drained tool list."""

    status: str
    detail: str = ""
    tools: list[Any] | None = None
    server_info: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = ""
    pages: int = 0
    address: str = ""


class _Stop(Exception):
    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(status)
        self.status = status
        self.detail = detail


class _DuplicateKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            # A duplicated member means two readers can disagree about what the server said.
            # Python keeps the last one silently; we refuse to guess.
            raise _DuplicateKey(key)
        out[key] = value
    return out


def parse_json(text: str | bytes) -> Any:
    return loads_limited(text, object_pairs_hook=_strict_object)


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self.at = time.monotonic() + seconds
        self.seconds = seconds

    def check(self) -> None:
        if time.monotonic() > self.at:
            raise _Stop("timeout", f"no complete answer within {self.seconds:g} s")


class _Decoder:
    """One Content-Encoding at most, decoded with the byte budget handed to zlib."""

    def __init__(self, encoding: str) -> None:
        codings = [c.strip() for c in encoding.lower().split(",") if c.strip() and c.strip() != "identity"]
        if len(codings) > 1:
            raise _Stop("protocol", f"stacked content-encodings are not accepted ({encoding})")
        self.coding = codings[0] if codings else ""
        if self.coding in ("gzip", "x-gzip"):
            self._z: Any = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif self.coding == "deflate":
            self._z = zlib.decompressobj(zlib.MAX_WBITS)
            self._raw_fallback = True
        elif self.coding:
            raise _Stop("protocol", f"content-encoding {self.coding!r} is not accepted")
        else:
            self._z = None

    def feed(self, chunk: bytes, budget: int) -> bytes:
        """Decode *chunk*, producing at most ``budget + 1`` bytes (one over means too large)."""
        if self._z is None:
            return chunk[: budget + 1]
        try:
            out = self._z.decompress(chunk, budget + 1)
        except zlib.error as exc:
            if self.coding == "deflate" and getattr(self, "_raw_fallback", False):
                # Some servers send raw deflate without the zlib header; retry once that way.
                self._raw_fallback = False
                self._z = zlib.decompressobj(-zlib.MAX_WBITS)
                return self.feed(chunk, budget)
            raise _Stop("protocol", f"{self.coding} body does not decode") from exc
        self._raw_fallback = False
        return out


def _matching(message: Any, request_id: int) -> bool:
    return isinstance(message, dict) and message.get("id") == request_id and (
        "result" in message or "error" in message
    )


class McpReader:
    def __init__(self, *, timeout: float = 20.0, allow_private: bool = False, transport: httpx.BaseTransport | None = None,
                 deadline: float | None = None):
        self.timeout = timeout
        self.allow_private = allow_private
        self._transport = transport
        # One observation — initialize, the notification and every tools/list page — must finish
        # inside this, whatever the server does with the stream.
        self.deadline = deadline if deadline is not None else max(3 * timeout, 60.0)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            follow_redirects=False,
            transport=self._transport,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        )

    def _post(self, client: httpx.Client, pinned: PinnedTarget, body: dict[str, Any], *, session: str | None,
              protocol: str | None, want_id: int | None, deadline: _Deadline,
              budget: int = MAX_BODY_BYTES) -> tuple[Any, httpx.Headers, int]:
        deadline.check()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Host": pinned.host_header,
        }
        if protocol:
            headers["MCP-Protocol-Version"] = protocol
        if session:
            headers["Mcp-Session-Id"] = session
        extensions = {"sni_hostname": pinned.sni_hostname} if pinned.sni_hostname else {}
        request = client.build_request(
            "POST", pinned.url, content=json.dumps(body).encode(), headers=headers, extensions=extensions
        )
        response = client.send(request, stream=True)
        try:
            if 300 <= response.status_code < 400:
                raise _Stop(f"http-{response.status_code}", "redirects are not followed")
            if response.status_code >= 400:
                raise _Stop(f"http-{response.status_code}")
            if want_id is None:
                return None, response.headers, 0
            decoder = _Decoder(response.headers.get("content-encoding", ""))
            ctype = response.headers.get("content-type", "").lower()
            budget = min(budget, MAX_BODY_BYTES)
            if "text/event-stream" in ctype:
                message, size = self._read_sse(response, want_id, decoder, deadline, budget)
            else:
                message, size = self._read_json(response, want_id, decoder, deadline, budget)
            return message, response.headers, size
        finally:
            response.close()

    @staticmethod
    def _decoded(response: httpx.Response, decoder: _Decoder, deadline: _Deadline, budget: int, what: str):
        """Decoded body chunks, never more than *budget* bytes in total, never past the deadline."""
        total = 0
        if hasattr(response, "_content"):
            # Built in memory (the test transports): httpx has already decoded it.
            source, decode = iter([response.content]), False
        else:
            source, decode = response.iter_raw(), True
        for raw in source:
            deadline.check()
            chunk = decoder.feed(raw, budget - total) if decode else raw[: budget - total + 1]
            total += len(chunk)
            if total > budget:
                raise _Stop("too-large", f"{what} exceeded {budget} bytes")
            yield chunk

    @classmethod
    def _read_json(cls, response: httpx.Response, want_id: int, decoder: _Decoder, deadline: _Deadline,
                   budget: int) -> tuple[Any, int]:
        chunks: list[bytes] = []
        total = 0
        for chunk in cls._decoded(response, decoder, deadline, budget, "response"):
            total += len(chunk)
            chunks.append(chunk)
        body = b"".join(chunks).strip()
        if not body:
            raise _Stop("protocol", "empty response body")
        message = cls._parse(body, "response")
        if isinstance(message, list):  # a JSON-RPC batch reply
            message = next((m for m in message if _matching(m, want_id)), None)
        if not _matching(message, want_id):
            raise _Stop("protocol", "no JSON-RPC response for our request id")
        return message, total

    @staticmethod
    def _parse(payload: bytes, what: str) -> Any:
        try:
            return parse_json(payload.decode("utf-8", "replace"))
        except _DuplicateKey as exc:
            raise _Stop("protocol", f"duplicate JSON member {str(exc)!r} in {what}") from exc
        except TooDeep as exc:
            raise _Stop("protocol", str(exc)) from exc
        except ValueError as exc:
            raise _Stop("protocol", f"{what} is not JSON") from exc

    @classmethod
    def _read_sse(cls, response: httpx.Response, want_id: int, decoder: _Decoder, deadline: _Deadline,
                  budget: int) -> tuple[Any, int]:
        total = 0
        buffer = b""
        data: list[bytes] = []

        def lines(final: bool):
            nonlocal buffer
            while True:
                cut = -1
                for i, byte in enumerate(buffer):
                    if byte in (10, 13):
                        cut = i
                        break
                if cut < 0:
                    if len(buffer) > MAX_SSE_LINE:
                        raise _Stop("too-large", f"an event-stream line exceeded {MAX_SSE_LINE} bytes")
                    if final and buffer:
                        line, buffer = buffer, b""
                        yield line
                    return
                if buffer[cut] == 13 and cut == len(buffer) - 1 and not final:
                    return  # a CR at the edge of a chunk may be the first half of CRLF
                line = buffer[:cut]
                step = 2 if buffer[cut] == 13 and buffer[cut + 1 : cut + 2] == b"\n" else 1
                buffer = buffer[cut + step :]
                yield line

        def event(line: bytes) -> Any:
            nonlocal data
            if line.startswith(b"data:"):
                data.append(line[5:].lstrip(b" "))
                return None
            if line == b"" and data:
                payload, data = b"\n".join(data), []
                try:
                    message = cls._parse(payload, "event")
                except _Stop as exc:
                    if exc.detail.startswith("duplicate") or "nests deeper" in exc.detail:
                        raise
                    return None  # a non-JSON event is someone else's; keep reading
                return message if _matching(message, want_id) else None
            return None

        for chunk in cls._decoded(response, decoder, deadline, budget, "event stream"):
            total += len(chunk)
            buffer += chunk
            for line in lines(final=False):
                found = event(line)
                if found is not None:
                    return found, total
        for line in [*lines(final=True), b""]:
            found = event(line)
            if found is not None:
                return found, total
        raise _Stop("protocol", "event stream ended without our response")

    def list_tools(self, url: str) -> Observation:
        """Observe *url*. Never raises for a server's behaviour; the status says what happened."""
        obs = Observation(status="error")
        deadline = _Deadline(self.deadline)
        try:
            # Resolved once: every request of the session goes to the same checked address, so
            # a round-robin name cannot split one observation across two machines.
            pinned = resolve_public(url, allow_private=self.allow_private)
            obs.address = pinned.address
            with self._client() as client:
                init, headers, _ = self._post(
                    client, pinned,
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "histor", "version": __version__},
                    }},
                    session=None, protocol=None, want_id=1, deadline=deadline,
                )
                if "error" in init or not isinstance(init.get("result"), dict):
                    raise _Stop("protocol", "initialize returned an error")
                result = init["result"]
                info = result.get("serverInfo")
                obs.server_info = info if isinstance(info, dict) else {}
                negotiated = result.get("protocolVersion")
                obs.protocol_version = negotiated[:40] if isinstance(negotiated, str) else ""
                session = headers.get("mcp-session-id")
                if session is not None and not _SESSION_RE.match(session):
                    raise _Stop("protocol", "Mcp-Session-Id is not visible ASCII")
                # Echoed back in a header only if it looks like a protocol version; httpx would
                # otherwise raise on a non-ASCII value, and a CR/LF would be header injection.
                protocol = obs.protocol_version if _PROTOCOL_RE.match(obs.protocol_version) else PROTOCOL_VERSION
                try:
                    self._post(client, pinned, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                               session=session, protocol=protocol, want_id=None, deadline=deadline)
                except (_Stop, httpx.HTTPError) as exc:
                    if isinstance(exc, _Stop) and exc.status == "timeout":
                        raise
                    # a notification needs no answer; some servers 202, some 4xx it

                tools: list[Any] = []
                spent = 0
                cursor: str | None = None
                request_id = 2
                while True:
                    params: dict[str, Any] = {"cursor": cursor} if cursor else {}
                    reply, _, size = self._post(
                        client, pinned,
                        {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params},
                        session=session, protocol=protocol, want_id=request_id, deadline=deadline,
                        budget=MAX_SET_BYTES - spent,
                    )
                    spent += size
                    obs.pages += 1
                    if "error" in reply or not isinstance(reply.get("result"), dict):
                        raise _Stop("protocol", "tools/list returned an error")
                    page = reply["result"].get("tools")
                    if not isinstance(page, list):
                        raise _Stop("protocol", "tools/list result has no tools array")
                    tools.extend(page)
                    if len(tools) > MAX_TOOLS:
                        raise _Stop("too-large", f"more than {MAX_TOOLS} tools")
                    nxt = reply["result"].get("nextCursor")
                    if not nxt:
                        break
                    if not isinstance(nxt, str) or nxt == cursor:
                        raise _Stop("partial", "pagination cursor did not advance")
                    if obs.pages >= MAX_PAGES:
                        raise _Stop("partial", f"more than {MAX_PAGES} pages; not drained")
                    cursor = nxt
                    request_id += 1
                obs.tools = tools
                obs.status = "ok"
        except BlockedAddress as exc:
            obs.status, obs.detail = "blocked-address", str(exc)
        except _Stop as exc:
            obs.status, obs.detail = exc.status, exc.detail
            if exc.status == "too-large" and exc.detail.startswith("response exceeded"):
                obs.detail = f"tool set exceeded {MAX_SET_BYTES} bytes" if obs.pages else exc.detail
        except httpx.TimeoutException:
            obs.status, obs.detail = "timeout", ""
        except httpx.ConnectError as exc:
            obs.status, obs.detail = "connect", type(exc).__name__
        except httpx.HTTPError as exc:
            obs.status, obs.detail = "network", type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - "never raises for a server's behaviour" is the contract
            obs.status, obs.detail = "client-error", type(exc).__name__
        if obs.status != "ok":
            obs.tools = None
        return obs
