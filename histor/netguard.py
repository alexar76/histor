"""Where the crawler is allowed to connect, decided once per request and then pinned.

Every endpoint HISTOR dials comes from third-party data — a registry entry anyone can publish,
or a URL a client pasted into /check. Without a guard, one entry pointing at ``127.0.0.1:9083``
or a cloud metadata address turns the crawler into a way to POST JSON-RPC at the host's own
loopback services. So:

* the host name is resolved here, and **every** address it resolves to must be global — one
  private answer in a round-robin set is enough to refuse;
* the connection then goes to the address that was checked, not to a second lookup. The URL is
  rewritten to the IP, ``Host`` carries the original name, and TLS gets it as SNI, so the
  certificate is still verified against the name. A resolver that answers differently a
  millisecond later (DNS rebinding) never gets asked.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class BlockedAddress(ValueError):
    """The endpoint resolves somewhere the crawler must not go."""


@dataclass(frozen=True)
class PinnedTarget:
    url: str  # the URL with the host replaced by the checked address
    host_header: str
    sni_hostname: str | None
    address: str


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


def resolve_public(url: str, *, allow_private: bool = False, resolver=socket.getaddrinfo) -> PinnedTarget:
    """Check *url* and return where to actually connect. Raises :class:`BlockedAddress`.

    ``allow_private`` exists for the test suite's loopback fixture server and for nothing else;
    the app never sets it from a request.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:  # port 99999, port "abc", an unclosed "[" — a registry typo or worse
        raise BlockedAddress(f"endpoint URL does not parse: {exc}") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http"):
        raise BlockedAddress(f"scheme {scheme!r} is not dialled")
    if scheme == "http" and not allow_private:
        # Tool definitions over cleartext can be rewritten by anyone on the path, so an
        # observation of them would not be an observation of the server.
        raise BlockedAddress("cleartext http endpoints are not observed")
    host = parts.hostname
    if not host:
        raise BlockedAddress("endpoint has no host")
    if parts.username or parts.password:
        raise BlockedAddress("endpoint carries credentials")
    port = port or (443 if scheme == "https" else 80)
    try:
        # Host header and SNI are ASCII on the wire: an internationalised name goes as its IDNA
        # (punycode) form, exactly as a browser would send it.
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise BlockedAddress(f"host {host!r} is not a valid domain name") from exc

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            infos = resolver(ascii_host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise BlockedAddress(f"cannot resolve {host}: {exc}") from exc
        addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise BlockedAddress(f"{host} resolved to nothing")
    if not allow_private:
        bad = [a for a in addresses if not _is_public(a)]
        if bad:
            raise BlockedAddress(f"{host} resolves to a non-public address")

    # Prefer IPv4: most crawler hosts have no IPv6 route and the failure would read as the
    # server's, not ours.
    chosen = next((a for a in addresses if ":" not in a), addresses[0])
    netloc_host = f"[{chosen}]" if ":" in chosen else chosen
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = netloc_host if default_port else f"{netloc_host}:{port}"
    host_name = f"[{host}]" if literal is not None and ":" in host else ascii_host
    host_header = host_name if default_port else f"{host_name}:{port}"
    return PinnedTarget(
        url=urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")),
        host_header=host_header,
        sni_hostname=ascii_host if scheme == "https" and literal is None else None,
        address=chosen,
    )
