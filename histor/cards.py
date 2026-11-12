"""MCP server cards: the operator's own description of a server's tools, kept as a declaration.

Some sites keep HISTOR off their MCP path in robots.txt and still publish a server card at a path it allows
(signupsheet.page: ``Disallow: /mcp``, ``Allow: /mcp/server-card``); some servers answer ``tools/list`` only
with a key and list their tools in a public card. A card that lists every tool with a name and an
``inputSchema`` is digested like an observation (histor.subject.tool_set_digest) and recorded as a set the
publisher declares, with ``source = 'card'`` beside the registry entry and the declaration file
(histor.declared). It stays the publisher's word: never an observation, never a label. Where HISTOR also
reads the server, the page shows the card and the served set side by side; on 2026-11-02, 49 of 137 cards
differed from what their server served.

Where a card is looked for, in this order: the endpoint's own ``<endpoint>/server-card``, the cards the
host's AI Catalog lists, the host's ``/.well-known/mcp/server-card.json``, and the host's
``/.well-known/mcp.json``. The last has no standard behind it but is where many sites put theirs: on
2026-11-02, 34 of 400 hosts with neither of the first two served one there, 4 of them with a full tool list
(aicom artifacts/histor-card-paths-2026-11-02).

The AI Catalog (https://ai-catalog.io/, ``/.well-known/ai-catalog.json``) is where MCP's Server Card
extension (SEP-2127, Final) sends a client that knows only a domain: each entry typed
``application/mcp-server-card+json`` links to a card (``url``, on any host) or carries one (``data``). A card
in that extension's schema says where to connect and lists no tools on purpose, so it declares no set here;
HISTOR records that the card exists and which endpoint it names. Nested catalogues are followed four levels
deep, at most ``MAX_CATALOG_DOCUMENTS`` documents and ``MAX_CATALOG_CARDS`` cards for one host.

Which card belongs to which endpoint:

* A card that names endpoints (``transport.endpoint``/``url``, ``endpoint``, ``url``, ``mcpUrl``,
  ``remote.url``, ``remotes[]``, ``servers[]``, ``aliases[]``, ``endpoints`` as a list or as a map of
  transports; relative ones against the card's URL) describes those and no other.
* A card that names none describes the endpoint whose path it extends (``<endpoint>/server-card``), or the
  host's only listed remote endpoint when it is one of the host-wide cards or the only card its catalogue lists.

Reading: only paths the site's robots.txt allows HISTOR (histor.robots), through the same address checks as
every request, no redirects, at most 1 MiB, once a day per endpoint; a host without a card is asked again
after a week. A card that is gone or no longer lists its tools withdraws what it declared; a failed read
withdraws nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from histor.mcpclient import USER_AGENT
from histor.netguard import BlockedAddress, resolve_public
from histor.robots import origin_of

WELL_KNOWN = "/.well-known/mcp/server-card.json"
MCP_JSON = "/.well-known/mcp.json"
HOST_WIDE = (WELL_KNOWN, MCP_JSON)
AI_CATALOG = "/.well-known/ai-catalog.json"
SERVER_CARD_TYPE = "application/mcp-server-card+json"
CATALOG_TYPE = "application/ai-catalog+json"
CATALOG_MAJOR = 1                 # the specVersion major this reader understands
MAX_CATALOG_DEPTH = 4             # the specification's recommended limit for nested catalogues
MAX_CATALOG_DOCUMENTS = 8         # catalogue documents read for one host
MAX_CATALOG_CARDS = 20            # hosted cards read for one host's catalogue
MAX_CATALOG_ENTRIES = 5000        # entries looked at across one host's catalogues
# A card is asked for by its media type (SEP-2127 discovery: the client "SHOULD request that URL expressing
# the application/mcp-server-card+json media type"); plain JSON stays acceptable for the older cards.
ACCEPT = f"{SERVER_CARD_TYPE}, {CATALOG_TYPE}, application/json;q=0.9"
SERVER_CARD_SCHEMA_PREFIX = "https://static.modelcontextprotocol.io/schemas/"
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
        headers = {"Host": pinned.host_header, "Accept": ACCEPT, "User-Agent": USER_AGENT,
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


def catalog_url(endpoint: str) -> str:
    """Where the AI Catalog of *endpoint*'s host is looked for."""
    return f"{origin_of(endpoint)}{AI_CATALOG}"


@dataclass(frozen=True)
class Listed:
    """The MCP Server Cards one host's AI Catalog lists."""
    status: str = "absent"                                  # the root catalogue's read: ok | absent | robots | error
    urls: tuple[str, ...] = ()                              # hosted cards, absolute, in catalogue order
    inline: tuple[tuple[str, dict[str, Any]], ...] = ()     # (catalogue URL # identifier, the card)
    detail: str = ""

    @property
    def cards(self) -> tuple[str, ...]:
        """Every listed card's key in *reads*: the hosted ones, then the inline ones."""
        return self.urls + tuple(key for key, _doc in self.inline)


def _media_type(value: Any) -> str:
    return value.split(";", 1)[0].strip().lower() if isinstance(value, str) else ""


def catalog_version_problem(doc: dict[str, Any]) -> str:
    """Why this document is not a catalogue this reader may use; "" when it is one. A consumer accepts
    its own major version whatever the minor, and refuses a higher major instead of guessing at it
    (specification, "Version Handling")."""
    version = doc.get("specVersion")
    if not isinstance(version, str) or not isinstance(doc.get("entries"), list):
        return "not an AI Catalog (no specVersion and entries)"
    major, _, minor = version.partition(".")
    if not (major.isdigit() and minor.isdigit()):
        return f"specVersion {version[:20]!r} is not Major.Minor"
    if int(major) != CATALOG_MAJOR:
        return f"specVersion {version} is not supported (this reader understands {CATALOG_MAJOR}.x)"
    return ""


def walk_catalog(url: str, read: Callable[[str], Read]) -> Listed:
    """Read the AI Catalog at *url* and the catalogues it nests, and return the Server Cards they list.

    *read* is the reader every card goes through (robots.txt, address checks, no redirects, size limit).
    An entry counts when its ``type`` is the Server Card media type and it has exactly one of ``url`` and
    ``data``. A hosted card may be on any host; it must be https unless the catalogue itself was read over
    http. A catalogue is never read twice in one walk, so a catalogue that nests itself ends the walk.
    """
    root = read(url)
    if root.status != "ok":
        return Listed(root.status, detail=root.detail)
    problem = catalog_version_problem(root.doc)
    if problem:
        return Listed("absent", detail=problem)
    urls: list[str] = []
    inline: list[tuple[str, dict[str, Any]]] = []
    notes: list[str] = []
    visited = {url}
    budget = {"documents": 1, "entries": 0}
    allowed = {"https", urlsplit(url).scheme}

    def absolute(value: Any, base: str) -> str | None:
        if not isinstance(value, str) or not value.strip() or len(value) > 2048:
            return None
        target = urljoin(base, value.strip())
        return target if urlsplit(target).scheme in allowed else None

    def walk(doc: dict[str, Any], base: str, depth: int) -> None:
        for index, entry in enumerate(doc.get("entries") or []):
            budget["entries"] += 1
            if budget["entries"] > MAX_CATALOG_ENTRIES:
                notes.append(f"stopped after {MAX_CATALOG_ENTRIES} entries")
                return
            if not isinstance(entry, dict) or ("url" in entry) == ("data" in entry):
                continue        # exactly one of url and data, or the entry says nothing usable
            kind = _media_type(entry.get("type"))
            if kind == SERVER_CARD_TYPE:
                if "data" in entry:
                    if isinstance(entry["data"], dict):
                        name = entry.get("identifier") if isinstance(entry.get("identifier"), str) else str(index)
                        inline.append((f"{base}#{quote(name, safe=':@._-')}", entry["data"]))
                    continue
                target = absolute(entry.get("url"), base)
                if target and target not in urls:
                    if len(urls) >= MAX_CATALOG_CARDS:
                        if not any(n.startswith("more than") for n in notes):
                            notes.append(f"more than {MAX_CATALOG_CARDS} cards listed; the first {MAX_CATALOG_CARDS} were read")
                        continue
                    urls.append(target)
            elif kind == CATALOG_TYPE:
                if depth >= MAX_CATALOG_DEPTH:
                    notes.append(f"catalogues nested deeper than {MAX_CATALOG_DEPTH} were not followed")
                    continue
                if "data" in entry:
                    if isinstance(entry["data"], dict) and not catalog_version_problem(entry["data"]):
                        walk(entry["data"], base, depth + 1)
                    continue
                target = absolute(entry.get("url"), base)
                if not target or target in visited:
                    continue
                if budget["documents"] >= MAX_CATALOG_DOCUMENTS:
                    if not any(n.startswith("only") for n in notes):
                        notes.append(f"only {MAX_CATALOG_DOCUMENTS} catalogue documents were read")
                    continue
                visited.add(target)
                budget["documents"] += 1
                nested = read(target)
                if nested.status == "ok" and not catalog_version_problem(nested.doc):
                    walk(nested.doc, target, depth + 1)

    walk(root.doc, url, 1)
    return Listed("ok", tuple(urls), tuple(inline), "; ".join(notes)[:300])


def _no_tools_detail(doc: dict[str, Any]) -> str:
    schema = doc.get("$schema")
    if isinstance(schema, str) and schema.startswith(SERVER_CARD_SCHEMA_PREFIX) and "server-card" in schema:
        return "a Server Card in the MCP schema (SEP-2127): it lists no tools by design"
    return "no complete tool list (each tool needs a name and an inputSchema)"


def judge(endpoint: str, reads: dict[str, Read], hosts_endpoints: int, listed: tuple[str, ...] = ()) -> Verdict:
    """What the card reads say about *endpoint*. *reads* maps each candidate URL to its read; *listed* are
    the cards the host's AI Catalog lists (their keys in *reads*), looked at after the endpoint's own path
    and before the host-wide cards. The first card with a full tool list for this endpoint decides; without
    one, a card that describes this endpoint (and lists no tools) is reported before a card that describes
    another."""
    target = normalise(endpoint)
    about_this = elsewhere = None     # a card that describes this endpoint outranks one that describes another
    origin = origin_of(endpoint)
    path = (urlsplit(endpoint).path or "/").rstrip("/")
    own = f"{origin}{path}/server-card" if path else None
    host_wide = [f"{origin}{p}" for p in HOST_WIDE]
    from_catalog = [u for u in listed if u != own and u not in host_wide]
    for url in [u for u in (own, *from_catalog, *host_wide) if u]:
        r = reads.get(url)
        if r is None or r.status != "ok":
            continue
        named = named_endpoints(r.doc, url)
        if named and target not in named:
            elsewhere = elsewhere or Verdict(OTHER_ENDPOINT, url, detail="the card names " + ", ".join(sorted(named))[:300])
            continue
        if not named and url != own and (hosts_endpoints > 1 or (url in from_catalog and len(listed) > 1)):
            what = (f"the card names no endpoint and its catalogue lists {len(listed)} cards"
                    if url in from_catalog and hosts_endpoints <= 1
                    else f"the card names no endpoint and this host lists {hosts_endpoints}")
            elsewhere = elsewhere or Verdict(AMBIGUOUS, url, detail=what)
            continue
        tools = full_tools(r.doc)
        if tools is None:
            about_this = about_this or Verdict(NO_TOOLS, url, detail=_no_tools_detail(r.doc))
            continue
        return Verdict(DECLARED, url, tools)
    seen_card = about_this or elsewhere
    if seen_card is not None:
        return seen_card
    statuses = [reads[u].status for u in candidates(endpoint) if u in reads]
    if statuses and all(s == "robots" for s in statuses):
        return Verdict(ROBOTS, detail="robots.txt does not allow HISTOR any card path")
    if "error" in statuses:
        errors = [reads[u].detail for u in candidates(endpoint) if u in reads and reads[u].status == "error"]
        return Verdict(ERROR, detail="; ".join(errors)[:300])
    return Verdict(ABSENT, detail="; ".join(f"{u}: {reads[u].detail}" for u in candidates(endpoint) if u in reads)[:300])
