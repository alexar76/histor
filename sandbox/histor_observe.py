#!/usr/bin/env python3
"""Observe one npm or PyPI MCP server package: install it, start it, read its tool definitions.

Runs on the sandbox host. HISTOR reaches it over HTTPS (``serve``): a self-signed certificate
HISTOR pins, a bearer token, and a firewall rule that admits only HISTOR's host. The only thing a
caller can ask for is one observation. The same requests work from a shell, or as the forced
command of an SSH key:

    observe npm @scope/name [version]
    observe pypi name [version]
    selftest
    serve                       # HTTPS on HISTOR_SANDBOX_LISTEN (default 0.0.0.0:9443)

Prints one JSON document on stdout and exits 0, whatever happened to the package — the status
field says what happened. Exit 2 is reserved for a malformed request.

The package is a stranger's code and some packages are hostile, so:

* Both stages run under gVisor (``--runtime=runsc``): the package talks to gVisor's kernel, not
  to the host's. The script refuses to run without it rather than fall back to plain runc.
* Install stage: network on (a bridge with inter-container traffic off), but no package code
  runs — npm with ``--ignore-scripts``, pip with ``--only-binary=:all:`` (a wheel is unpacked,
  never built). Packages that only ship an sdist are reported, not built.
* Run stage: an internal network with no route out, read-only root and package, no environment
  beyond PATH/HOME, an unprivileged uid, no capabilities, no new privileges, memory/CPU/PID
  limits. The server speaks MCP over the container's stdin/stdout to this script, which sends
  ``initialize`` and ``tools/list``, then calls up to CALL_LIMIT tools once each with canary
  arguments built from their schemas (an address on ``histor-trap.invalid``, a path to a decoy
  file) — inside the sandbox, against nothing real — and kills the container.
* Behaviour (when the tracing runtime is registered): the run is traced by gVisor itself
  (``runsc-trace``: execve, connect, open/openat), outside the sandbox, so the package cannot
  hide or forge it. The only resolver it can reach is this script's DNS logger on the internal
  network's gateway, which records each name and redirects to bounded HTTP/HTTPS/SMTP
  traps. Those traps never forward traffic and record payload recipient fields. HOME holds
  decoy credentials (SSH keys, cloud and registry tokens, a wallet, shell history) and the
  working directory a decoy ``.env``. The report says, for install scripts (npm, run in their
  own traced stage), startup and the tool calls: programs started, names looked up and
  addresses dialled, decoys opened, and writes that would persist outside the sandbox.
* ``--memory-swap`` equals ``--memory``: Docker's default doubles the limit with swap, and a
  900 MB allocation passed a 512 MB container on a host with swap until it was set.

Stdlib only: the sandbox host needs Python 3.10+, Docker and runsc, nothing from PyPI.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import selectors
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

VERSION = "3"  # explicit coverage, independent lifecycle and bounded canary calls
STATE = Path(os.environ.get("HISTOR_SANDBOX_STATE", "/var/lib/histor-sandbox"))
DOCKER = shutil.which("docker") or "/usr/bin/docker"
RUNTIME = "runsc"
FETCH_NETWORK = "histor-fetch"
IMAGES = {"npm": "node:22-alpine", "pypi": "python:3.12-slim"}
SLOTS = int(os.environ.get("HISTOR_SANDBOX_SLOTS", "2"))  # parallel observations on this host
SLOT_WAIT_S = 600
INSTALL_TIMEOUT_S = 240
RUN_TIMEOUT_S = 150            # from container start to the last canary call
SCRIPTS_TIMEOUT_S = 120        # npm install scripts, in their own traced stage
CALL_LIMIT = 15                # tools called with canary arguments, once each
CALL_TIMEOUT_S = 8
TRACE_RUNTIME = "runsc-trace"  # runsc with --strace and --debug-log=<STATE>/trace/%ID%/
TRACE_ROOT = STATE / "trace"
OBSERVE_NETWORK = "histor-observe"   # docker network create --internal (no route out)
OBSERVE_GATEWAY = os.environ.get("HISTOR_SANDBOX_OBSERVE_GATEWAY", "10.231.0.1")
HOME_IN = "/home/histor"
APP_IN = "/work/app"
INIT_TIMEOUT_S = 45
MAX_OUTPUT = 16 * 1024 * 1024  # bytes read from the server's stdout, all pages together
MAX_TOOLS = 4096
MAX_PAGES = 64
PROTOCOL = "2025-06-18"

NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]{0,100}/)?[a-z0-9][a-z0-9._~-]{0,100}$")
PYPI_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
UA = f"histor-sandbox/{VERSION} (+https://histor.modelmarket.dev)"


class Refused(Exception):
    """A malformed request: exit 2, nothing run."""


def out(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def tail(text: str, limit: int = 400) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text).strip()
    return text[-limit:]


def docker(*args: str, timeout: float = 60, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=timeout, check=check)


def http_json(url: str, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - fixed registry hosts
        return json.loads(res.read(8 * 1024 * 1024))


# -- resolution: which version, which artifact ------------------------------------------------

def resolve(registry: str, name: str, version: str | None) -> dict[str, Any]:
    if registry == "npm":
        path = urllib.parse.quote(name, safe="@/")
        meta = http_json(f"https://registry.npmjs.org/{path}/{urllib.parse.quote(version or 'latest')}")
        dist = meta.get("dist") or {}
        return {"version": meta["version"], "integrity": dist.get("integrity") or dist.get("shasum"),
                "published": None}
    meta = http_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version) + '/' if version else ''}json")
    files = meta.get("urls") or []
    wheels = [f for f in files if f.get("packagetype") == "bdist_wheel"]
    pick = next((w for w in wheels if w.get("filename", "").endswith("-none-any.whl")), wheels[0] if wheels else None)
    return {"version": meta["info"]["version"],
            "integrity": f"sha256:{pick['digests']['sha256']}" if pick else None,
            "published": (pick or {}).get("upload_time_iso_8601"),
            "has_wheel": bool(wheels)}


# -- decoys ---------------------------------------------------------------------------------------

def _canary(*parts: str) -> str:
    # Joined at run time: a literal key shape in this file would trip secret scanners.
    return "".join(parts)


def plant_decoys(work: Path, tag: str) -> None:
    """A HOME and a working directory that look worth stealing from. Every value is a canary."""
    home, app = work / "home", work / "app"
    files = {
        home / ".ssh" / "id_rsa": "\n".join([_canary("-----BEGIN OPENSSH ", "PRIVATE KEY-----"), f"histor-canary-ssh-{tag}",
                                             _canary("-----END OPENSSH ", "PRIVATE KEY-----"), ""]),
        home / ".ssh" / "id_ed25519": f"histor-canary-ed25519-{tag}\n",
        home / ".aws" / "credentials": "[default]\naws_access_key_id = " + _canary("AK", "IA", "HISTORCANARY", tag[:4].upper())
                                       + f"\naws_secret_access_key = histor-canary-aws-{tag}\n",
        home / ".npmrc": "//registry.npmjs.org/:_authToken=" + _canary("npm", "_", "historcanary", tag) + "\n",
        home / ".pypirc": f"[pypi]\nusername = __token__\npassword = histor-canary-pypi-{tag}\n",
        home / ".netrc": f"machine github.com login histor password histor-canary-netrc-{tag}\n",
        home / ".config" / "gh" / "hosts.yml": "github.com:\n    oauth_token: " + _canary("gh", "o_", "historcanary", tag) + "\n",
        home / ".docker" / "config.json": json.dumps({"auths": {"https://index.docker.io/v1/": {"auth": "aGlzdG9yOmNhbmFyeQ=="}}}),
        home / ".kube" / "config": f"apiVersion: v1\nkind: Config\nusers:\n- name: histor\n  user:\n    token: histor-canary-kube-{tag}\n",
        home / ".config" / "solana" / "id.json": json.dumps([7] * 64),
        home / ".bash_history": f"export OPENAI_API_KEY=histor-canary-openai-{tag}\ngit push\n",
        home / ".zsh_history": f": 1700000000:0;export STRIPE_SECRET=histor-canary-stripe-{tag}\n",
        home / "notes" / "canary.txt": f"histor canary note {tag}\n",
        app / ".env": f"OPENAI_API_KEY=histor-canary-openai-{tag}\nDATABASE_URL=postgres://histor:canary-{tag}@db.histor-trap.invalid/app\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if ".ssh" in path.parts or path.name in (".npmrc", ".pypirc", ".netrc", "credentials"):
            path.chmod(0o600)


# Paths (inside the sandbox) whose opening means the package went looking for credentials.
DECOYS = (".ssh/", ".aws/", ".npmrc", ".pypirc", ".netrc", ".config/gh/", ".docker/config.json", ".kube/",
          ".config/solana/", ".bash_history", ".zsh_history", ".gnupg/", ".env")
# Sockets the C library itself tries (name service caches): not the package's doing.
QUIET_SOCKETS = ("/var/run/nscd/socket", "/run/nscd/socket", "/var/run/dbus/system_bus_socket", "/run/systemd/userdb/io.systemd.DynamicUser")
# Writes that would make the package outlive the sandbox on a real machine.
PERSIST = (".bashrc", ".bash_profile", ".profile", ".zshrc", ".zprofile", ".ssh/authorized_keys", ".config/autostart/",
           "Library/LaunchAgents/", ".config/systemd/user/", ".npmrc", ".gitconfig")


# -- the DNS logger -------------------------------------------------------------------------------

class DnsLogger:
    """The only resolver the run stage can reach. It writes down every name asked for and answers
    with the local protocol trap gateway when traps are active. Without traps, it uses
    non-routable synthetic addresses and behavioural coverage cannot be complete."""

    def __init__(self, host: str, port: int = 53) -> None:
        import socket
        from collections import deque
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.records: deque[tuple[float, str, str]] = deque(maxlen=200_000)
        self.by_name: dict[str, str] = {}
        self.by_ip: dict[str, str] = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _address(self, name: str) -> str:
        with self.lock:
            if name not in self.by_name:
                if len(self.by_name) >= 120_000:
                    self.by_name.clear()
                    self.by_ip.clear()
                n = len(self.by_name) + 1
                ip = f"198.{18 + (n >> 16)}.{(n >> 8) & 255}.{n & 255}"
                self.by_name[name], self.by_ip[ip] = ip, name
            return self.by_name[name]

    def _loop(self) -> None:
        while True:
            try:
                data, (src, sport) = self.sock.recvfrom(4096)
                q = parse_dns_query(data)
                if q is None:
                    continue
                qid, name, qtype, question = q
                self.records.append((time.time(), src, name))
                ip = OBSERVE_GATEWAY if qtype == 1 and SINK else self._address(name) if qtype == 1 else None
                self.sock.sendto(dns_answer(qid, question, ip), (src, sport))
            except Exception:  # noqa: BLE001 - a malformed packet must not stop the logger
                continue

    def names(self, src: str | None, since: float, until: float) -> list[str]:
        return sorted({n for t, s, n in list(self.records) if (src is not None and s == src) and since <= t <= until})

    def name_for(self, ip: str) -> str | None:
        return self.by_ip.get(ip)


def parse_dns_query(data: bytes) -> tuple[bytes, str, int, bytes] | None:
    if len(data) < 17 or int.from_bytes(data[4:6], "big") < 1:
        return None
    pos, labels = 12, []
    while pos < len(data):
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 or len(labels) > 127:
            return None
        labels.append(data[pos + 1:pos + 1 + n].decode("ascii", "replace"))
        pos += 1 + n
    if pos + 4 > len(data):
        return None
    qtype = int.from_bytes(data[pos:pos + 2], "big")
    return data[:2], ".".join(labels).lower()[:253], qtype, data[12:pos + 4]


def dns_answer(qid: bytes, question: bytes, ip: str | None) -> bytes:
    header = qid + b"\x81\x80" + b"\x00\x01" + (b"\x00\x01" if ip else b"\x00\x00") + b"\x00\x00\x00\x00"
    if not ip:
        return header + question  # NOERROR, no record: AAAA and the rest fall back to A
    return header + question + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + bytes(int(x) for x in ip.split("."))


DNS: DnsLogger | None = None
SINK = None


# -- reading gVisor's trace -----------------------------------------------------------------------

_STAMP = re.compile(r"^I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{6})")
_PID = re.compile(r"\] \[\s*(\d+):\s*\d+\] ")
_EXEC = re.compile(r" X execve\(0x[0-9a-f]+ ([^,]+), 0x[0-9a-f]+ \[([^\]]*)\].*\) = 0 ")
_CONNECT = re.compile(r" X connect\(0x[0-9a-f]+ [^,]*, 0x[0-9a-f]+ \{Family: (AF_INET6?|AF_UNIX), Addr: ([^,}]+)(?:, Port: (\d+))?")
_OPEN = re.compile(r" X open(?:at)?\((?:[^,]*, )?0x[0-9a-f]+ (.+?), (O_[A-Z_|0-9]+(?:\|0x[0-9a-f]+)*)")
_SOCKET = re.compile(r" X socket\((AF_\w+), .*\) = (\d+) ")
_SOCK_FD = re.compile(r" X (?:connect|sendto|sendmsg|sendmmsg)\((0x[0-9a-f]+) socket:\[(\d+)\]")
_ADDRESS = re.compile(r"\{Family: (AF_\w+), Addr: ([^,}]*)(?:, Port: (\d+))?")


def _stamp(line: str, year: int) -> float | None:
    m = _STAMP.match(line)
    if not m:
        return None
    mo, d, h, mi, s, us = (int(x) for x in m.groups())
    try:
        return time.mktime((year, mo, d, h, mi, s, 0, 0, -1)) + us / 1e6
    except (OverflowError, ValueError):
        return None


def read_trace(container_id: str, phases: list[tuple[str, float, float]], entry: str, src_ip: str | None,
               drop_pid1: bool = False) -> dict[str, Any]:
    """Behaviour per phase from gVisor's own log of the container: written outside the sandbox.

    The entry exec chain is omitted, but PID 1 file/network activity remains evidence.
    Missing traces and unsupported records explicitly prevent a complete verdict."""
    year = time.localtime().tm_year
    acts: dict[str, dict[str, set[str]]] = {name: {"exec": set(), "network": set(), "decoys": set(), "writes": set()}
                                            for name, _, _ in phases}
    lookups: dict[str, list[str]] = {}
    trace_dir = TRACE_ROOT / container_id
    logs = sorted(trace_dir.glob("runsc.log.*.boot.txt"))
    issues = set()
    if not logs:
        issues.add("trace-missing")
    if not src_ip:
        issues.add("source-ip-missing")
    records = 0
    socket_families: dict[tuple[str, int], str] = {}
    socket_peers: dict[str, tuple[str, str, str | None]] = {}
    for log in logs:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if " X " not in line:
                    continue
                pm = _PID.search(line)
                pid1 = bool(pm and pm.group(1) == "1")
                at = _stamp(line, year)
                phase = next((n for n, a, b in phases if at is not None and a <= at <= b), None)
                if phase is None:
                    issues.add("unattributed-trace-record")
                    continue
                records += 1
                a = acts[phase]
                if (m := _SOCKET.search(line)):
                    if pm:
                        socket_families[(pm.group(1), int(m.group(2)))] = m.group(1)
                elif (m := _EXEC.search(line)):
                    path = m.group(1).strip()
                    if pid1:
                        continue  # the entry's own exec chain
                    argv = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))[:6]
                    a["exec"].add(" ".join([os.path.basename(path)] + argv[1:])[:160])
                elif any(f" X {call}(" in line for call in ("connect", "sendto", "sendmsg", "sendmmsg")):
                    addresses = _ADDRESS.findall(line)
                    fd = _SOCK_FD.search(line)
                    family = socket_families.get((pm.group(1), int(fd.group(1), 16))) if pm and fd else None
                    if addresses and fd and " X connect(" in line:
                        socket_peers[fd.group(2)] = addresses[0]
                    if not addresses and fd and fd.group(2) in socket_peers:
                        addresses = [socket_peers[fd.group(2)]]
                    if not addresses and family == "AF_NETLINK":
                        continue  # kernel route/interface enumeration, never an external socket
                    if not addresses:
                        issues.add("socket-destination-unresolved")
                        a["network"].add(line.split(" X ", 1)[1].strip()[:200])
                    for family, addr, port in addresses:
                        addr = addr.strip().strip('"')
                        if family == "AF_UNIX":
                            if addr and addr != "@" and addr not in QUIET_SOCKETS:
                                a["network"].add(f"unix:{addr[:120]}")
                            continue
                        if family not in ("AF_INET", "AF_INET6"):
                            issues.add("socket-family-unparsed")
                            continue
                        if (addr == OBSERVE_GATEWAY and port == "53") or addr.startswith(("127.", "0.0.0.0")) or addr in ("::1", "::"):
                            continue
                        name = DNS.name_for(addr) if DNS else None
                        a["network"].add(f"{name or addr}:{port}")
                elif (m := _OPEN.search(line)):
                    path, flags = m.group(1).strip().strip('"'), m.group(2)
                    if not re.search(r"\) = (?:0x[0-9a-f]+|[0-9]+)(?: |$)", line):
                        continue  # failed opens are attempts, not opened credentials
                    if not path.startswith("/"):
                        issues.add("relative-path-unresolved")
                        # Still flag known decoy names; never infer clean from an unresolved cwd/dirfd.
                        if any(d in path for d in DECOYS):
                            a["decoys"].add(path[:160])
                    path = os.path.normpath(path)
                    rel = path[len(HOME_IN) + 1:] if path.startswith(HOME_IN + "/") else None
                    if (rel and rel.startswith(DECOYS)) or path == f"{APP_IN}/.env":
                        a["decoys"].add(rel or ".env (working directory)")
                    writing = any(f in flags for f in ("O_WRONLY", "O_RDWR", "O_CREAT"))
                    if writing and ((rel and rel.startswith(PERSIST)) or not path.startswith(("/tmp", HOME_IN, "/work", "/dev/", "/proc/"))):
                        a["writes"].add(path[:160])
                elif any(f" X {call}(" in line for call in ("sendto", "sendmsg", "sendmmsg", "execveat", "unlinkat", "renameat", "mkdirat", "chdir", "fchdir")):
                    # Preserve unparsed activity without claiming successful or complete attribution.
                    issues.add("unparsed-syscall")
                    a["network" if "send" in line else "exec"].add(line.split(" X ", 1)[1][:200])
    if not records:
        issues.add("trace-empty")
    for i, (name, since, until) in enumerate(phases):
        if DNS:
            # The last phase runs a little past its mark: the trace and the logger flush late.
            lookups[name] = DNS.names(src_ip, since, until + (1 if i == len(phases) - 1 else 0))
    out: dict[str, Any] = {}
    for name, _, _ in phases:
        if any(len(v) > 40 for v in acts[name].values()) or len(lookups.get(name, [])) > 60:
            issues.add("trace-truncated")
        section = {k: sorted(v)[:40] for k, v in acts[name].items() if v}
        if lookups.get(name):
            section["lookups"] = lookups[name][:60]
        out[name] = {**section, "complete": not issues, "incompleteReasons": sorted(issues)}
    return out


# -- canary arguments ------------------------------------------------------------------------------

class CanaryUnsupported(ValueError):
    pass


def canary_value(name: str, spec: Any, tag: str, depth: int = 0) -> Any:
    """Generate fake input, never accept a supplier's recipient/path default as authority.

    Unsupported constraints are explicitly skipped instead of counting a rejected call as coverage.
    This bounded subset is deliberately conservative; it is not a JSON Schema validator.
    """
    spec = spec if isinstance(spec, dict) else {}
    if depth > 4 or any(k in spec for k in ("$ref", "allOf", "anyOf", "oneOf", "not", "pattern", "if", "dependentSchemas")):
        raise CanaryUnsupported("unsupported schema constraint")
    kind = spec.get("type", "object" if "properties" in spec else "string")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    low = name.lower()
    if kind == "object":
        value = canary_args(spec, tag, depth + 1)
    elif kind == "array":
        count = max(1, spec.get("minItems", 0))
        if not isinstance(count, int) or count > 5 or spec.get("uniqueItems") or spec.get("maxItems", count) < count:
            raise CanaryUnsupported("unsupported array bounds")
        value = [canary_value(name, spec.get("items"), tag, depth + 1) for _ in range(count)]
    elif kind in ("number", "integer"):
        value = spec.get("minimum", 1)
        if any(k in spec for k in ("exclusiveMinimum", "exclusiveMaximum", "multipleOf")) or value > spec.get("maximum", value):
            raise CanaryUnsupported("unsupported numeric bounds")
    elif kind == "boolean":
        value = False
    elif kind == "null":
        value = None
    elif kind == "string":
        if "email" in low or low in ("to", "recipient", "recipients", "cc", "bcc") or spec.get("format") == "email":
            value = f"canary-{tag}@histor-trap.invalid"
        elif any(k in low for k in ("url", "uri", "href", "endpoint", "link", "webhook")) or spec.get("format") in ("uri", "uri-reference", "url"):
            value = f"https://histor-trap.invalid/{tag}"
        elif any(k in low for k in ("path", "file", "dir", "folder")):
            value = f"{HOME_IN}/notes/canary.txt"
        else:
            choices = spec.get("enum", [spec.get("const", f"histor canary {tag}")])
            value = choices[0] if isinstance(choices, list) and choices else f"histor canary {tag}"
            if not isinstance(value, str) or "@" in value or "://" in value or value.startswith(("/", "~")):
                raise CanaryUnsupported("supplier-provided destination")
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 10000):
            raise CanaryUnsupported("string bounds")
        if spec.get("format") not in (None, "email", "uri", "uri-reference", "url"):
            raise CanaryUnsupported("unsupported format")
    else:
        raise CanaryUnsupported("unsupported type")
    if ("enum" in spec and value not in spec["enum"]) or ("const" in spec and value != spec["const"]):
        raise CanaryUnsupported("canary conflicts with supplier constraint")
    return value


def canary_args(schema: Any, tag: str, depth: int = 0) -> dict[str, Any]:
    schema = schema if isinstance(schema, dict) else {}
    if depth > 4 or any(k in schema for k in ("$ref", "allOf", "anyOf", "oneOf", "not", "if", "dependentRequired", "dependentSchemas")):
        raise CanaryUnsupported("unsupported object constraint")
    props = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(props, dict) or not isinstance(required, list) or any(k not in props for k in required) or len(required) > 20:
        raise CanaryUnsupported("required properties exceed supported schema")
    keys = list(dict.fromkeys(required + list(props)))[:20]
    return {k: canary_value(k, props[k], tag, depth) for k in keys}


def lifecycle_packages(work: Path) -> list[str]:
    """npm packages in the tree that would run install scripts."""
    found = []
    for manifest in (work / "pkg" / "node_modules").glob("**/package.json"):
        if manifest.parent.parent.name not in ("node_modules",) and not manifest.parent.parent.name.startswith("@"):
            continue
        try:
            scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            continue
        if any(k in scripts for k in ("preinstall", "install", "postinstall")):
            found.append(str(manifest.parent.relative_to(work / "pkg" / "node_modules")))
    return sorted(set(found))[:50]


# -- stages -------------------------------------------------------------------------------------

def base_flags(run_id: str, runtime: str = RUNTIME) -> list[str]:
    # The observer's own unprivileged uid: files it created are writable by the install stage,
    # and nothing inside the container is root.
    return ["--runtime", runtime, "--rm", "--name", f"histor-{run_id}", "--user", f"{os.getuid()}:{os.getgid()}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256",
            "--label", "histor-sandbox=1"]


def install(registry: str, name: str, version: str, work: Path, run_id: str) -> tuple[bool, str]:
    # On a user-defined network Docker points resolv.conf at its embedded DNS (127.0.0.11), which
    # lives in iptables rules gVisor's own network stack never sees: name the resolvers instead.
    flags = [*base_flags(run_id + "-i"), "--network", FETCH_NETWORK, "--memory", "1g", "--memory-swap", "1g", "--cpus", "1",
             "-v", f"{STATE / 'resolv.conf'}:/etc/resolv.conf:ro",
             "-v", f"{work}:/work", "-e", "HOME=/work/.home", "-w", "/work"]
    if registry == "npm":
        cmd = ["npm", "install", "--ignore-scripts", "--omit=dev", "--no-audit", "--no-fund", "--no-update-notifier",
               "--prefix", "/work/pkg", f"{name}@{version}"]
    else:
        cmd = ["pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:",
               "--no-compile", "--target", "/work/pkg", f"{name}=={version}"]
    try:
        res = docker("run", *flags, IMAGES[registry], *cmd, timeout=INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        docker("rm", "-f", f"histor-{run_id}-i")
        return False, "install timed out"
    return res.returncode == 0, tail(res.stderr or res.stdout)


def npm_entry(work: Path, name: str) -> list[str] | None:
    manifest = work / "pkg" / "node_modules" / name / "package.json"
    try:
        pkg = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bins = pkg.get("bin")
    short = name.split("/")[-1]
    if isinstance(bins, str):
        target = bins
    elif isinstance(bins, dict) and bins:
        target = bins.get(short) or bins.get(name) or next(
            (v for k, v in bins.items() if "mcp" in k.lower()), next(iter(bins.values())))
    else:
        return None
    rel = os.path.normpath(target)
    if rel.startswith("..") or os.path.isabs(rel):
        return None  # a bin pointing outside its own package
    return ["node", f"/work/pkg/node_modules/{name}/{rel}"]


def pypi_entry(work: Path, name: str) -> list[str] | None:
    want = re.sub(r"[-_.]+", "-", name).lower()
    scripts: dict[str, str] = {}
    for info in (work / "pkg").glob("*.dist-info"):
        dist = re.sub(r"[-_.]+", "-", info.name.removesuffix(".dist-info").rsplit("-", 1)[0]).lower()
        ep = info / "entry_points.txt"
        if dist != want or not ep.is_file():
            continue
        section = None
        for line in ep.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line.strip("[]").strip()
            elif section == "console_scripts" and "=" in line:
                key, value = (p.strip() for p in line.split("=", 1))
                scripts[key] = value.split()[0]
    if not scripts:
        return None
    target = scripts.get(want) or scripts.get(name) or next(
        (v for k, v in scripts.items() if "mcp" in k.lower()), next(iter(scripts.values())))
    module, _, attr = target.partition(":")
    if not re.fullmatch(r"[A-Za-z_][\w.]*", module) or (attr and not re.fullmatch(r"[A-Za-z_][\w.]*", attr)):
        return None
    call = f"import sys, importlib; m = importlib.import_module({module!r})"
    call += f"; f = m\nfor p in {attr!r}.split('.'):\n    f = getattr(f, p)\nsys.exit(f())" if attr else ""
    return ["python", "-c", call]


class Session:
    """MCP over the container's stdio: newline-delimited JSON-RPC."""

    def __init__(self, proc: subprocess.Popen[bytes], deadline: float) -> None:
        self.proc = proc
        self.deadline = deadline
        self.buf = b""
        self.read = 0
        self.sel = selectors.DefaultSelector()
        assert proc.stdout is not None
        os.set_blocking(proc.stdout.fileno(), False)
        self.sel.register(proc.stdout, selectors.EVENT_READ)

    def send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def _lines(self, until: float):
        while True:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                yield line
            left = min(until, self.deadline) - time.monotonic()
            if left <= 0:
                raise TimeoutError
            if not self.sel.select(left):
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)  # type: ignore[union-attr]
            if not chunk:
                raise EOFError
            self.read += len(chunk)
            if self.read > MAX_OUTPUT:
                raise OverflowError
            self.buf += chunk

    def call(self, rid: int, method: str, params: dict[str, Any], wait_s: float) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        for raw in self._lines(time.monotonic() + wait_s):
            try:
                msg = json.loads(raw)
            except ValueError:
                continue  # a server that logs to stdout: not a protocol message
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                return msg
            if "method" in msg and "id" in msg:  # the server asks us something: decline, never act
                reply = {"roots": []} if msg["method"] == "roots/list" else None
                self.send({"jsonrpc": "2.0", "id": msg["id"], **({"result": reply} if reply is not None else
                          {"error": {"code": -32601, "message": "not supported by the observer"}})})
        raise EOFError


def traced_available() -> bool:
    """The tracing runtime, the internal network and the DNS logger are all there."""
    runtimes = docker("info", "--format", "{{json .Runtimes}}").stdout
    try:
        health = Path("/run/histor-firewall/healthy.json")
        verified = json.loads(health.read_text())
        if health.stat().st_uid != 0 or verified.get("trapRedirects") != 1 or not 0 <= time.time() - verified["verifiedAt"] <= 65:
            return False
        net = json.loads(docker("network", "inspect", OBSERVE_NETWORK).stdout)[0]
        return TRACE_RUNTIME in runtimes and DNS is not None and SINK is not None and net["Internal"] and not net.get("EnableIPv6")
    except (OSError, ValueError, KeyError, IndexError):
        return False


def sandbox_flags(run_id: str, work: Path, traced: bool, cidfile: Path, *, package_rw: bool = False) -> list[str]:
    flags = [*base_flags(run_id, TRACE_RUNTIME if traced else RUNTIME), "--read-only", "--memory", "512m", "--memory-swap", "512m",
             "--cpus", "1", "--tmpfs", "/tmp:rw,size=64m", "-v", f"{work}/pkg:/work/pkg" + ("" if package_rw else ":ro"),
             "-v", f"{work}/home:{HOME_IN}", "-v", f"{work}/app:{APP_IN}", "-e", f"HOME={HOME_IN}", "-e", "NODE_ENV=production", "-e", f"HISTOR_CANARY_RECIPIENT=canary-{work.name}@histor-trap.invalid"]
    if traced:
        flags += ["--network", OBSERVE_NETWORK, "-v", f"{STATE / 'resolv-observe.conf'}:/etc/resolv.conf:ro", "--cidfile", str(cidfile)]
    else:
        flags += ["--network", "none"]
    if traced:
        flags += ["-v", f"{STATE / 'trap-ca/ca.pem'}:/etc/histor-trap-ca.pem:ro", "-e", "NODE_EXTRA_CA_CERTS=/etc/histor-trap-ca.pem",
                  "-e", "SSL_CERT_FILE=/etc/histor-trap-ca.pem", "-e", "REQUESTS_CA_BUNDLE=/etc/histor-trap-ca.pem"]
    return flags


def merge_sink(section: dict[str, Any], ip: str | None, since: float, until: float, tag: str) -> None:
    captured = SINK.phase(ip, since, until, tag) if SINK else {"complete": False}
    complete = section.get("complete") is True and captured.pop("complete") is True and not captured.get("truncated")
    section.update(captured)
    section["complete"] = complete


def container_ip(cidfile: Path, wait_s: float = 30) -> tuple[str | None, str | None]:
    """The run container's id and its address on the internal network, read while it runs."""
    deadline = time.monotonic() + wait_s
    cid = ""
    while time.monotonic() < deadline:
        cid = cidfile.read_text().strip() if cidfile.exists() else ""
        if cid:
            try:
                res = docker("inspect", "--format", f'{{{{(index .NetworkSettings.Networks "{OBSERVE_NETWORK}").IPAddress}}}}',
                             cid, timeout=min(10, max(0.1, deadline - time.monotonic())))
                if res.returncode == 0 and res.stdout.strip():
                    return cid, res.stdout.strip()
            except subprocess.TimeoutExpired:
                pass  # Docker can briefly serialize network setup under load; stay bounded.
        time.sleep(0.1)
    return cid or None, None


def run_scripts(work: Path, run_id: str, packages: list[str]) -> dict[str, Any]:
    """npm install scripts, run on their own under the trace: what installing would have done."""
    cidfile = work / "cid-scripts"
    flags = [*sandbox_flags(run_id + "-s", work, True, cidfile, package_rw=True), "-w", "/work/pkg"]
    # npm itself reads ~/.npmrc and checks for updates. Keep its configuration
    # separate from the planted credential; package lifecycle code still sees
    # the decoy at HOME/.npmrc and all its reads remain observable.
    flags += ["-e", "NPM_CONFIG_USERCONFIG=/dev/null", "-e", "NPM_CONFIG_UPDATE_NOTIFIER=false"]
    since = time.time()
    proc = subprocess.Popen([DOCKER, "run", *flags, IMAGES["npm"], "npm", "rebuild", "--foreground-scripts", "--no-audit", "--no-fund"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cid, ip = None, None
    timed_out = False
    try:
        cid, ip = container_ip(cidfile)
        proc.wait(timeout=SCRIPTS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        docker("rm", "-f", f"histor-{run_id}-s", timeout=30)
        proc.wait(timeout=10)
    until = time.time()
    section = read_trace(cid, [("installScripts", since, until)], "npm", ip)["installScripts"] if cid else {"complete": False, "incompleteReasons": ["container-id-missing"]}
    merge_sink(section, ip, since, until, run_id)
    return {"packages": packages, **section, "exitCode": proc.returncode, "timedOut": timed_out,
            "complete": section.get("complete") is True and not timed_out and proc.returncode == 0}


def run(registry: str, work: Path, entry: list[str], run_id: str, traced: bool = False) -> dict[str, Any]:
    cidfile = work / "cid-run"
    flags = [*sandbox_flags(run_id + "-r", work, traced, cidfile), "-i", "-w", APP_IN]
    if registry == "pypi":
        flags += ["-e", "PYTHONPATH=/work/pkg", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    start, wall = time.monotonic(), time.time()
    proc = subprocess.Popen([DOCKER, "run", *flags, IMAGES[registry], *entry],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    cid, ip = None, None
    marks: dict[str, Any] = {}
    try:
        cid, ip = container_ip(cidfile) if traced else (None, None)
        answer = talk(proc, start, run_id, traced, marks)
    finally:
        docker("rm", "-f", f"histor-{run_id}-r", timeout=30)
        try:
            proc.kill()
        except OSError:
            pass
    ended = time.time()
    if traced and cid:
        listed = marks.get("listed", ended)
        answer["behaviour"] = read_trace(cid, [("startup", wall, listed), ("calls", listed, ended + 2)], entry[0], ip)
        merge_sink(answer["behaviour"]["startup"], ip, wall, listed, run_id)
        merge_sink(answer["behaviour"]["calls"], ip, listed, ended + 2, run_id)
        calls = answer["behaviour"]["calls"]
        calls.update(marks.get("coverage", {"successful": 0, "skipped": len(answer.get("tools", []))}))
        calls["complete"] = calls.get("complete") is True and marks.get("callsComplete") is True
    else:
        answer["behaviour"] = {p: {"complete": False, "incompleteReasons": ["trace-unavailable"]} for p in ("startup", "calls")}
    return {**answer, "seconds": round(time.monotonic() - start, 1)}


def talk(proc: subprocess.Popen[bytes], start: float, tag: str = "", traced: bool = False,
         marks: dict[str, Any] | None = None) -> dict[str, Any]:
    marks = marks if marks is not None else {}
    session = Session(proc, start + RUN_TIMEOUT_S)
    result: dict[str, Any] = {}
    try:
        init = session.call(1, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                              "clientInfo": {"name": "histor-sandbox", "version": VERSION}},
                            INIT_TIMEOUT_S)
        if "error" in init:
            return {"status": "protocol", "detail": tail(json.dumps(init["error"]))}
        info = init.get("result") or {}
        result["serverInfo"] = info.get("serverInfo")
        result["protocolVersion"] = info.get("protocolVersion")
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[Any] = []
        cursor = None
        for page in range(MAX_PAGES):
            listed = session.call(2 + page, "tools/list", {"cursor": cursor} if cursor else {}, RUN_TIMEOUT_S)
            if "error" in listed:
                return {**result, "status": "protocol", "detail": tail(json.dumps(listed["error"]))}
            body = listed.get("result") or {}
            tools.extend(body.get("tools") or [])
            if len(tools) > MAX_TOOLS:
                return {**result, "status": "too-many-tools", "detail": f"more than {MAX_TOOLS} tools"}
            cursor = body.get("nextCursor")
            if not cursor:
                break
        if cursor:
            return {**result, "status": "too-many-pages", "detail": "tool listing incomplete"}
        marks["listed"] = time.time()
        if traced:
            coverage = {"total": len(tools), "attempted": 0, "successful": 0, "errors": 0, "timeouts": 0, "skipped": 0, "outcomes": []}
            marks["coverage"] = coverage
            for i, tool in enumerate(tools[:CALL_LIMIT]):
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                    coverage["skipped"] += 1
                    continue
                outcome = {"tool": tool["name"], "status": "skipped"}
                coverage["outcomes"].append(outcome)
                try:
                    arguments = canary_args(tool.get("inputSchema"), tag)
                    coverage["attempted"] += 1
                    reply = session.call(10_000 + i, "tools/call", {"name": tool["name"], "arguments": arguments}, CALL_TIMEOUT_S)
                    valid = isinstance(reply.get("result"), dict) and isinstance(reply["result"].get("content"), list)
                    good = valid and "error" not in reply and not reply["result"].get("isError")
                    coverage["successful" if good else "errors"] += 1
                    outcome["status"] = "success" if good else "error"
                except (CanaryUnsupported, TypeError, ValueError) as exc:
                    coverage["skipped"] += 1
                    outcome["reason"] = str(exc)[:120]
                except TimeoutError:
                    coverage["timeouts"] += 1
                    outcome["status"] = "timeout"
                    if time.monotonic() >= start + RUN_TIMEOUT_S:
                        break
                except (EOFError, OverflowError):
                    coverage["errors"] += 1
                    outcome["status"] = "error"
                    break
            coverage["skipped"] = coverage["total"] - coverage["attempted"]
            marks["callsComplete"] = coverage["successful"] == coverage["total"]
        return {**result, "status": "ok", "tools": tools}
    except TimeoutError:
        return {**result, "status": "timeout", "detail": stderr_of(proc)}
    except EOFError:
        # Exited before answering: most often a server that needs an API key or a path to start.
        return {**result, "status": "exited", "detail": stderr_of(proc)}
    except OverflowError:
        return {**result, "status": "too-large", "detail": f"more than {MAX_OUTPUT} bytes on stdout"}


def stderr_of(proc: subprocess.Popen[bytes]) -> str:
    try:
        proc.kill()
        _, err = proc.communicate(timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    return tail((err or b"").decode("utf-8", "replace"))


# -- one observation ----------------------------------------------------------------------------

def slot():
    STATE.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + SLOT_WAIT_S
    while True:
        for n in range(SLOTS):
            fh = open(STATE / f"slot-{n}.lock", "w")  # noqa: SIM115 - held for the whole observation
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except BlockingIOError:
                fh.close()
        if time.monotonic() > deadline:
            raise TimeoutError("no free sandbox slot")
        time.sleep(2)


def image_digest(registry: str) -> str | None:
    res = docker("image", "inspect", "--format", "{{index .RepoDigests 0}}", IMAGES[registry])
    return res.stdout.strip() or None


def observe(registry: str, name: str, version: str | None) -> dict[str, Any]:
    doc: dict[str, Any] = {"type": "histor.package-observation/v1", "observer": VERSION, "registry": registry,
                           "name": name, "runtime": RUNTIME, "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        meta = resolve(registry, name, version)
    except Exception as exc:  # noqa: BLE001 - every failure is a status, never a crash
        return {**doc, "status": "not-found", "detail": tail(f"{type(exc).__name__}: {exc}")}
    doc.update(version=meta["version"], integrity=meta.get("integrity"), image=image_digest(registry))
    if registry == "pypi" and not meta.get("has_wheel"):
        return {**doc, "status": "no-wheel", "detail": "only an sdist: building it would run the package's code"}
    lock = slot()
    run_id = secrets.token_hex(6)
    work = STATE / "runs" / run_id
    try:
        (work / "pkg").mkdir(parents=True)
        (work / ".home").mkdir()
        plant_decoys(work, run_id)
        ok, log = install(registry, name, meta["version"], work, run_id)
        if not ok:
            return {**doc, "status": "install-failed", "detail": log}
        traced = traced_available()
        pkgs = lifecycle_packages(work) if registry == "npm" else []
        scripts = (run_scripts(work, run_id, pkgs) if traced else {"complete": False, "incompleteReasons": ["trace-unavailable"]}) if pkgs else {"complete": True, "status": "not-applicable", "packages": []}
        entry = npm_entry(work, name) if registry == "npm" else pypi_entry(work, name)
        result = run(registry, work, entry, run_id, traced) if entry else {"status": "no-entry-point", "detail": "the package declares no command to start"}
        behaviour = result.setdefault("behaviour", {})
        behaviour["installScripts"] = scripts
        behaviour["observer"] = "gvisor-trace/2"
        behaviour["version"] = meta["version"]
        behaviour["integrity"] = meta.get("integrity")
        behaviour["complete"] = result.get("status") == "ok" and all(behaviour.get(p, {}).get("complete") is True for p in ("installScripts", "startup", "calls"))
        return {**doc, **result}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        lock.close()


def selftest() -> dict[str, Any]:
    info = docker("info", "--format", "{{json .Runtimes}}").stdout
    probe = docker("run", "--rm", "--runtime", RUNTIME, "--network", "none", IMAGES["npm"], "uname", "-r", timeout=120)
    return {"type": "histor.sandbox-selftest/v1", "observer": VERSION, "runsc": RUNTIME in info,
            "kernel": probe.stdout.strip(), "gvisor": "gvisor" in probe.stdout,
            "trace": TRACE_RUNTIME in info, "dnsLogger": DNS is not None,
            "observeNetwork": docker("network", "inspect", OBSERVE_NETWORK).returncode == 0,
            "images": {k: image_digest(k) for k in IMAGES}}


MAX_REQUESTS = 2 * SLOTS  # in flight; SLOTS observe at once, the rest wait for a slot


def campaign_case(body: dict[str, Any]) -> dict[str, Any]:
    """Run a built-in fixture, never caller-supplied code, sharing production traps/slots."""
    if (not isinstance(body, dict) or set(body) != {"seed", "index"}
            or not isinstance(body["seed"], str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", body["seed"])
            or type(body["index"]) is not int or not 0 <= body["index"] < 72):
        raise Refused("expected a bounded seed and fixture index 0..71")
    import histor_fixtures
    case = histor_fixtures.generate(body["seed"])[body["index"]]
    lock = slot()
    try:
        evidence = histor_fixtures.observe_case(sys.modules[__name__], case)
        return {"decision": histor_fixtures.evaluate(case, evidence),
                "observerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "fixturesSha256": hashlib.sha256(Path(histor_fixtures.__file__).read_bytes()).hexdigest()}
    finally:
        lock.close()


def serve() -> None:
    """HTTPS: ``POST /observe`` {"registry", "name", "version"?} and ``GET /selftest``, both behind
    ``Authorization: Bearer $HISTOR_SANDBOX_TOKEN``. Certificate and key from HISTOR_SANDBOX_CERT /
    HISTOR_SANDBOX_KEY; HISTOR pins the certificate, so it can be self-signed."""
    token = os.environ.get("HISTOR_SANDBOX_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("HISTOR_SANDBOX_TOKEN must be set (32+ characters)")
    host, _, port = os.environ.get("HISTOR_SANDBOX_LISTEN", "0.0.0.0:9443").rpartition(":")
    gate = threading.BoundedSemaphore(MAX_REQUESTS)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"histor-sandbox/{VERSION}"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # one line per request, no bodies
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

        def reply(self, code: int, doc: dict[str, Any]) -> None:
            body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorised(self) -> bool:
            given = self.headers.get("Authorization", "").removeprefix("Bearer ").encode()
            if hmac.compare_digest(given, token.encode()):
                return True
            self.reply(401, {"status": "unauthorised"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self.authorised():
                return
            if self.path != "/selftest":
                self.reply(404, {"status": "not-found"})
                return
            self.reply(200, selftest())

        def do_POST(self) -> None:  # noqa: N802
            if not self.authorised():
                return
            if self.path not in ("/observe", "/campaign-case"):
                self.reply(404, {"status": "not-found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 2048:
                self.reply(400, {"status": "refused", "detail": "body must be 1..2048 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
                if self.path == "/observe":
                    argv = ["observe", str(body["registry"]), str(body["name"]),
                            *([str(body["version"])] if body.get("version") else [])]
                    req = parse(argv)
            except (ValueError, KeyError, TypeError, Refused) as exc:
                self.reply(400, {"status": "refused", "detail": str(exc)[:200]})
                return
            if not gate.acquire(blocking=False):
                self.reply(503, {"status": "busy", "detail": "too many observations in flight"})
                return
            try:
                if RUNTIME not in docker("info", "--format", "{{json .Runtimes}}").stdout:
                    self.reply(503, {"status": "sandbox-unavailable", "detail": "gVisor (runsc) is not registered"})
                    return
                try:
                    result = (campaign_case(body) if self.path == "/campaign-case"
                              else observe(req[1], req[2], req[3] if len(req) > 3 else None))
                    self.reply(200, result)
                except Refused as exc:
                    self.reply(400, {"status": "refused", "detail": str(exc)})
                except TimeoutError as exc:
                    self.reply(503, {"status": "busy", "detail": str(exc)})
                except Exception as exc:  # noqa: BLE001 - failed inspection never means clean
                    self.reply(503, {"status": "inspection-failed", "detail": type(exc).__name__})
            finally:
                gate.release()

    global DNS, SINK
    try:
        from histor_sink import start as start_sink
        SINK = start_sink(OBSERVE_GATEWAY, STATE)
        DNS = DnsLogger(OBSERVE_GATEWAY)
    except OSError as exc:
        sys.stderr.write(f"DNS logger not started ({exc}); observations run without behaviour\n")
    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(os.environ["HISTOR_SANDBOX_CERT"], os.environ["HISTOR_SANDBOX_KEY"])
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    httpd.serve_forever()


def parse(argv: list[str]) -> tuple[str, ...]:
    if argv in (["selftest"], ["serve"]):
        return (argv[0],)
    if len(argv) not in (3, 4) or argv[0] != "observe" or argv[1] not in IMAGES:
        raise Refused("usage: observe npm|pypi NAME [VERSION] | selftest | serve")
    registry, name = argv[1], argv[2]
    version = argv[3] if len(argv) == 4 else None
    if not (NPM_NAME if registry == "npm" else PYPI_NAME).match(name):
        raise Refused("bad package name")
    if version is not None and not VERSION_RE.match(version):
        raise Refused("bad version")
    return ("observe", registry, name, *( [version] if version else []))


def main() -> int:
    # Under the SSH key the request arrives in SSH_ORIGINAL_COMMAND; never through a shell.
    raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    argv = raw.split() if raw is not None else sys.argv[1:]
    try:
        req = parse(argv)
    except Refused as exc:
        out({"status": "refused", "detail": str(exc)})
        return 2
    if os.getuid() == 0:
        out({"status": "refused", "detail": "run as the unprivileged sandbox user, never as root"})
        return 2
    if req[0] == "selftest":
        out(selftest())
        return 0
    if req[0] == "serve":
        if raw is not None:
            out({"status": "refused", "detail": "serve is started by the service, not over SSH"})
            return 2
        serve()
        return 0
    if RUNTIME not in docker("info", "--format", "{{json .Runtimes}}").stdout:
        out({"status": "sandbox-unavailable", "detail": "gVisor (runsc) is not registered with Docker; refusing to run"})
        return 0
    global DNS, SINK
    try:
        from histor_sink import start as start_sink
        SINK = start_sink(OBSERVE_GATEWAY, STATE)
        DNS = DnsLogger(OBSERVE_GATEWAY)  # needs CAP_NET_BIND_SERVICE; without it, no behaviour
    except OSError:
        DNS = None
    try:
        out(observe(req[1], req[2], req[3] if len(req) > 3 else None))
    except TimeoutError as exc:
        out({"status": "busy", "detail": str(exc)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
