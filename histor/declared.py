"""Tool sets a publisher declares: in its registry entry, or in a file on its own domain.

A package's tool set can depend on the machine it runs on. A launcher hands stdio to a program
installed next to it and lists that program's tools there, while in HISTOR's sandbox, where the
program is missing, it lists one "install me" tool. A real key can unlock tools a placeholder does
not. HISTOR observes one of those sets; the publisher knows the others.

So a publisher may name them, by MTL/1 tool-set digest, in the server.json it publishes to the
official MCP registry, under the field the registry keeps for downstream registries::

    "_meta": {
      "io.modelcontextprotocol.registry/publisher-provided": {
        "dev.modelmarket.histor/tool-sets": {
          "mtl": "1",
          "sets": [{"digest": "sha256-…", "count": 23, "when": "the Auten runner is installed"}],
          "url": "https://auten.ai/.well-known/mcp-tool-sets.json"
        }
      }
    }

``sets``, ``url`` or both. A runner released apart from the package, per operating system, changes
the set without a new registry entry; the file at ``url`` is where such a publisher keeps the list,
updated with each runner release, and HISTOR reads it every hour. The file is the same object
(``{"mtl": "1", "sets": [...]}``) with room for more sets than the registry's ~4 KB allow.

Whose word it is. The registry authenticates the namespace: ``ai.auten/*`` was proven by whoever
controls auten.ai, ``io.github.alexar76/*`` by the GitHub account alexar76. A file is taken only
from that domain (or a subdomain) or that account's GitHub hosts, so it carries the same authority
as the entry that points at it, and nobody else can speak for the publisher through it.

It stays the publisher's word: HISTOR records it with the dates it read it and answers /check
with it, and never counts it as an observation, never issues a label about it, never reads the
tools behind it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

import httpx

from histor.mcpclient import MAX_TOOLS, USER_AGENT
from histor.netguard import BlockedAddress, resolve_public

PUBLISHER_PROVIDED = "io.modelcontextprotocol.registry/publisher-provided"
FIELD = "dev.modelmarket.histor/tool-sets"
MAX_SETS = 8          # in the registry entry: ~4 KB of publisher-provided metadata, 8 sets fit with room
MAX_FILE_SETS = 64    # in a file on the publisher's domain: runner versions × operating systems
MAX_WHEN = 200        # a condition, not a document
MAX_FILE_BYTES = 64 * 1024
FETCH_TIMEOUT_S = 10.0
SRI = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
_GITHUB_USER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38})$")


class DeclarationError(ValueError):
    """A declaration file HISTOR could not take: unreachable, refused, too large or malformed."""


def _when(value: Any) -> str | None:
    """The condition, as one line of visible text: control and format characters (zero-width,
    bidi overrides) are dropped, so what a page shows is what the publisher wrote."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if ch in "\t\r\n" else ch for ch in value
                   if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn") or ch in "\t\r\n")
    text = " ".join(text.split())
    return text[:MAX_WHEN] or None


def url_refusal(url: Any, server_name: str) -> str | None:
    """Why HISTOR will not read a declaration file at *url* for *server_name*, or None.

    The file must be on the domain the registry verified for the server's namespace (or a
    subdomain): ``ai.auten/mcp`` → auten.ai. A GitHub namespace ``io.github.<user>`` may use
    ``<user>.github.io`` or that user's raw/gist files. https only, the default port, no credentials.
    """
    if not isinstance(url, str) or not url or len(url) > 2000:
        return '"url" must be an https URL'
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return '"url" does not parse'
    if parts.scheme != "https":
        return '"url" must be https'
    if parts.username or parts.password:
        return '"url" must not carry credentials'
    if port not in (None, 443):
        return '"url" must use the default https port'
    host = (parts.hostname or "").rstrip(".")
    namespace = server_name.split("/", 1)[0].strip().lower()
    if namespace.startswith("io.github."):
        user = namespace[len("io.github."):]
        if not _GITHUB_USER.match(user):
            return "the server name has no GitHub user HISTOR can bind the file to"
        path = parts.path.lower()
        if host == f"{user}.github.io" or (
                host in ("raw.githubusercontent.com", "gist.githubusercontent.com") and path.startswith(f"/{user}/")):
            return None
        return f'"url" must be on {user}.github.io or under /{user}/ on raw.githubusercontent.com, the account the registry verified'
    labels = [x for x in namespace.split(".") if x]
    if len(labels) < 2:
        return "the server name has no domain HISTOR can bind the file to"
    domain = ".".join(reversed(labels))
    if host == domain or host.endswith("." + domain):
        return None
    return f'"url" must be on {domain} or a subdomain, the domain the registry verified for {namespace}'


def parse_sets(entries: Any, limit: int, where: str = "sets") -> tuple[list[dict[str, Any]], list[str]]:
    """Validated sets from a ``sets`` list, and why any entry was skipped."""
    if not isinstance(entries, list) or not entries:
        return [], [f'"{where}" must be a non-empty list']
    sets: list[dict[str, Any]] = []
    ignored: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if len(sets) == limit:
            ignored.append(f"{where}[{i}:]: more than {limit} sets")
            break
        if not isinstance(entry, dict):
            ignored.append(f"{where}[{i}]: not an object")
            continue
        digest = entry.get("digest")
        if not isinstance(digest, str) or not SRI.match(digest):
            ignored.append(f"{where}[{i}]: digest is not an MTL/1 tool-set digest (sha256-<base64>)")
            continue
        if digest in seen:
            ignored.append(f"{where}[{i}]: the same digest twice")
            continue
        count = entry.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_TOOLS):
            ignored.append(f"{where}[{i}]: count must be an integer from 0 to {MAX_TOOLS}")
            continue
        seen.add(digest)
        out: dict[str, Any] = {"digest": digest}
        if count is not None:
            out["count"] = count
        when = _when(entry.get("when"))
        if when:
            out["when"] = when
        sets.append(out)
    return sets, ignored


def declared_tool_sets(server: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """(sets, ignored, url) from one server.json. A set is ``{"digest", "count"?, "when"?}``;
    *ignored* says, per entry or for the whole field, why HISTOR did not take it; *url* is the
    declaration file HISTOR will read, already checked against the server's namespace."""
    meta = server.get("_meta") if isinstance(server.get("_meta"), dict) else {}
    provided = meta.get(PUBLISHER_PROVIDED) if isinstance(meta.get(PUBLISHER_PROVIDED), dict) else {}
    if FIELD not in provided:
        return [], [], None
    block = provided[FIELD]
    if not isinstance(block, dict):
        return [], [f"{FIELD} is not an object"], None
    if block.get("mtl") != "1":
        return [], ['"mtl" must be "1": the digests are MTL/1 tool-set digests'], None
    url: str | None = None
    ignored: list[str] = []
    if "url" in block:
        refusal = url_refusal(block["url"], str(server.get("name") or ""))
        if refusal:
            ignored.append(refusal)
        else:
            url = block["url"]
    if "sets" not in block:
        if url is None and not ignored:
            ignored.append('"sets" or "url" is needed')
        return [], ignored, url
    sets, skipped = parse_sets(block["sets"], MAX_SETS)
    return sets, ignored + skipped, url


def parse_file(body: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """A declaration file: ``{"mtl": "1", "sets": [...]}``. Raises DeclarationError when nothing in
    it can be taken, so a broken file never reads as "the publisher declares nothing"."""
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DeclarationError("the file is not JSON") from exc
    if not isinstance(doc, dict):
        raise DeclarationError("the file is not a JSON object")
    if doc.get("mtl") != "1":
        raise DeclarationError('"mtl" must be "1"')
    if doc.get("sets") == []:
        return [], []  # an explicit empty list: the publisher withdraws every set it listed here
    sets, ignored = parse_sets(doc.get("sets"), MAX_FILE_SETS)
    if not sets:
        raise DeclarationError("; ".join(ignored[:3]) or "no valid sets")
    return sets, ignored


class DeclarationFetcher:
    """Reads declaration files. The address is checked and pinned like every endpoint HISTOR
    dials (histor.netguard): the URL comes from a registry entry, so it must not reach the host's
    own services. No redirects, a short timeout, at most 64 KiB."""

    def __init__(self, *, allow_private: bool = False, transport: httpx.BaseTransport | None = None,
                 timeout: float = FETCH_TIMEOUT_S, resolver: Any = None) -> None:
        self.allow_private = allow_private
        self.transport = transport
        self.timeout = timeout
        self.resolver = resolver  # tests only: a stand-in for DNS

    def fetch(self, url: str) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            pinned = resolve_public(url, allow_private=self.allow_private,
                                    **({"resolver": self.resolver} if self.resolver else {}))
        except BlockedAddress as exc:
            raise DeclarationError(f"refused: {exc}") from exc
        headers = {"Host": pinned.host_header, "Accept": "application/json", "User-Agent": USER_AGENT}
        extensions = {"sni_hostname": pinned.sni_hostname} if pinned.sni_hostname else {}
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 5.0)),
                              follow_redirects=False, transport=self.transport) as client:
                request = client.build_request("GET", pinned.url, headers=headers, extensions=extensions)
                response = client.send(request, stream=True)
                try:
                    if 300 <= response.status_code < 400:
                        raise DeclarationError(f"http-{response.status_code}: redirects are not followed")
                    if response.status_code != 200:
                        raise DeclarationError(f"http-{response.status_code}")
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_FILE_BYTES:
                            raise DeclarationError(f"the file is larger than {MAX_FILE_BYTES // 1024} KiB")
                finally:
                    response.close()
        except httpx.HTTPError as exc:
            raise DeclarationError(f"unreachable: {type(exc).__name__}") from exc
        return parse_file(body)


def public(row: dict[str, Any]) -> dict[str, Any]:
    """A stored declaration as the API and /check show it."""
    return {k: v for k, v in (
        ("toolSetDigest", row["toolset"]),
        ("toolCount", row["tool_count"]),
        ("when", row["when_text"]),
        ("source", row.get("source") or "registry"),
        ("url", row.get("url") if (row.get("source") == "url") else None),
        ("registryVersion", row["registry_version"]),
        ("firstDeclared", row["first_declared"]),
        ("lastDeclared", row["last_declared"]),
        ("withdrawn", row["withdrawn_at"]),
    ) if v is not None}
