"""robots.txt for the crawler, as RFC 9309 says.

HISTOR is a crawler: it reads registry-listed MCP servers on its own schedule and publishes what it read.
Before a server is read, its origin's robots.txt is asked whether the endpoint's path is open to HISTOR's
product token ``histor`` (the group naming it, else ``*``). What a site does not allow, HISTOR does not
read; the history it already holds stays, and reading resumes by itself once the site allows it.

The rules followed (RFC 9309):

* Groups (2.1, 2.2.1): one or more ``user-agent`` lines, then rules. The group whose user-agent is the
  product token, matched case-insensitively, is used; several such groups are combined; with none, the
  ``*`` groups; with neither, everything is allowed.
* Matching (2.2.2, 2.2.3): the path with its query, percent-encoding normalised on both sides; ``*`` is
  any sequence and a final ``$`` the end. The match with the most octets decides; Allow wins a tie.
  ``/robots.txt`` itself is always allowed.
* Access results (2.3.1): 2xx is the file (the first 512 KiB parsed; at least 500 KiB is required);
  up to five redirects are followed, more means "unavailable"; 4xx is "unavailable": everything allowed;
  5xx or no answer is "unreachable": everything disallowed.
* Caching (2.4): a file is used for at most 24 h, except while its origin is unreachable, when the copy
  last read keeps applying. An origin unreachable for 30 days in a row with no copy is treated as
  unavailable (2.3.1.4 allows it).
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlsplit

import httpx

from histor.netguard import BlockedAddress, resolve_public

AGENT = "histor"
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 5
FRESH_S = 24 * 3600            # 2.4: SHOULD NOT use a cached file for more than 24 hours
RETRY_UNREACHABLE_S = 3600     # how soon an unreachable origin is asked again
GIVE_UP_UNREACHABLE_S = 30 * 24 * 3600  # 2.3.1.4: "for example, 30 days"

OK, UNAVAILABLE, UNREACHABLE = "ok", "unavailable", "unreachable"
DISALLOWED, UNREACHABLE_STATUS = "robots-disallowed", "robots-unreachable"


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


# --- the file ----------------------------------------------------------------------------------------------

_KEEP = "/?=&;:@!$'()*+,~-._%#[]"


def _normalise(value: str) -> str:
    """2.2.2: compare percent-encoded forms. Non-ASCII goes to UTF-8 escapes, escapes to upper case."""
    encoded = quote(value, safe=_KEEP)
    return re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group(0).upper(), encoded)


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str  # normalised

    def matches(self, path: str) -> bool:
        anchored = self.pattern.endswith("$")
        body = self.pattern[:-1] if anchored else self.pattern
        regex = ".*".join(re.escape(part) for part in body.split("*")) + ("$" if anchored else "")
        return re.match(regex, path) is not None

    @property
    def text(self) -> str:
        return f"{'Allow' if self.allow else 'Disallow'}: {self.pattern}"


def _names_agent(value: str, agent: str) -> bool:
    # The product token, case-insensitively; a value written like a User-Agent ("histor/0.5") names it too.
    token = value.strip().split("/", 1)[0].strip().lower()
    return token == agent.lower()


def rules_for(text: str, agent: str = AGENT) -> tuple[list[Rule], str]:
    """The rules that apply to *agent*, and which group they came from ("histor", "*" or "")."""
    groups: list[tuple[list[str], list[Rule]]] = []
    agents: list[str] = []
    rules: list[Rule] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value)
        elif key in ("allow", "disallow"):
            if not agents:
                continue  # a rule before any user-agent line belongs to no group
            in_rules = True
            if value:  # an empty value is no rule
                rules.append(Rule(allow=key == "allow", pattern=_normalise(value)))
    if agents:
        groups.append((agents, rules))
    named = [r for names, group in groups if any(_names_agent(n, agent) for n in names) for r in group]
    if any(any(_names_agent(n, agent) for n in names) for names, _ in groups):
        return named, agent
    star = [r for names, group in groups if any(n.strip() == "*" for n in names) for r in group]
    if any(any(n.strip() == "*" for n in names) for names, _ in groups):
        return star, "*"
    return [], ""


def decide_path(text: str, url: str, agent: str = AGENT) -> tuple[bool, Rule | None, str]:
    """Is the path of *url* allowed by *text*? Also the deciding rule and its group."""
    parts = urlsplit(url)
    path = _normalise((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
    if path == "/robots.txt":
        return True, None, ""
    rules, group = rules_for(text, agent)
    best: Rule | None = None
    for rule in rules:
        if not rule.matches(path):
            continue
        length = len(rule.pattern.encode())
        if best is None or length > len(best.pattern.encode()) or (length == len(best.pattern.encode()) and rule.allow):
            best = rule
    return (best is None or best.allow), best, group


# --- fetching --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fetched:
    kind: str                 # ok | unavailable | unreachable
    http: int | None = None
    body: str | None = None   # for ok: the first MAX_BYTES, as UTF-8
    note: str = ""


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port
    host = (parts.hostname or "").lower()
    default = (scheme == "https" and port in (None, 443)) or (scheme == "http" and port in (None, 80))
    netloc = f"[{host}]" if ":" in host else host
    return f"{scheme}://{netloc}" if default else f"{scheme}://{netloc}:{port}"


class Fetcher:
    """GET <origin>/robots.txt through the same address checks as every other request HISTOR makes."""

    def __init__(self, *, user_agent: str, timeout: float = 10.0, allow_private: bool = False,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.allow_private = allow_private
        self.transport = transport

    def __call__(self, origin: str) -> Fetched:
        url = f"{origin}/robots.txt"
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
                              follow_redirects=False, transport=self.transport,
                              # identity: a small text file, and no compressed body to inflate past the cap
                              headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"}) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    pinned = resolve_public(url, allow_private=self.allow_private)
                    extensions = {"sni_hostname": pinned.sni_hostname} if pinned.sni_hostname else {}
                    request = client.build_request("GET", pinned.url, headers={"Host": pinned.host_header},
                                                   extensions=extensions)
                    response = client.send(request, stream=True)
                    try:
                        code = response.status_code
                        if 300 <= code < 400 and response.headers.get("location"):
                            url = str(httpx.URL(url).join(response.headers["location"]))
                            continue
                        if 200 <= code < 300:
                            body = bytearray()
                            # Built in memory (the test transports): already read, as in mcpclient._decoded.
                            source = [response.content] if hasattr(response, "_content") else response.iter_raw()
                            for chunk in source:
                                body.extend(chunk)
                                if len(body) >= MAX_BYTES:
                                    break
                            text = bytes(body[:MAX_BYTES]).decode("utf-8", "replace").replace("\x00", "")
                            return Fetched(OK, code, text)
                        if 400 <= code < 500:
                            return Fetched(UNAVAILABLE, code)
                        if code >= 500:
                            return Fetched(UNREACHABLE, code)
                        return Fetched(UNREACHABLE, code, note=f"HTTP {code}")
                    finally:
                        response.close()
                return Fetched(UNAVAILABLE, None, note=f"more than {MAX_REDIRECTS} redirects")
        except BlockedAddress as exc:
            return Fetched(UNREACHABLE, None, note=f"not fetched: {exc}")
        except httpx.TimeoutException:
            return Fetched(UNREACHABLE, None, note="timeout")
        except httpx.HTTPError as exc:
            return Fetched(UNREACHABLE, None, note=type(exc).__name__)


# --- the decision ----------------------------------------------------------------------------------------

@dataclass
class State:
    """What HISTOR knows about one origin's robots.txt (one row of ``robots_txt``)."""

    origin: str
    checked_at: str
    kind: str
    http: int | None = None
    body: str | None = None       # the last file read (kept while the origin is unreachable)
    digest: str | None = None
    read_at: str | None = None    # when that file was read
    unreachable_since: str | None = None
    note: str = ""

    def row(self) -> dict[str, Any]:
        return {"origin": self.origin, "checked_at": self.checked_at, "kind": self.kind, "http": self.http,
                "body": self.body, "digest": self.digest, "read_at": self.read_at,
                "unreachable_since": self.unreachable_since, "note": self.note}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: str = ""   # robots-disallowed | robots-unreachable when not allowed
    detail: str = ""


@dataclass
class Policy:
    """Thread-safe: crawler workers ask it; the crawler loads it once and saves what changed."""

    fetch: Callable[[str], Fetched]
    clock: Callable[[], str] = _now
    agent: str = AGENT
    _states: dict[str, State] = field(default_factory=dict)
    _dirty: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _fetching: dict[str, threading.Lock] = field(default_factory=dict)
    loaded: bool = False

    def load(self, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            for row in rows:
                self._states.setdefault(row["origin"], State(**{k: row.get(k) for k in State.__dataclass_fields__}))
            self.loaded = True

    def take_dirty(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [self._states[o].row() for o in sorted(self._dirty)]
            self._dirty.clear()
        return rows

    def _stale(self, state: State, now: datetime) -> bool:
        age = (now - _parse_iso(state.checked_at)).total_seconds()
        return age >= (RETRY_UNREACHABLE_S if state.kind == UNREACHABLE else FRESH_S)

    def _refresh(self, origin: str) -> State:
        with self._lock:
            gate = self._fetching.setdefault(origin, threading.Lock())
        with gate:
            now = _parse_iso(self.clock())
            with self._lock:
                state = self._states.get(origin)
            if state is not None and not self._stale(state, now):
                return state
            got = self.fetch(origin)
            at = _iso(now)
            if got.kind == OK:
                body = got.body or ""
                state = State(origin, at, OK, got.http, body, hashlib.sha256(body.encode()).hexdigest(), at, None, got.note)
            elif got.kind == UNAVAILABLE:
                state = State(origin, at, UNAVAILABLE, got.http, None, None, at, None, got.note)
            else:
                prior = state
                since = prior.unreachable_since if prior and prior.kind == UNREACHABLE and prior.unreachable_since else at
                # Keep the last file read (a 2xx; a 4xx is no file): while the origin is unreachable it
                # still applies (2.4).
                state = State(origin, at, UNREACHABLE, got.http,
                              prior.body if prior else None, prior.digest if prior else None,
                              prior.read_at if prior and prior.body is not None else None, since, got.note)
            with self._lock:
                self._states[origin] = state
                self._dirty.add(origin)
            return state

    def decide(self, url: str) -> Decision:
        origin = origin_of(url)
        state = self._refresh(origin)
        path = urlsplit(url).path or "/"
        if state.kind == UNREACHABLE:
            since = _parse_iso(state.unreachable_since or state.checked_at)
            waited = (_parse_iso(self.clock()) - since).total_seconds()
            if waited >= GIVE_UP_UNREACHABLE_S:
                return Decision(True)  # 2.3.1.4: after a long time, MAY assume unavailable
            if state.body is not None:
                return self._judge(state, url, path, stale_note=True)
            what = f"answered HTTP {state.http}" if state.http else f"gave no answer ({state.note or 'unreachable'})"
            until = _iso(since + timedelta(seconds=GIVE_UP_UNREACHABLE_S))
            return Decision(False, UNREACHABLE_STATUS,
                            f"{origin}/robots.txt {what} since {_iso(since)}; RFC 9309 says to assume everything is "
                            f"disallowed, so HISTOR does not read {path}. Unless it answers, HISTOR treats it as absent "
                            f"from {until}.")
        if state.kind == UNAVAILABLE:
            return Decision(True)
        return self._judge(state, url, path)

    def _judge(self, state: State, url: str, path: str, stale_note: bool = False) -> Decision:
        allowed, rule, group = decide_path(state.body or "", url, self.agent)
        if allowed:
            return Decision(True)
        who = "User-agent: histor" if group == self.agent else "User-agent: *"
        read = f" (read {state.read_at}; the site has not answered since)" if stale_note else ""
        return Decision(False, DISALLOWED,
                        f"{state.origin}/robots.txt{read} disallows {path} for HISTOR: '{rule.text if rule else ''}' "
                        f"in the '{who}' group (RFC 9309). To let HISTOR read it, add 'User-agent: histor' "
                        f"with 'Allow: {path}'.")
