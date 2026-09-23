"""Harvest the official MCP registry into observation targets.

One target per (server name, streamable-http remote). Remotes HISTOR does not dial are still
recorded, with the reason, so the public numbers account for every listed endpoint instead of
quietly shrinking the denominator.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from histor.mcpclient import USER_AGENT
from histor.subject import REGISTRY_OFFICIAL
from histor.untrusted import clean_text

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
    """An entry is a URL prefix (``https://host/path``) or a host, which also covers its subdomains."""
    if not entries:
        return False
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
                         opt_out: tuple[str, ...] = ()) -> list[Target]:
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
    return out


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
            retry_delay: float = 2.0, opt_out: tuple[str, ...] = ()) -> list[Target]:
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
    return targets_from_servers(servers, allow_cleartext=allow_cleartext, opt_out=opt_out)
