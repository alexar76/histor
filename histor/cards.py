"""MCP server cards: the operator's own description of a server's tools, kept as a declaration.

Some sites keep HISTOR off their MCP path in robots.txt and still publish a server card at a path it allows
(signupsheet.page: ``Disallow: /mcp``, ``Allow: /mcp/server-card``); some servers answer ``tools/list`` only
with a key and list their tools in a public card. A card that lists every tool with a name and an
``inputSchema`` is digested like an observation (histor.subject.tool_set_digest) and recorded as a set the
publisher declares, with ``source = 'card'`` beside the registry entry and the declaration file
(histor.declared). It stays the publisher's word: never an observation, never a label. Where HISTOR also
reads the server, the page shows the card and the served set side by side; on 2026-11-02, 49 of 137 cards
differed from what their server served.

Where a card is looked for, in this order: the endpoint's own ``<endpoint>/server-card``, the host's
``/.well-known/mcp/server-card.json``, and the host's ``/.well-known/mcp.json``. The last has no standard
behind it but is where many sites put theirs: on 2026-11-02, 34 of 400 hosts with neither of the first two
served one there, 4 of them with a full tool list (aicom artifacts/histor-card-paths-2026-11-02).

Which card belongs to which endpoint:

* A card that names endpoints (``transport.endpoint``/``url``, ``endpoint``, ``url``, ``mcpUrl``,
  ``remote.url``, ``remotes[]``, ``servers[]``, ``aliases[]``, ``endpoints`` as a list or as a map of
  transports; relative ones against the card's URL) describes those and no other.
* A card that names none describes the endpoint whose path it extends (``<endpoint>/server-card``), or the
  host's only listed remote endpoint when it is one of the host-wide cards.

Reading: only paths the site's robots.txt allows HISTOR (histor.robots), through the same address checks as
every request, no redirects, at most 1 MiB, once a day per endpoint; a host without a card is asked again
after a week. A card that is gone or no longer lists its tools withdraws what it declared; a failed read
withdraws nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from histor.mcpclient import USER_AGENT
from histor.netguard import BlockedAddress, resolve_public
from histor.robots import origin_of

WELL_KNOWN = "/.well-known/mcp/server-card.json"
MCP_JSON = "/.well-known/mcp.json"
HOST_WIDE = (WELL_KNOWN, MCP_JSON)
MAX_CARD_BYTES = 1024 * 1024
MAX_TOOLS = 5000
FETCH_TIMEOUT_S = 10.0
FRESH_S = 24 * 3600
ABSENT_RECHECK_S = 7 * 24 * 3600

DECLARED = "declared"          # a full tool list for this endpoint
NO_TOOLS = "no-tools"          # a card, without a complete tool list
OTHER_ENDPOINT = "other-endpoint"
AMBIGUOUS = "ambiguous"        # a host-wide card naming no endpoint, on a host with several
UNDIGESTIBLE = "undigestible"
ABSENT = "absent"              # no card at any allowed path
ROBOTS = "robots"              # every candidate path disallowed
ERROR = "error"                # unreachable, not JSON, too large: says nothing about the card
# What a card read decides: these replace or withdraw the card's declaration; ROBOTS and ERROR do not.
DECISIVE = frozenset({DECLARED, NO_TOOLS, OTHER_ENDPOINT, AMBIGUOUS, UNDIGESTIBLE, ABSENT})


def normalise(url: str) -> str:
    parts = urlsplit(url)
    return f"{origin_of(url)}{(parts.path or '/').rstrip('/') or '/'}"


def candidates(endpoint: str) -> list[str]:
    """The card URLs for *endpoint*: its own ``<path>/server-card``, then the host-wide cards."""
    origin = origin_of(endpoint)
    path = (urlsplit(endpoint).path or "/").rstrip("/")
    own = f"{origin}{path}/server-card" if path else None
    return [u for u in (own, *(f"{origin}{p}" for p in HOST_WIDE)) if u]


def is_host_wide(card_url: str) -> bool:
    return urlsplit(card_url).path in HOST_WIDE


def named_endpoints(doc: dict[str, Any], card_url: str) -> set[str]:
    """Every endpoint the card says it describes, as absolute normalised URLs."""
    found: list[Any] = []
    for value in (doc.get("transport"), doc.get("remote")):
        if isinstance(value, dict):
            found += [value.get("endpoint"), value.get("url")]
    found += [doc.get("endpoint"), doc.get("url"), doc.get("mcpUrl")]
    for key in ("remotes", "endpoints", "servers", "aliases"):
        items = doc.get(key)
        if isinstance(items, dict):        # mcp.json: {"streamable_http": "https://…/mcp"}
            items = list(items.values())
        if isinstance(items, list):
            found += [v for i in items for v in ((i.get("url"), i.get("endpoint")) if isinstance(i, dict) else (i,))]
    out = set()
    for value in found:
        if isinstance(value, str) and value.strip() and len(value) < 2048:
            absolute = urljoin(card_url, value.strip())
            if urlsplit(absolute).scheme in ("http", "https"):
                out.add(normalise(absolute))
    return out


def full_tools(doc: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The card's tools when every one has a name and an inputSchema; otherwise None."""
    tools = doc.get("tools")
    if not isinstance(tools, list) or not tools or len(tools) > MAX_TOOLS:
        return None
    if not all(isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"] and isinstance(t.get("inputSchema"), dict)
               for t in tools):
        return None
    return tools


@dataclass(frozen=True)
class Read:
    status: str        # ok | absent | robots | error
    doc: dict[str, Any] | None = None
    detail: str = ""


@dataclass(frozen=True)
class Verdict:
    status: str
    url: str | None = None
    tools: list[dict[str, Any]] | None = None
    detail: str = ""


class CardReader:
    def __init__(self, *, robots: Any = None, allow_private: bool = False, transport: httpx.BaseTransport | None = None,
                 timeout: float = FETCH_TIMEOUT_S) -> None:
        self.robots = robots          # histor.robots.Policy; None only in tests
        self.allow_private = allow_private
        self.transport = transport
        self.timeout = timeout

    def read(self, url: str) -> Read:
        if self.robots is not None:
            try:
                if not self.robots.decide(url).allowed:
                    return Read("robots", detail="robots.txt does not allow HISTOR this path")
            except Exception as exc:  # noqa: BLE001 - when unsure, do not read
                return Read("error", detail=f"robots check failed ({type(exc).__name__})")
        try:
            pinned = resolve_public(url, allow_private=self.allow_private)
        except BlockedAddress as exc:
            return Read("error", detail=f"refused: {exc}")
        headers = {"Host": pinned.host_header, "Accept": "application/json", "User-Agent": USER_AGENT,
                   "Accept-Encoding": "identity"}
        extensions = {"sni_hostname": pinned.sni_hostname} if pinned.sni_hostname else {}
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 5.0)),
                              follow_redirects=False, transport=self.transport) as client:
                response = client.send(client.build_request("GET", pinned.url, headers=headers, extensions=extensions),
                                       stream=True)
                try:
                    code = response.status_code
                    if 400 <= code < 500:
                        return Read("absent", detail=f"http-{code}")
                    if code != 200:
                        return Read("error", detail=f"http-{code}" + (": redirects are not followed" if 300 <= code < 400 else ""))
                    body = bytearray()
                    source = [response.content] if hasattr(response, "_content") else response.iter_raw()
                    for chunk in source:
                        body.extend(chunk)
                        if len(body) > MAX_CARD_BYTES:
                            return Read("error", detail=f"larger than {MAX_CARD_BYTES // 1024} KiB")
                finally:
                    response.close()
        except httpx.HTTPError as exc:
            return Read("error", detail=f"unreachable: {type(exc).__name__}")
        try:
            doc = json.loads(bytes(body))
        except ValueError:
            return Read("absent", detail="not JSON")
        if not isinstance(doc, dict):
            return Read("absent", detail="not a JSON object")
        return Read("ok", doc=doc)


def judge(endpoint: str, reads: dict[str, Read], hosts_endpoints: int) -> Verdict:
    """What the card reads say about *endpoint*. *reads* maps each candidate URL to its read."""
    target = normalise(endpoint)
    seen_card = None
    for url in candidates(endpoint):
        r = reads.get(url)
        if r is None or r.status != "ok":
            continue
        named = named_endpoints(r.doc, url)
        own_path = not is_host_wide(url)
        if named and target not in named:
            seen_card = seen_card or Verdict(OTHER_ENDPOINT, url, detail="the card names " + ", ".join(sorted(named))[:300])
            continue
        if not named and not own_path and hosts_endpoints > 1:
            seen_card = seen_card or Verdict(AMBIGUOUS, url, detail=f"the card names no endpoint and this host lists {hosts_endpoints}")
            continue
        tools = full_tools(r.doc)
        if tools is None:
            seen_card = seen_card or Verdict(NO_TOOLS, url, detail="no complete tool list (each tool needs a name and an inputSchema)")
            continue
        return Verdict(DECLARED, url, tools)
    if seen_card is not None:
        return seen_card
    statuses = [reads[u].status for u in candidates(endpoint) if u in reads]
    if statuses and all(s == "robots" for s in statuses):
        return Verdict(ROBOTS, detail="robots.txt does not allow HISTOR any card path")
    if "error" in statuses:
        errors = [reads[u].detail for u in candidates(endpoint) if u in reads and reads[u].status == "error"]
        return Verdict(ERROR, detail="; ".join(errors)[:300])
    return Verdict(ABSENT, detail="; ".join(f"{u}: {reads[u].detail}" for u in candidates(endpoint) if u in reads)[:300])
