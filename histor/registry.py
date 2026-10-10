"""Harvest the official MCP registry into observation targets.

One target per (server name, streamable-http remote), and one per npm or PyPI package a server
ships (``npm:<name>``, ``pypi:<name>``; observed in the package sandbox, histor.packages).
Remotes and packages HISTOR does not observe are still recorded, with the reason, so the public
numbers account for every listed endpoint instead of quietly shrinking the denominator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from histor.mcpclient import USER_AGENT
from histor.packages import REGISTRIES, package_key, parse_package
from histor.subject import REGISTRY_CURATED, REGISTRY_OFFICIAL
from histor.untrusted import clean_text

CURATED_PATH = Path(__file__).with_name("curated.json")

# Not a size limit: the registry may grow as it likes (the first production harvest was ~355 pages
# of 100). A pagination that loops is caught by the seen-cursor check in harvest(); this only bounds
# one that keeps inventing new cursors forever. The practical ceiling is memory — a harvested record
# holds ~2.6 KB, so the 768 MB container fits roughly 100 000 servers.
MAX_PAGES = 1_000_000
log = logging.getLogger("histor.registry")
MAX_REMOTES_PER_SERVER = 3


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    endpoint: str
    transport: str
    registry: str
    title: str | None
    description: str | None
    version: str | None
    repository: str | None
    website: str | None
    skip_reason: str | None  # set when HISTOR will not dial this endpoint


def target_id(name: str, endpoint: str) -> str:
    return hashlib.sha256(f"{name}\n{endpoint}".encode()).hexdigest()[:16]


def _str(value: Any, limit: int = 2000) -> str | None:
    # Registry text is anyone's: NUL and unpaired surrogates would fail the SQL insert and take
    # the whole crawl down with them.
    return clean_text(value, limit) or None if isinstance(value, str) and value else None


OPT_OUT = "operator-opt-out"


def opted_out(url: str, entries: tuple[str, ...]) -> bool:
    """An entry is a URL prefix (``https://host/path``), a host, which also covers its subdomains,
    or a package (``npm:@scope/name``, ``pypi:name``)."""
    if not entries:
        return False
    if url.startswith(tuple(f"{r}:" for r in REGISTRIES)):
        return url in entries
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    for entry in entries:
        if "://" in entry:
            if url.startswith(entry):
                return True
        elif host and (host == entry or host.endswith("." + entry)):
            return True
    return False


def load_opt_out(env_value: str, path: Path | None) -> tuple[str, ...]:
    """``HISTOR_OPT_OUT`` (comma-separated) plus ``opt-out.txt`` on the data volume, re-read per crawl."""
    entries = [e.strip().lower() if "://" not in e else e.strip() for e in env_value.split(",")]
    if path is not None and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                entries.append(line if "://" in line else line.lower())
    return tuple(e for e in entries if e)


def targets_from_servers(servers: list[dict[str, Any]], *, allow_cleartext: bool = False,
                         opt_out: tuple[str, ...] = (), observe_packages: bool = False) -> list[Target]:
    """Latest record per name → targets. Pure, so the selection rules are testable offline.

    ``allow_cleartext`` exists for a loopback test registry, the same switch that lets the
    crawler dial private addresses — and like it, refused under ``HISTOR_PROFILE=prod``.
    """
    latest: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for item in servers:
        srv = item.get("server") if isinstance(item.get("server"), dict) else item
        if not isinstance(srv, dict) or not isinstance(srv.get("name"), str) or not srv["name"].strip():
            continue
        meta = (item.get("_meta") or {}).get("io.modelcontextprotocol.registry/official", {}) or {}
        if meta.get("status") == "deleted":
            continue
        prev = latest.get(srv["name"])
        if prev is None or (meta.get("isLatest") and not prev[1].get("isLatest")) or (
            str(meta.get("publishedAt", "")) > str(prev[1].get("publishedAt", "")) and not prev[1].get("isLatest")
        ):
            latest[clean_text(srv["name"], 300)] = (srv, meta)

    out: list[Target] = []
    packages_seen: set[str] = set()
    for name, (srv, _meta) in sorted(latest.items()):
        remotes = [r for r in (srv.get("remotes") or []) if isinstance(r, dict) and isinstance(r.get("url"), str)]
        repo = srv.get("repository") if isinstance(srv.get("repository"), dict) else {}
        for remote in remotes[:MAX_REMOTES_PER_SERVER]:
            url = clean_text(remote["url"].strip(), 2000)
            transport = clean_text(remote.get("type") or "", 40)
            skip = None
            if transport != "streamable-http":
                skip = "transport-not-observed"  # sse: legacy transport, not dialled in v0.1
            elif "{" in url or "}" in url:
                skip = "templated-url"  # needs per-user values we do not have
            elif not url.startswith("https://") and not allow_cleartext:
                skip = "cleartext-url"
            if opted_out(url, opt_out):
                # Asked to be left out: still listed, with the reason, never silently dropped.
                skip = OPT_OUT
            out.append(Target(
                id=target_id(name, url),
                name=name,
                endpoint=url,
                transport=transport or "unknown",
                registry=REGISTRY_OFFICIAL,
                title=_str(srv.get("title"), 200),
                description=_str(srv.get("description")),
                version=_str(srv.get("version"), 100),
                repository=_str(repo.get("url"), 500),
                website=_str(srv.get("websiteUrl"), 500),
                skip_reason=skip,
            ))
        kinds: set[str] = set()
        for pkg in srv.get("packages") or []:
            if not isinstance(pkg, dict):
                continue
            kind = str(pkg.get("registryType") or pkg.get("registry_type") or pkg.get("registry_name") or "").lower()
            ident = pkg.get("identifier") or pkg.get("name")
            if not isinstance(ident, str) or not ident.strip() or kind in kinds:
                continue  # one package per registry type per server
            kinds.add(kind)
            ptransport = pkg.get("transport") if isinstance(pkg.get("transport"), dict) else {}
            ptransport = clean_text(str(ptransport.get("type") or "stdio"), 40)
            key = package_key(kind, ident.strip()) if kind in REGISTRIES else None
            endpoint = key or clean_text(f"{kind or 'unknown'}:{ident.strip()}", 300)
            skip = None
            if kind not in REGISTRIES:
                skip = "package-type-not-observed"  # oci, mcpb, nuget: no sandbox runner for them yet
            elif key is None:
                skip = "bad-package-name"
            elif ptransport != "stdio":
                skip = "transport-not-observed"
            elif endpoint in packages_seen:
                skip = "package-observed-under-another-name"  # the same package listed twice
            elif not observe_packages:
                skip = "sandbox-not-configured"
            if opted_out(endpoint, opt_out):
                skip = OPT_OUT
            packages_seen.add(endpoint)
            out.append(Target(
                id=target_id(name, endpoint),
                name=name,
                endpoint=endpoint,
                transport=ptransport or "stdio",
                registry=REGISTRY_OFFICIAL,
                title=_str(srv.get("title"), 200),
                description=_str(srv.get("description")),
                version=_str(pkg.get("version"), 100),
                repository=_str(repo.get("url"), 500),
                website=_str(srv.get("websiteUrl"), 500),
                skip_reason=skip,
            ))
    return out


def curated_targets(path: Path = CURATED_PATH, *, opt_out: tuple[str, ...] = (),
                    observe_packages: bool = False) -> list[Target]:
    """HISTOR's own list of popular remote servers the official registry does not carry.

    Many servers people actually install never published to the registry, and a /check for them
    answered "not-listed" — as if nobody had ever seen them. The list ships with the code, so what
    HISTOR watches beyond the registry is public and reviewed like any other change. Its names are
    HISTOR's, in their own namespace (REGISTRY_CURATED): a label never claims a registry listing.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Target] = []
    seen: set[str] = set()
    for entry in data.get("servers") or []:
        name, url = entry.get("name"), entry.get("endpoint")
        if not isinstance(name, str) or not name.strip() or not isinstance(url, str) or url in seen:
            continue
        seen.add(url)
        if parse_package(url):
            transport = "stdio"
            skip = None if observe_packages else "sandbox-not-configured"
        else:
            transport = "streamable-http"
            skip = None if url.startswith("https://") else "cleartext-url"
        if opted_out(url, opt_out):
            skip = OPT_OUT
        out.append(Target(
            id=target_id(name, url),
            name=name,
            endpoint=url,
            transport=transport,
            registry=REGISTRY_CURATED,
            title=_str(entry.get("title"), 200),
            description=_str(entry.get("description")),
            version=None,
            repository=_str(entry.get("repository"), 500),
            website=_str(entry.get("website"), 500),
            skip_reason=skip,
        ))
    return out


def curated_rank(path: Path = CURATED_PATH) -> dict[str, int]:
    """Endpoint/package → its place in the curated list: the order never-observed packages run in."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for i, entry in enumerate(data.get("servers") or []):
        if isinstance(entry.get("endpoint"), str):
            out.setdefault(entry["endpoint"], i)
    return out


def with_curated(registry_targets: list[Target], curated: list[Target]) -> list[Target]:
    """The registry's targets plus the curated ones it does not already list.

    One endpoint keeps one record: when a curated server is published to the registry, the
    registry's listing replaces ours (our record is delisted at that crawl, its history kept).
    """
    listed = {t.endpoint for t in registry_targets}
    return registry_targets + [t for t in curated if t.endpoint not in listed]


def _get_page(client: httpx.Client, url: str, params: dict[str, Any], attempts: int = 5,
              delay: float = 2.0) -> dict[str, Any]:
    """One registry page, retried: the registry answers most pages in a second and some in fifty."""
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, params=params)
            if response.status_code < 500 and response.status_code != 429:
                response.raise_for_status()
                return response.json()
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == attempts:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 30.0)
    raise RuntimeError(f"registry page kept failing after {attempts} attempts")


def harvest(registry_url: str, *, timeout: float = 90.0, pause_s: float = 0.15,
            allow_cleartext: bool = False, transport: httpx.BaseTransport | None = None,
            retry_delay: float = 2.0, opt_out: tuple[str, ...] = (), observe_packages: bool = False) -> list[Target]:
    servers: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    with httpx.Client(timeout=timeout, transport=transport,
                      headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
        for pages in range(1, MAX_PAGES + 1):
            # version=latest: one record per server instead of every version ever published.
            params: dict[str, Any] = {"limit": 100, "version": "latest"}
            if cursor:
                params["cursor"] = cursor
            page = _get_page(client, f"{registry_url}/v0/servers", params, delay=retry_delay)
            servers.extend(s for s in (page.get("servers") or []) if isinstance(s, dict))
            nxt = (page.get("metadata") or {}).get("nextCursor")
            if nxt and (not isinstance(nxt, str) or nxt in seen or nxt == cursor):
                # Any cursor seen before — not only the previous one — is a loop (A → B → A …).
                raise RuntimeError("registry pagination cursor did not advance (a cursor repeated)")
            if nxt:
                seen.add(nxt)
            cursor = nxt
            if pages % 10 == 0:
                log.warning("registry harvest: %d pages, %d servers so far", pages, len(servers))
            if not cursor:
                break
            time.sleep(pause_s)
        else:
            raise RuntimeError(f"registry pagination did not end within {MAX_PAGES} pages")
    return targets_from_servers(servers, allow_cleartext=allow_cleartext, opt_out=opt_out,
                                observe_packages=observe_packages)
