"""npm and PyPI MCP servers, observed in the package sandbox.

A stdio server is a package the user's own client starts on the user's own machine, so HISTOR
cannot dial it. The sandbox host (``histor/sandbox/``) installs the package and starts it under
gVisor with fake secrets and no external network. It reads ``initialize`` / ``tools/list``
and exercises a bounded subset of tools with canary arguments. Remote endpoints are never called. This module asks it to, over HTTPS pinned to the sandbox's certificate.

What is observed is a *version*. A published npm or PyPI version cannot be changed, so a package
is observed again only when a new version appears, and a new version of a package HISTOR already
knows is observed first: that is where a quiet change to what the tools tell the model shows up,
for every client that runs ``npx -y package`` without a pinned version.

Targets are ``npm:<name>`` and ``pypi:<normalised name>`` in the endpoint column. PyPI names are
normalised as PEP 503 says, so ``Mcp_Server.Fetch`` and ``mcp-server-fetch`` are one package.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
import threading
import time
import urllib.parse
from typing import Any

import httpx

from histor.mcpclient import USER_AGENT, Observation

REGISTRIES = ("npm", "pypi")
INSTALL_SCRIPTS = ("preinstall", "install", "postinstall")
NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]{0,100}/)?[a-z0-9][a-z0-9._~-]{0,100}$")
PYPI_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,126}[a-z0-9])?$")
# What the sandbox reports about OUR side, not the package's: never a label about the package.
SANDBOX_FAULTS = {"busy", "sandbox-unavailable", "refused", "unauthorised", "not-found-route"}
SANDBOX_ERROR = "sandbox-error"


def pypi_normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_key(registry: str, name: str) -> str | None:
    """``npm:<name>`` / ``pypi:<name>``, or None for a name the sandbox would refuse."""
    if registry == "npm":
        return f"npm:{name}" if NPM_NAME.match(name) else None
    if registry == "pypi":
        norm = pypi_normalise(name)
        return f"pypi:{norm}" if PYPI_NAME.match(norm) else None
    return None


def parse_package(key: str) -> tuple[str, str] | None:
    registry, sep, name = key.partition(":")
    if not sep or registry not in REGISTRIES or package_key(registry, name) != key:
        return None
    return registry, name


class PackageReader:
    """Latest versions from the registries (cheap, every crawl) and observations from the sandbox."""

    # Registry lookups per second, per registry. npm answers 429 with Retry-After ≈ 10 minutes
    # after a few hundred requests in a burst, and a 429 read as "no version" once dropped
    # every npm package from a crawl; paced, the per-crawl lookups stay under it.
    REGISTRY_RATE = {"npm": 8.0, "pypi": 20.0}
    # How long one lookup may wait out a 429 before the package is skipped for this crawl.
    RATE_LIMIT_WAIT_S = 660.0

    def __init__(self, url: str, token: str, cert_sha256: str, *, timeout: float = 420.0,
                 registry_timeout: float = 20.0, transport: httpx.BaseTransport | None = None,
                 sleep: Any = time.sleep, clock: Any = time.monotonic) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("the sandbox URL must be https://host:port")
        self.host = parts.hostname
        self.port = parts.port or 443
        self.token = token
        self.pin = cert_sha256.replace(":", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", self.pin):
            raise ValueError("the sandbox certificate pin must be a SHA-256 fingerprint")
        self.timeout = timeout
        self.registry = httpx.Client(timeout=registry_timeout, transport=transport, follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._sleep, self._clock = sleep, clock
        self._pace = threading.Lock()
        self._next_at = {r: 0.0 for r in REGISTRIES}
        self._blocked_until = {r: 0.0 for r in REGISTRIES}

    def close(self) -> None:
        self.registry.close()

    def _registry_get(self, registry: str, url: str) -> httpx.Response:
        """One registry request, paced per registry; a 429 is waited out (all threads), not read as an answer."""
        deadline = self._clock() + self.RATE_LIMIT_WAIT_S
        while True:
            with self._pace:
                now = self._clock()
                at = max(now, self._next_at[registry], self._blocked_until[registry])
                self._next_at[registry] = at + 1.0 / self.REGISTRY_RATE[registry]
            if at > now:
                self._sleep(at - now)
            res = self.registry.get(url)
            if res.status_code != 429:
                return res
            try:
                wait = float(res.headers.get("retry-after") or 60)
            except ValueError:
                wait = 60.0
            wait = min(max(wait, 1.0), self.RATE_LIMIT_WAIT_S)
            with self._pace:
                self._blocked_until[registry] = max(self._blocked_until[registry], self._clock() + wait)
                if self._blocked_until[registry] > deadline:
                    return res

    def latest_version(self, registry: str, name: str) -> str | None:
        """The version ``npx -y`` / ``uvx`` would run today, or None when the registry does not say."""
        if registry == "npm":
            res = self._registry_get(registry, f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@/')}/latest")
        else:
            res = self._registry_get(registry, f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
        if res.status_code != 200:
            return None
        doc = res.json()
        version = doc.get("version") if registry == "npm" else (doc.get("info") or {}).get("version")
        return version if isinstance(version, str) and 0 < len(version) <= 64 else None

    def version_facts(self, registry: str, name: str, version: str) -> dict[str, Any] | None:
        """What the registry says about one published version: whether its build is attested, who
        published it, the install scripts it runs and the packages it depends on. Never an email."""
        if registry == "npm":
            res = self._registry_get(registry, f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@/')}/{urllib.parse.quote(version)}")
            if res.status_code != 200:
                return None
            m = res.json()
            user = m.get("_npmUser") if isinstance(m.get("_npmUser"), dict) else {}
            trusted = user.get("trustedPublisher") if isinstance(user.get("trustedPublisher"), dict) else None
            publisher = f"trusted publisher: {trusted.get('id')}" if trusted and trusted.get("id") else user.get("name")
            scripts = m.get("scripts") if isinstance(m.get("scripts"), dict) else {}
            dist = m.get("dist") if isinstance(m.get("dist"), dict) else {}
            return {
                "version": version,
                "provenance": bool((dist.get("attestations") or {}).get("provenance")) if isinstance(dist.get("attestations"), dict) else False,
                "publisher": str(publisher)[:100] if publisher else None,
                "installScripts": sorted(k for k in scripts if k in INSTALL_SCRIPTS),
                "provenanceVerified": None,  # registry presence is not cryptographic verification
                "dependencySpecs": {str(k): str(v) for k, v in (m.get("dependencies") or {}).items()},
                "dependencies": sorted(str(k)[:214] for k in (m.get("dependencies") or {}))[:300],
            }
        res = self._registry_get("pypi", f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json")
        if res.status_code != 200:
            return None
        doc = res.json()
        wheels = [u for u in doc.get("urls") or [] if u.get("packagetype") == "bdist_wheel"]
        wheel = next((w for w in wheels if str(w.get("filename", "")).endswith("-none-any.whl")), wheels[0] if wheels else None)
        attested = None
        if wheel:
            prov = self._registry_get("pypi", f"https://pypi.org/integrity/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/"
                                     f"{urllib.parse.quote(str(wheel.get('filename')))}/provenance")
            attested = True if prov.status_code == 200 else False if prov.status_code == 404 else None
        raw_requires = (doc.get("info") or {}).get("requires_dist") or []
        requires = [pypi_normalise(re.split(r"[\s;<>=!~\[(]", str(r), maxsplit=1)[0]) for r in raw_requires]
        dependency_specs: dict[str, list[str]] = {}
        for dependency, requirement in zip(requires, raw_requires, strict=True):
            dependency_specs.setdefault(dependency, []).append(str(requirement))
        return {"version": version, "provenance": attested, "publisher": None, "installScripts": [],
                "dependencies": sorted({r for r in requires if r})[:300], "provenanceVerified": None,
                "dependencySpecs": {k: sorted(v) for k, v in dependency_specs.items()}}

    def observe(self, registry: str, name: str, version: str | None, *, env: list[str] | None = None,
                args: list[str] | None = None, pins: list[str] | None = None, runtime: str | None = None) -> Observation:
        body: dict[str, Any] = {"registry": registry, "name": name}
        if version:
            body["version"] = version
        if env:
            body["env"] = list(env)
        if args:
            body["args"] = list(args)
        if pins:
            body["pins"] = list(pins)
        if runtime:
            body["runtime"] = runtime
        try:
            code, doc = self._post("/observe", body)
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
            return Observation(status=SANDBOX_ERROR, detail=f"sandbox unreachable ({type(exc).__name__})")
        status = doc.get("status") if isinstance(doc, dict) else None
        if code != 200 or not isinstance(status, str) or status in SANDBOX_FAULTS:
            return Observation(status=SANDBOX_ERROR, detail=f"sandbox answered {code} {status or ''}".strip())
        observed = doc.get("version") if isinstance(doc.get("version"), str) else (version or "")
        observer = str(doc.get("observer") or "")[:20]
        behaviour = doc.get("behaviour") if isinstance(doc.get("behaviour"), dict) else None
        conditions = {k: str(doc[k])[:200] for k in ("image", "imageTag", "runtime") if isinstance(doc.get(k), str) and doc[k]} or None
        given = doc.get("supplied") if isinstance(doc.get("supplied"), dict) else {}
        supplied = {k: [str(v)[:100] for v in given.get(k) or [] if isinstance(v, str)][:8] for k in ("env", "args", "pins")}
        if isinstance(given.get("runtime"), str) and given["runtime"]:
            supplied["runtime"] = given["runtime"][:20]  # type: ignore[assignment]
        supplied = {k: v for k, v in supplied.items() if v} or None
        extra = {"package_conditions": conditions, "package_supplied": supplied}
        if status != "ok":
            # The head says what failed, the tail where: both reach the exit-reason reader (stored
            # last_detail is still cut to 300).
            return Observation(status=status[:40], detail=str(doc.get("detail") or "")[:1300],
                               package_version=observed, package_observer=observer, behaviour=behaviour, **extra)
        tools = doc.get("tools")
        info = doc.get("serverInfo") if isinstance(doc.get("serverInfo"), dict) else {}
        return Observation(status="ok", tools=tools if isinstance(tools, list) else [], server_info=info,
                           protocol_version=str(doc.get("protocolVersion") or "")[:40], pages=1,
                           package_version=observed, package_observer=observer, behaviour=behaviour, **extra)

    def selftest(self) -> dict[str, Any]:
        """The host's own self-test (gVisor, tracing runtime, network, observer version)."""
        code, doc = self._request("GET", "/selftest", None)
        if code != 200 or not isinstance(doc, dict):
            raise ValueError(f"the sandbox answered {code}")
        return doc

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        # Pinned, not CA-verified: the sandbox's certificate is self-signed, and the fingerprint
        # is the whole of the trust — checked before a byte of the request (or the token) is sent.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=ctx)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True) if conn.sock else None
            if not der or hashlib.sha256(der).hexdigest() != self.pin:
                raise ssl.SSLError("the sandbox certificate does not match the pinned fingerprint")
            payload = json.dumps(body).encode() if body is not None else b""
            headers = {"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT}
            if body is not None:
                headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
            conn.request(method, path, body=payload or None, headers=headers)
            res = conn.getresponse()
            raw = res.read(32 * 1024 * 1024)
            return res.status, json.loads(raw) if raw else {}
        finally:
            conn.close()


def version_signals(current: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    """The facts of the version observed, and what changed since the version observed before it.

    The flags are the classic marks of a stolen publishing token: a package that was built and
    attested in CI suddenly is not, someone else publishes it, it starts running install
    scripts, or it pulls in a dependency it never had.
    """
    if not current:
        return None
    out: dict[str, Any] = {k: current[k] for k in ("version", "provenance", "publisher", "installScripts")}
    out["provenanceVerified"] = current.get("provenanceVerified")
    out["metadataComplete"] = current.get("provenance") is not None
    flags: list[str] = []
    if previous and previous.get("version") != current.get("version"):
        out["previousVersion"] = previous.get("version")
        if previous.get("provenance") is True and current.get("provenance") is False:
            flags.append("provenance-lost")
        if previous.get("publisher") and current.get("publisher") and previous["publisher"] != current["publisher"]:
            flags.append("publisher-changed")
            out["previousPublisher"] = previous["publisher"]
        old_specs, new_specs = previous.get("dependencySpecs") or {}, current.get("dependencySpecs") or {}
        changed = sorted(k for k in old_specs.keys() & new_specs.keys() if old_specs[k] != new_specs[k])
        if changed:
            flags.append("dependency-specs-changed")
            out["changedDependencies"] = changed[:20]
        added_scripts = sorted(set(current.get("installScripts") or []) - set(previous.get("installScripts") or []))
        if added_scripts:
            flags.append("install-scripts-added")
        new_deps = sorted(set(current.get("dependencies") or []) - set(previous.get("dependencies") or []))
        if new_deps:
            flags.append("new-dependencies")
            out["newDependencies"] = new_deps[:20]
    if current.get("installScripts") and "install-scripts-added" not in flags:
        flags.append("install-scripts")
    out["flags"] = flags
    return out


class SandboxPool:
    """Several sandbox hosts behind the interface of one PackageReader.

    Registry questions (latest version, version facts) go to the first reader: they are HTTP calls
    to npm and PyPI, not sandbox work. An observation goes to a host with a free slot, so a faster
    host simply takes more packages; if the chosen host fails as infrastructure (SANDBOX_ERROR),
    the package is tried once on another host before the failure is recorded as ours.
    """

    def __init__(self, members: list[tuple[PackageReader, int]], *, max_slots: int = 12) -> None:
        if not members:
            raise ValueError("a sandbox pool needs at least one host")
        self.members = [(reader, threading.BoundedSemaphore(max(1, slots))) for reader, slots in members]
        # The crawl admits at most this many observations at once, whatever the hosts could take.
        self.capacity = min(sum(max(1, slots) for _, slots in members), max_slots)
        self.lock = threading.Lock()
        self.next = 0
        # A host that failed as infrastructure is passed over for a while, so a dead or hung host
        # does not take every other package's first attempt; it is used again when nothing else is free.
        self.resting_until = [0.0] * len(self.members)
        self.counts = [{"observed": 0, "errors": 0} for _ in self.members]

    def close(self) -> None:
        for reader, _ in self.members:
            reader.close()

    def latest_version(self, registry: str, name: str) -> str | None:
        return self.members[0][0].latest_version(registry, name)

    def version_facts(self, registry: str, name: str, version: str) -> dict[str, Any] | None:
        return self.members[0][0].version_facts(registry, name, version)

    REST_S = 60.0

    def _take(self, skip: int | None = None) -> int:
        """Index of a host with a free slot, round-robin and resting hosts last; blocks until one frees."""
        while True:
            with self.lock:
                now = time.monotonic()
                order = [(self.next + i) % len(self.members) for i in range(len(self.members))]
                order.sort(key=lambda i: self.resting_until[i] > now)  # stable: healthy hosts first
                for i in order:
                    if i != skip and self.members[i][1].acquire(blocking=False):
                        self.next = (i + 1) % len(self.members)
                        return i
            time.sleep(0.2)

    def _run(self, i: int, registry: str, name: str, version: str | None, **config: Any) -> Observation:
        try:
            obs = self.members[i][0].observe(registry, name, version, **config)
        finally:
            self.members[i][1].release()
        with self.lock:
            failed = obs.status == SANDBOX_ERROR
            self.counts[i]["errors" if failed else "observed"] += 1
            self.resting_until[i] = time.monotonic() + self.REST_S if failed else 0.0
        return obs

    def observe(self, registry: str, name: str, version: str | None, **config: Any) -> Observation:
        first = self._take()
        obs = self._run(first, registry, name, version, **config)
        if obs.status != SANDBOX_ERROR or len(self.members) == 1:
            return obs
        return self._run(self._take(skip=first), registry, name, version, **config)

    def take_counts(self) -> dict[str, dict[str, int]]:
        """Per host, what it did since the last call; then start counting again. Hosts are named by
        their number in the configuration (1 = HISTOR_SANDBOX_URL, 2 = HISTOR_SANDBOX_2_URL, …),
        never by address: the run record should not carry an infrastructure IP."""
        with self.lock:
            out = {str(i + 1): dict(c) for i, c in enumerate(self.counts)}
            self.counts = [{"observed": 0, "errors": 0} for _ in self.members]
            return out


def sandbox_readers(settings: Any) -> list[tuple[PackageReader, int]]:
    """Every configured sandbox host with its slots, in configuration order (1, 2, …)."""
    first = PackageReader(settings.sandbox_url, settings.sandbox_token, settings.sandbox_cert_sha256,
                          timeout=settings.sandbox_timeout_s)
    return [(first, settings.sandbox_concurrency)] + [
        (PackageReader(url, token, pin, timeout=settings.sandbox_timeout_s), slots)
        for url, token, pin, slots in settings.sandbox_extra]


def check_sandboxes(settings: Any) -> list[dict[str, Any]]:
    """``histor sandboxes``: each host's self-test over the pinned channel. Addresses, never tokens."""
    out = []
    for n, (reader, slots) in enumerate(sandbox_readers(settings), start=1):
        row: dict[str, Any] = {"host": n, "address": f"{reader.host}:{reader.port}", "slots": slots}
        try:
            doc = reader.selftest()
            row.update(observer=doc.get("observer"), gvisor=doc.get("gvisor") is True,
                       trace=doc.get("trace") is True, observeNetwork=doc.get("observeNetwork") is True)
            row["ok"] = bool(row["gvisor"] and row["trace"] and row["observeNetwork"]
                             and row["observer"] == settings.sandbox_observer)
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
            row.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
        finally:
            reader.close()
        out.append(row)
    return out
