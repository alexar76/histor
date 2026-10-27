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
* Run stage: an internal network with no route out, a read-only root (the package directory is
  the run's own writable copy, as an install on a user's machine is), no environment
  beyond PATH/HOME/USER, an unprivileged uid named in its own /etc/passwd, no capabilities, no new privileges, memory/CPU/PID
  limits. The server speaks MCP over the container's stdin/stdout to this script, which sends
  ``server/discover`` (or the legacy ``initialize`` handshake) and ``tools/list``, then calls up to CALL_LIMIT tools once each with canary
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
* Configuration (only when HISTOR asks, and only names): a server that refuses to start without
  ``SOME_API_KEY`` is started again with that variable set to a placeholder this script makes —
  ``histor-envkey-<run>`` for a key, an address on ``histor-trap.invalid`` for a URL, the working
  directory for a path — and, when its registry entry or its usage text names one, a start
  argument such as ``serve``; for PyPI, a dependency pin from a fixed list (``mcp<2``: how a
  server written for MCP SDK 1.x ran before 2.0); for npm, a runtime from a fixed list
  (``node24``: the current LTS image, for a package that requires it). HISTOR never sends a value. The traps record where a placeholder
  key was sent, so "sent the key to its own API" and "sent it somewhere else" can be told apart.
  Reviewed launch profiles may also supply the container's exact working directory, ``/work/app``,
  or the argument token ``histor-placeholder-token``. Only that exact token is replaced with a
  per-run placeholder before exec; no shell expansion or external credential is involved.
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

VERSION = "5"  # modern discovery/per-request metadata, bounded legacy fallback; existing evidence shape
STATE = Path(os.environ.get("HISTOR_SANDBOX_STATE", "/var/lib/histor-sandbox"))
DOCKER = shutil.which("docker") or "/usr/bin/docker"
RUNTIME = "runsc"
FETCH_NETWORK = "histor-fetch"
# Built on the sandbox host by install.sh from images/*.Dockerfile: node:22-bookworm-slim and
# python:3.12-slim plus bash, git, Python 3 or Node 22, and a Java 21 runtime.
IMAGES = {"npm": "histor-node:22", "pypi": "histor-python:3.12"}
SLOTS = int(os.environ.get("HISTOR_SANDBOX_SLOTS", "2"))  # parallel observations on this host
SLOT_WAIT_S = 600
INSTALL_TIMEOUT_S = 360
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
DISCOVER_TIMEOUT_S = 10        # bounded modern probe before a silent legacy server is initialized
MAX_OUTPUT = 16 * 1024 * 1024  # bytes read from the server's stdout, all pages together
MAX_TOOLS = 4096
MAX_PAGES = 64
PROTOCOL = "2025-06-18"
MODERN_PROTOCOL = "2026-07-28"
MODERN_ERRORS = {-32020, -32021, -32022}  # header mismatch, missing capability, unsupported version

NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]{0,100}/)?[a-z0-9][a-z0-9._~-]{0,100}$")
PYPI_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
UA = f"histor-sandbox/{VERSION} (+https://histor.modelmarket.dev)"
# Configuration HISTOR may ask for: variable NAMES (the values are made here) and start arguments.
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
ENV_DENY = re.compile(r"^(PATH|HOME|USER|SHELL|PWD|TMPDIR|TERM|LANG|LC_[A-Z_]+|NODE_[A-Z_]+|NPM_[A-Z_]+|PYTHON[A-Z_]*|PIP_[A-Z_]+"
                      r"|UV_[A-Z_]+|LD_[A-Z_]+|DYLD_[A-Z_]+|SSL_[A-Z_]+|REQUESTS_CA_BUNDLE|CURL_CA_BUNDLE|HISTOR_[A-Z_]+"
                      r"|(?:HTTPS?|ALL|NO)_PROXY|GIT_[A-Z_]+|DOCKER_[A-Z_]+)$")
ARG_TOKEN = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,63}|-{1,2}[A-Za-z0-9][A-Za-z0-9._-]{0,40}(?:=[A-Za-z0-9._:/@+-]{1,63})?)$")
MAX_ENV = 8
MAX_ARGS = 6
# Exact, bounded local path and symbolic token used by HISTOR's reviewed launch profiles.
# The path is already inside this run's container; arbitrary absolute paths stay refused.
PLACEHOLDER_TOKEN = "histor-placeholder-token"
# Dependency pins HISTOR may ask for, by registry: a fixed list, never a requirement string from
# outside. mcp<2 is how a server written for the MCP SDK 1.x ran before 2.0 renamed FastMCP.
PINS = {"pypi": ("mcp<2",)}
# Runtimes HISTOR may ask for, by registry: a package that exits with "Node 24+ required" runs
# again on the current LTS image. The image a run used is always in its report (image).
RUNTIMES = {"npm": {"node24": "histor-node:24"},
            "pypi": {"py313": "histor-python:3.13", "py314": "histor-python:3.14"}}
# The Python images, oldest first, by the version each runs: a package whose Requires-Python
# excludes the default runs on the oldest one it accepts (pip refuses it on 3.12 with
# "from versions: none"), without HISTOR asking — it is what pip itself would have needed.
PYTHONS = ((None, (3, 12)), ("py313", (3, 13)), ("py314", (3, 14)))
MAX_NOISE = 4096               # bytes of non-protocol stdout kept to say why a server exited
# The trace reader's own generation: 3 resolves relative opens and reads socketpair ends as local
# (2 listed asyncio's self-pipe as a destination). HISTOR runs a package traced by an older
# reader again when that left it with sends it could not place.
TRACE_OBSERVER = "gvisor-trace/9"  # 4: evidence matched to the address only while this container held it; 5: its start read while it runs; 6: a socket's family follows the socket, not its fd number; 7: an accepted connection's peer; 8: a trap record carrying another run's tag is not this run's evidence, and leaves it incomplete; 9: renames, links, symlinks and creat are traced and their targets judged as writes, a .pth/sitecustomize written outside /tmp or code written into the installed package after install is persistence, and a trace runtime that does not record those calls leaves the run incomplete


class Refused(Exception):
    """A malformed request: exit 2, nothing run."""


def out(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def tail(text: str, limit: int = 400) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text).strip()
    return text[-limit:]


def head_tail(text: str, head: int = 600, end: int = 600) -> str:
    """What a process printed as it failed: the first lines (where the error says what it is)
    and the last (the stack's end). Keeping only the tail lost the message under a stack trace."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text).strip()
    return text if len(text) <= head + end + 5 else f"{text[:head]} … {text[-end:]}"


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
            "has_wheel": bool(wheels), "requires_python": meta["info"].get("requires_python")}


def _release(text: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in text.split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group()))
    return tuple(out)


def python_accepts(spec: Any, version: tuple[int, int]) -> bool | None:
    """Whether a Requires-Python specifier admits CPython ``version`` (at a late patch release);
    None when it cannot be read here, and pip decides. Stdlib only, so a subset of PEP 440:
    the clauses Requires-Python is written with."""
    if not isinstance(spec, str) or not spec.strip():
        return True
    have = (*version, 99)
    for clause in spec.split(","):
        m = re.fullmatch(r"\s*(~=|==|!=|<=|>=|<|>)\s*v?([0-9]+(?:\.[0-9]+)*)(\.\*)?\s*", clause)
        if not m:
            return None
        op, want, star = m.group(1), _release(m.group(2)), bool(m.group(3))
        padded = (want + (0, 0, 0))[:3]
        if op in ("==", "!="):
            same = have[:len(want)] == want if star else have == padded
            ok = same if op == "==" else not same
        elif op == "~=":
            ok = len(want) >= 2 and have >= padded and have[:len(want) - 1] == want[:-1]
        else:
            ok = {">=": have >= padded, "<=": have <= padded, ">": have > padded, "<": have < padded}[op]
        if not ok:
            return False
    return True


def python_for(spec: Any) -> tuple[str | None, bool]:
    """(runtime, runnable): the oldest Python image a package's Requires-Python admits — the
    default first — or (None, False) when it admits none of them."""
    for runtime, version in PYTHONS:
        verdict = python_accepts(spec, version)
        if verdict is None:
            return None, True  # unreadable here: the default image, and pip says what it thinks
        if verdict:
            return runtime, True
    return None, False


# -- decoys ---------------------------------------------------------------------------------------

def _canary(*parts: str) -> str:
    # Joined at run time: a literal key shape in this file would trip secret scanners.
    return "".join(parts)


def env_value(name: str, tag: str) -> str:
    """The placeholder for one variable a server asked for: never a real value, always recognisable."""
    last = name.rsplit("_", 1)[-1]
    if last in ("URL", "URI", "ENDPOINT", "HOST", "BASE", "SERVER", "ORIGIN", "DOMAIN"):
        return f"https://env-{tag}.histor-trap.invalid"
    if last in ("PATH", "DIR", "DIRECTORY", "FILE", "ROOT", "FOLDER", "WORKSPACE"):
        return APP_IN
    if last in ("PORT",):
        return "8080"
    if last in ("EMAIL", "USER", "USERNAME", "LOGIN", "ACCOUNT"):
        return f"canary-{tag}@histor-trap.invalid"
    return f"histor-envkey-{tag}"


def image_for(registry: str, runtime: str | None = None) -> str:
    return RUNTIMES[registry][runtime] if runtime else IMAGES[registry]


def check_config(env: Any, args: Any, pins: Any = None, registry: str = "pypi",
                 runtime: Any = None) -> tuple[list[str], list[str], list[str], str | None]:
    """The configuration HISTOR asked for, or Refused: names only, bounded, nothing that steers the runtime."""
    env = list(env or [])
    args = list(args or [])
    pins = list(pins or [])
    if any(p not in PINS.get(registry, ()) for p in pins):
        raise Refused("pin not on the list")
    if runtime is not None and (not isinstance(runtime, str) or runtime not in RUNTIMES.get(registry, {})):
        raise Refused("runtime not on the list")
    if len(env) > MAX_ENV or len(args) > MAX_ARGS:
        raise Refused("too much configuration")
    for name in env:
        if not isinstance(name, str) or not ENV_NAME.match(name) or ENV_DENY.match(name) or len(name) > 64:
            raise Refused("bad variable name")
    for arg in args:
        if not isinstance(arg, str) or (arg != APP_IN and not ARG_TOKEN.fullmatch(arg)):
            raise Refused("bad start argument")
    return sorted(set(env)), args, sorted(set(pins)), runtime


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
# Files an interpreter runs by itself on every start, wherever they land on its path: the
# installed package lives under /work, so a .pth written there ran unreported (Vulnetix
# GCVE-110-PYPI-2026-022091).
AUTORUN_NAMES = ("sitecustomize.py", "usercustomize.py")
AUTORUN_SUFFIXES = (".pth",)
# Code written into the installed package once it is installed changes what the next run executes.
PKG_IN = "/work/pkg"
CODE_SUFFIXES = (".py", ".pth", ".js", ".mjs", ".cjs", ".node", ".so")
# Calls that move or create a file under another name. The trace runtime must record them (see
# install.sh); a runtime that does not leaves every run incomplete rather than clean.
FILE_MOVE_CALLS = ("rename", "renameat", "renameat2", "link", "linkat", "symlink", "symlinkat", "creat")
TRACED_SYSCALLS = ("socket", "connect", "openat", "open", "creat", "execve", "execveat", "sendto", "sendmsg",
                   "sendmmsg", "bind", "unlinkat", "rename", "renameat", "renameat2", "link", "linkat",
                   "symlink", "symlinkat", "mkdirat", "socketpair", "accept", "accept4")


def persistent_write(path: str, phase: str) -> bool:
    """Would this write let the package act again after the run, on a real machine?"""
    rel = path[len(HOME_IN) + 1:] if path.startswith(HOME_IN + "/") else None
    if rel and rel.startswith(PERSIST):
        return True
    name = os.path.basename(path)
    if (name in AUTORUN_NAMES or name.endswith(AUTORUN_SUFFIXES)) and not path.startswith(("/tmp/", "/proc/", "/dev/")):
        return True
    if path.startswith(PKG_IN + "/") and phase != "installScripts" and name.endswith(CODE_SUFFIXES):
        return True
    return not path.startswith(("/tmp", HOME_IN, "/work", "/dev/", "/proc/"))


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
# gVisor prints openat's directory with the path it stands for — "AT_FDCWD /work/app", "0x3
# /home/histor/.aws" — so a relative name is resolved from the trace itself, never guessed.
_OPEN = re.compile(r" X (?:openat\((?:AT_FDCWD|0x[0-9a-f]+)(?: (/[^,]*))?, |open\()0x[0-9a-f]+ (.+?), (O_[A-Z_|0-9]+(?:\|0x[0-9a-f]+)*)")
_SOCKET = re.compile(r" X socket\((AF_\w+), .*\) = (\d+) ")
# rename(0xb3a320 a, 0xb3a350 b); renameat(0x3 /tmp/w, 0x7e… .bashrc, 0x3, 0xb3a380 c): the second
# directory is printed without its path when it repeats; renameat2's may come as the number
# 0xffffffffffffff9c (AT_FDCWD) with no path; creat(0x7e… /tmp/w/x.pth, 0o644) = 4. Captured from
# runsc's own strace on 2026-10-25.
_FILE_MOVE = re.compile(r" X (renameat2|renameat|rename|linkat|link|symlinkat|symlink|creat)\((.*)\) = (-?\d+|0x[0-9a-f]+)")
_PTR_PATH = re.compile(r"^0x[0-9a-f]+ (.+)$")
_DIR_ARG = re.compile(r"^(AT_FDCWD|0x[0-9a-f]+)(?: (/.*))?$")
_AT_FDCWD_NUM = "0xffffffffffffff9c"
_SOCK_FD = re.compile(r" X (?:connect|sendto|sendmsg|sendmmsg)\((0x[0-9a-f]+) socket:\[(\d+)\]")
# accept4(0x3 socket:[1], 0x7e… {Family: AF_INET, Addr: 127.0.0.1, Port: 41192}, 0x7e… {length=16},
# SOCK_CLOEXEC) = 5 (0x5): gVisor prints the peer on exit, and the new fd as the result.
_ACCEPT = re.compile(r" X accept4?\(0x[0-9a-f]+ socket:\[\d+\], .*\) = (\d+) ")
_ADDRESS = re.compile(r"\{Family: (AF_\w+), Addr: ([^,}]*)(?:, Port: (\d+))?")


def file_move_target(call: str, args: str) -> tuple[str | None, str]:
    """(resolved absolute target or None, the target as printed) for a file-moving call."""
    parts = [p.strip() for p in args.split(", ")]

    def ptr(i: int) -> str:
        m = _PTR_PATH.match(parts[i]) if i < len(parts) else None
        return m.group(1).strip().strip('"') if m else ""

    def directory(i: int, like: int | None = None) -> str | None:
        m = _DIR_ARG.match(parts[i]) if i < len(parts) else None
        if not m:
            return None
        if m.group(2):
            return m.group(2).strip()
        if like is not None:
            # The same descriptor as the first directory, printed once.
            first = _DIR_ARG.match(parts[like]) if like < len(parts) else None
            if first and first.group(1) == m.group(1) and first.group(2):
                return first.group(2).strip()
        return None

    if call in ("rename", "link", "symlink"):
        target, base = ptr(1), None
    elif call == "creat":
        target, base = ptr(0), None
    elif call in ("renameat", "renameat2", "linkat"):
        target, base = ptr(3), directory(2, like=0)
    else:  # symlinkat(target, newdirfd, linkpath)
        target, base = ptr(2), directory(1)
    if not target:
        return None, ""
    if target.startswith("/"):
        return os.path.normpath(target), target
    if base:
        return os.path.normpath(os.path.join(base, target)), target
    return None, target


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
               drop_pid1: bool = False, net: tuple[float, float] | None = None) -> dict[str, Any]:
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
    # socket() prints a family and an fd number; connect() and send print the fd and the socket's
    # inode. The family is handed from the first to the second at the socket's first use, because
    # close() is not traced and the number comes back: gvisor-trace/5 kept the family of a closed
    # TCP socket (or a failed docker.sock connect) for the socketpair end that got its number, and
    # listed asyncio's one-byte wake-ups as sends it could not place.
    socket_families: dict[tuple[str, int], str] = {}
    inode_families: dict[str, str] = {}
    # accept() answers with a new fd and prints who connected; the send that follows names
    # neither. A server answering a loopback client wrote 789 bytes gvisor-trace/6 could not place.
    accepted: dict[tuple[str, int], tuple[str, str, str | None] | None] = {}
    socket_peers: dict[str, tuple[str, str, str | None]] = {}
    # socketpair(2) is traced, but gVisor prints its result array as an address, not the two fds.
    # A socket can leave the container only through a traced connect() or a send that names its
    # destination, so a send that names none, on a socket socket() did not make and no connect()
    # reached, is one end of an in-process pair — asyncio's self-pipe in every Python server.
    pairs_made = False
    dns_sent: set[str] = set()
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
                if " X socketpair(" in line:
                    if re.search(r"\) = 0 ", line):
                        pairs_made = True
                        # Its two fds are printed as an address: any number socket() gave out
                        # earlier and nothing has used yet may be one of them now.
                        for pending in (socket_families, accepted):
                            for key in [k for k in pending if pm and k[0] == pm.group(1)]:
                                del pending[key]
                elif (m := _SOCKET.search(line)):
                    if pm:
                        socket_families[(pm.group(1), int(m.group(2)))] = m.group(1)
                        accepted.pop((pm.group(1), int(m.group(2))), None)
                elif " X accept" in line and (m := _ACCEPT.search(line)):
                    if pm:
                        key = (pm.group(1), int(m.group(1)))
                        socket_families.pop(key, None)
                        peer = _ADDRESS.findall(line)
                        accepted[key] = peer[0] if peer else None
                elif (m := _EXEC.search(line)):
                    path = m.group(1).strip()
                    if pid1:
                        continue  # the entry's own exec chain
                    argv = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))[:6]
                    a["exec"].add(" ".join([os.path.basename(path)] + argv[1:])[:160])
                elif any(f" X {call}(" in line for call in ("connect", "sendto", "sendmsg", "sendmmsg")):
                    addresses = _ADDRESS.findall(line)
                    fd = _SOCK_FD.search(line)
                    if fd and pm and fd.group(2) not in inode_families:
                        key = (pm.group(1), int(fd.group(1), 16))
                        made = socket_families.pop(key, None)
                        if made:
                            inode_families[fd.group(2)] = made
                        elif key in accepted:
                            peer = accepted.pop(key)
                            # No printed peer: a connection from somewhere, never one end of a pair.
                            inode_families[fd.group(2)] = peer[0] if peer else "accepted"
                            if peer:
                                socket_peers[fd.group(2)] = peer
                    family = inode_families.get(fd.group(2)) if fd else None
                    if addresses and fd and " X connect(" in line:
                        socket_peers[fd.group(2)] = addresses[0]
                    if not addresses and fd and fd.group(2) in socket_peers:
                        addresses = [socket_peers[fd.group(2)]]
                    if not addresses and family == "AF_NETLINK":
                        continue  # kernel route/interface enumeration, never an external socket
                    if not addresses and family is None and pairs_made and fd and " X connect(" not in line:
                        continue  # one end of a socketpair: nothing leaves the container through it
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
                        if addr == OBSERVE_GATEWAY and port == "53":
                            dns_sent.add(phase)
                            continue
                        if addr.startswith(("127.", "0.0.0.0")) or addr in ("::1", "::"):
                            continue
                        name = DNS.name_for(addr) if DNS else None
                        a["network"].add(f"{name or addr}:{port}")
                elif (m := _OPEN.search(line)):
                    base, path, flags = m.group(1), m.group(2).strip().strip('"'), m.group(3)
                    if not re.search(r"\) = (?:0x[0-9a-f]+|[0-9]+)(?: |$)", line):
                        continue  # failed opens are attempts, not opened credentials
                    if not path.startswith("/") and base:
                        path = os.path.join(base.strip(), path)
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
                    # A path still relative here is unknown (and already makes the run incomplete),
                    # not a write outside the sandbox: gvisor-trace/2 listed node-gyp's
                    # buildcheck.gypi as persistence that way.
                    if writing and path.startswith("/") and persistent_write(path, phase):
                        a["writes"].add(path[:160])
                elif (m := _FILE_MOVE.search(line)):
                    call, args, result = m.group(1), m.group(2), m.group(3)
                    if result.startswith("-"):
                        continue  # failed: nothing moved
                    target, printed = file_move_target(call, args)
                    if target is None:
                        if not printed:
                            issues.add("unparsed-syscall")
                            a["exec"].add(line.split(" X ", 1)[1][:200])
                            continue
                        # gVisor prints no cwd for these: where it landed is unknown, so the run is
                        # not clean, and a name that persists is reported whatever its directory.
                        issues.add("relative-path-unresolved")
                        name = os.path.basename(printed)
                        if (name.startswith(".") and any(p.startswith(name) for p in PERSIST)) or \
                                name in AUTORUN_NAMES or name.endswith(AUTORUN_SUFFIXES):
                            a["writes"].add(f"{printed[:150]} ({call}, directory unknown)")
                        continue
                    if persistent_write(target, phase):
                        a["writes"].add(f"{target[:150]} ({call})" if call != "creat" else target[:160])
                elif any(f" X {call}(" in line for call in ("sendto", "sendmsg", "sendmmsg", "execveat", "unlinkat", "mkdirat", "chdir", "fchdir")):
                    # Preserve unparsed activity without claiming successful or complete attribution.
                    issues.add("unparsed-syscall")
                    a["network" if "send" in line else "exec"].add(line.split(" X ", 1)[1][:200])
    if not records:
        issues.add("trace-empty")
    for i, (name, since, until) in enumerate(phases):
        if DNS:
            # The last phase runs a little past its mark: the trace and the logger flush late.
            # Names are matched by the container's address, which Docker gives to the next
            # container once this one is gone: never before it started or after it was removed.
            lo, hi = net if net else (since, until + 1)
            lookups[name] = DNS.names(src_ip, max(since, lo), min(until + (1 if i == len(phases) - 1 else 0), hi))
    out: dict[str, Any] = {}
    for name, _, _ in phases:
        if any(len(v) > 40 for v in acts[name].values()) or len(lookups.get(name, [])) > 60:
            issues.add("trace-truncated")
        if DNS and dns_sent and not any(lookups.values()):
            # It asked the resolver and the logger has no name at all (phases compared together:
            # a query at a phase's edge is logged a moment later): an answer that came too late,
            # or a query lost. What it would have reached is unknown, so the run is not clean.
            issues.add("dns-unrecorded")
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


def install(registry: str, name: str, version: str, work: Path, run_id: str, pins: list[str] | None = None,
            image: str | None = None) -> tuple[bool, str]:
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
               "--no-compile", "--target", "/work/pkg", f"{name}=={version}", *(pins or [])]
    try:
        res = docker("run", *flags, image or IMAGES[registry], *cmd, timeout=INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        docker("rm", "-f", f"histor-{run_id}-i")
        return False, "install timed out"
    return res.returncode == 0, tail(res.stderr or res.stdout)


AVOID_COMMAND = re.compile(r"setup|install|config|init|login|logout|auth|doctor|inspect|upgrade|update|migrate")


def pick_command(names: list[str], own: set[str]) -> str:
    """Of the commands a package declares, the one that starts its server: one named for MCP
    before a plain CLI of the package's own name (``memory-mcp`` over ``memory``), the package's
    own name before anything else, never a setup or login helper."""
    def score(name: str) -> int:
        low = re.sub(r"[-_.]+", "-", name).lower()
        return (4 * ("mcp" in low) + 3 * (low in own) + 2 * any(w in low for w in ("server", "serve", "stdio"))
                - 5 * bool(AVOID_COMMAND.search(low)))
    return max(names, key=lambda n: (score(n), -names.index(n)))


def npm_entry(work: Path, name: str) -> tuple[list[str], str] | None:
    """(command, what it is) for the package's bin, or None when it declares none."""
    manifest = work / "pkg" / "node_modules" / name / "package.json"
    try:
        pkg = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bins = pkg.get("bin")
    short = name.split("/")[-1]
    if isinstance(bins, str):
        target, label = bins, short
    elif isinstance(bins, dict) and bins and all(isinstance(v, str) for v in bins.values()):
        label = pick_command(list(bins), {short.lower(), name.lower()})
        target = bins[label]
    else:
        return None
    rel = os.path.normpath(target)
    if rel.startswith("..") or os.path.isabs(rel):
        return None  # a bin pointing outside its own package
    return ["node", f"/work/pkg/node_modules/{name}/{rel}"], f"bin {label}"


def main_module(info: Path, want: str) -> str | None:
    """The module a package that declares no command is started as (``python -m``, as its README
    and ``uvx --from`` say): a top-level package's ``__main__.py``, else a top-level module named
    for the distribution, else that package's ``server.py`` or ``main.py`` — a server written with
    ``if __name__ == "__main__": mcp.run()``. A library run this way imports and exits."""
    try:
        lines = (info / "RECORD").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    ident = re.compile(r"[A-Za-z_]\w{0,99}")
    own = want.replace("-", "_").lower()
    mains: list[str] = []
    modules: list[str] = []
    for line in lines:
        parts = line.split(",", 1)[0].split("/")
        if len(parts) == 2 and ident.fullmatch(parts[0]) and parts[0] not in ("tests", "test"):
            if parts[1] == "__main__.py" and parts[0] not in mains:
                mains.append(parts[0])
            elif parts[1] in ("server.py", "main.py") and parts[0].lower() == own:
                modules.append(f"{parts[0]}.{parts[1][:-3]}")
        elif len(parts) == 1 and parts[0].endswith(".py") and ident.fullmatch(parts[0][:-3]) and parts[0][:-3].lower() == own:
            modules.insert(0, parts[0][:-3])
    if mains:
        return max(mains, key=lambda m: (m.lower() == own, "mcp" in m.lower(), -mains.index(m)))
    return sorted(modules, key=lambda m: (m.count("."), not m.endswith(".server")))[0] if modules else None


def pypi_entry(work: Path, name: str) -> tuple[list[str], str] | None:
    """(command, what it is): the package's console script, or ``python -m`` its own module."""
    want = re.sub(r"[-_.]+", "-", name).lower()
    scripts: dict[str, str] = {}
    module_info: Path | None = None
    for info in (work / "pkg").glob("*.dist-info"):
        dist = re.sub(r"[-_.]+", "-", info.name.removesuffix(".dist-info").rsplit("-", 1)[0]).lower()
        if dist != want:
            continue
        module_info = info
        ep = info / "entry_points.txt"
        if not ep.is_file():
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
        module = main_module(module_info, want) if module_info else None
        if not module:
            return None
        return ["python", "-c", f"import runpy; runpy.run_module({module!r}, run_name='__main__', alter_sys=True)"], f"python -m {module}"
    label = pick_command(list(scripts), {want, name.lower()})
    module, _, attr = scripts[label].partition(":")
    if not re.fullmatch(r"[A-Za-z_][\w.]*", module) or (attr and not re.fullmatch(r"[A-Za-z_][\w.]*", attr)):
        return None
    call = f"import sys, importlib; m = importlib.import_module({module!r})"
    call += f"; f = m\nfor p in {attr!r}.split('.'):\n    f = getattr(f, p)\nsys.exit(f())" if attr else ""
    return ["python", "-c", call], f"script {label}"


class Session:
    """MCP over the container's stdio: newline-delimited JSON-RPC."""

    def __init__(self, proc: subprocess.Popen[bytes], deadline: float) -> None:
        self.proc = proc
        self.deadline = deadline
        self.buf = b""
        self.read = 0
        self.noise = b""  # what it printed on stdout that is not the protocol: often why it exits
        self.modern = True  # the first request is the modern discovery probe
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

    def keep(self, raw: bytes) -> None:
        if len(self.noise) < MAX_NOISE and raw.strip():
            self.noise = (self.noise + raw[:MAX_NOISE] + b"\n")[:MAX_NOISE]

    def call(self, rid: int, method: str, params: dict[str, Any], wait_s: float) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        for raw in self._lines(time.monotonic() + wait_s):
            try:
                msg = json.loads(raw)
            except ValueError:
                self.keep(raw)
                continue  # a server that logs to stdout: not a protocol message
            if not isinstance(msg, dict):
                self.keep(raw)
                continue
            error = msg.get("error")
            if self.modern and ("result" in msg or "error" in msg):
                if msg.get("jsonrpc") != "2.0" or ("result" in msg) == ("error" in msg):
                    raise ProtocolFailure("invalid modern JSON-RPC response envelope; no downgrade")
                if "error" in msg and (not isinstance(error, dict) or type(error.get("code")) is not int):
                    raise ProtocolFailure("invalid modern JSON-RPC error code; no downgrade")
                if msg.get("id") is None and "error" in msg:
                    return msg  # protocol errors may omit an id; never misclassify as silence
                if type(msg.get("id")) is not int or msg["id"] != rid:
                    raise ProtocolFailure("modern response did not match the request id; no downgrade")
            if type(msg.get("id")) is int and msg["id"] == rid and ("result" in msg or "error" in msg):
                return msg
            if "method" in msg and "id" in msg:  # the server asks us something: decline, never act
                if self.modern:
                    # Modern MCP carries interactions in input_required results. Do not send a
                    # JSON-RPC response, supply roots, or execute a request from the server.
                    raise ProtocolFailure("unexpected modern server request; no client interaction supported")
                reply = {"roots": []} if msg["method"] == "roots/list" else None
                self.send({"jsonrpc": "2.0", "id": msg["id"], **({"result": reply} if reply is not None else
                          {"error": {"code": -32601, "message": "not supported by the observer"}})})
        raise EOFError


def trace_syscall_gaps(runtimes_json: str) -> list[str]:
    """Syscalls the trace runtime does not record, from `docker info`'s runtimes: every one of
    TRACED_SYSCALLS must be in its --strace-syscalls, or a rename onto .bashrc is invisible."""
    try:
        args = json.loads(runtimes_json).get(TRACE_RUNTIME, {}).get("runtimeArgs") or []
    except (ValueError, AttributeError):
        return list(TRACED_SYSCALLS)
    listed = {c for a in args if a.startswith("--strace-syscalls=") for c in a.split("=", 1)[1].split(",")}
    if any(a == "--strace" for a in args) and not any(a.startswith("--strace-syscalls=") for a in args):
        return []  # --strace with no filter records every call
    return [c for c in TRACED_SYSCALLS if c not in listed]


def traced_available() -> bool:
    """The tracing runtime (recording every call the parser needs), the internal network and
    the DNS logger are all there."""
    runtimes = docker("info", "--format", "{{json .Runtimes}}").stdout
    if trace_syscall_gaps(runtimes):
        return False
    try:
        health = Path("/run/histor-firewall/healthy.json")
        verified = json.loads(health.read_text())
        if health.stat().st_uid != 0 or verified.get("trapRedirects") != 1 or not 0 <= time.time() - verified["verifiedAt"] <= 65:
            return False
        net = json.loads(docker("network", "inspect", OBSERVE_NETWORK).stdout)[0]
        return TRACE_RUNTIME in runtimes and DNS is not None and SINK is not None and net["Internal"] and not net.get("EnableIPv6")
    except (OSError, ValueError, KeyError, IndexError):
        return False


def identity(work: Path) -> list[str]:
    """/etc/passwd and /etc/group that name the run's uid. Without an entry, Node's os.userInfo()
    throws (uv_os_get_passwd ENOENT) and Python's getpass fails: a user's own machine always has
    one, so a package that asks who is running it must not die here for it."""
    uid, gid = os.getuid(), os.getgid()
    etc = work / "etc"
    etc.mkdir(exist_ok=True)
    (etc / "passwd").write_text(f"root:x:0:0:root:/root:/sbin/nologin\nhistor:x:{uid}:{gid}:histor:{HOME_IN}:/bin/sh\n"
                                "nobody:x:65534:65534:nobody:/nonexistent:/sbin/nologin\n", encoding="utf-8")
    (etc / "group").write_text(f"root:x:0:\nhistor:x:{gid}:\nnogroup:x:65534:\n", encoding="utf-8")
    return ["-v", f"{etc / 'passwd'}:/etc/passwd:ro", "-v", f"{etc / 'group'}:/etc/group:ro",
            "-e", "USER=histor", "-e", "LOGNAME=histor"]


def sandbox_flags(run_id: str, work: Path, traced: bool, cidfile: Path, *, package_rw: bool = False) -> list[str]:
    flags = [*base_flags(run_id, TRACE_RUNTIME if traced else RUNTIME), *identity(work), "--read-only", "--memory", "512m", "--memory-swap", "512m",
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
    captured = SINK.phase(ip, since, until, tag) if SINK else {"complete": False, "incompleteReasons": ["sink-unavailable"]}
    complete = section.get("complete") is True and captured.pop("complete") is True and not captured.get("truncated")
    # The trap's reasons join the trace's: a run the observer could not fully account for says every
    # reason why ("dns-unrecorded" and "foreign-run-traffic" together), and stays apart from a clean one.
    reasons = {*section.get("incompleteReasons", []), *captured.pop("incompleteReasons", [])}
    section.update(captured)
    section["complete"] = complete
    section["incompleteReasons"] = sorted(reasons)


def container_started(cid: str | None) -> float | None:
    """When the container actually started (Docker's State.StartedAt), as epoch seconds.

    The traps and the DNS logger know a run only by its address on the internal network, and
    Docker gives a freed address to the next container. A window that opened before `docker run`
    once took in a red-team fixture's last request from the container that had the address a
    moment earlier, and a real package was shown sending decoy data to an extra recipient."""
    if not cid:
        return None
    try:
        res = docker("inspect", "--format", "{{.State.StartedAt}}", cid, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z", res.stdout.strip())
    if res.returncode != 0 or not m:
        return None
    import calendar
    return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) + float("0" + (m.group(2) or "")[:7])


def _attribution(started: float | None, fallback: float, sections: list[dict[str, Any]]) -> float:
    """The earliest moment evidence can be this container's; without a start time, not clean."""
    if started is None:
        for section in sections:
            section["complete"] = False
            section["incompleteReasons"] = sorted({*section.get("incompleteReasons", []), "start-time-unknown"})
        return fallback
    return max(fallback, started)


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


def run_scripts(work: Path, run_id: str, packages: list[str], image: str | None = None) -> dict[str, Any]:
    """npm install scripts, run on their own under the trace: what installing would have done."""
    cidfile = work / "cid-scripts"
    flags = [*sandbox_flags(run_id + "-s", work, True, cidfile, package_rw=True), "-w", "/work/pkg"]
    # npm itself reads ~/.npmrc and checks for updates. Keep its configuration
    # separate from the planted credential; package lifecycle code still sees
    # the decoy at HOME/.npmrc and all its reads remain observable.
    flags += ["-e", "NPM_CONFIG_USERCONFIG=/dev/null", "-e", "NPM_CONFIG_UPDATE_NOTIFIER=false"]
    since = time.time()
    proc = subprocess.Popen([DOCKER, "run", *flags, image or IMAGES["npm"], "npm", "rebuild", "--foreground-scripts", "--no-audit", "--no-fund"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cid, ip, started = None, None, None
    timed_out = False
    try:
        cid, ip = container_ip(cidfile)
        started = container_started(cid)  # while it exists: inspect knows nothing once it is removed
        proc.wait(timeout=SCRIPTS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        docker("rm", "-f", f"histor-{run_id}-s", timeout=30)
        proc.wait(timeout=10)
    until = time.time()  # after `docker rm`: the address cannot be anyone else's before this
    section = read_trace(cid, [("installScripts", since, until)], "npm", ip,
                         net=(started or since, until))["installScripts"] if cid else {"complete": False, "incompleteReasons": ["container-id-missing"]}
    merge_sink(section, ip, _attribution(started, since, [section]), until, run_id)
    return {"packages": packages, **section, "exitCode": proc.returncode, "timedOut": timed_out,
            "complete": section.get("complete") is True and not timed_out and proc.returncode == 0}


def run(registry: str, work: Path, entry: list[str], run_id: str, traced: bool = False,
        env: list[str] | None = None, args: list[str] | None = None, image: str | None = None) -> dict[str, Any]:
    cidfile = work / "cid-run"
    # The package directory is writable, as an install on a user's machine is: servers keep logs
    # and state next to their code, and it is this run's own copy, deleted with it.
    flags = [*sandbox_flags(run_id + "-r", work, traced, cidfile, package_rw=True), "-i", "-w", APP_IN]
    for name in env or []:
        flags += ["-e", f"{name}={env_value(name, run_id)}"]
    entry = [*entry, *(env_value("PACKAGE_API_KEY", run_id) if arg == PLACEHOLDER_TOKEN else arg
                       for arg in (args or []))]
    if registry == "pypi":
        flags += ["-e", "PYTHONPATH=/work/pkg", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    start, wall = time.monotonic(), time.time()
    proc = subprocess.Popen([DOCKER, "run", *flags, image or IMAGES[registry], *entry],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    cid, ip, started = None, None, None
    marks: dict[str, Any] = {}
    try:
        cid, ip = container_ip(cidfile) if traced else (None, None)
        started = container_started(cid)  # while it exists: inspect knows nothing once it is removed
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
        answer["behaviour"] = read_trace(cid, [("startup", wall, listed), ("calls", listed, ended + 2)], entry[0], ip,
                                         net=(started or wall, ended))
        lo = _attribution(started, wall, [answer["behaviour"]["startup"], answer["behaviour"]["calls"]])
        merge_sink(answer["behaviour"]["startup"], ip, lo, listed, run_id)
        merge_sink(answer["behaviour"]["calls"], ip, listed, ended, run_id)
        calls = answer["behaviour"]["calls"]
        calls.update(marks.get("coverage", {"successful": 0, "skipped": len(answer.get("tools", []))}))
        calls["complete"] = calls.get("complete") is True and marks.get("callsComplete") is True
    else:
        answer["behaviour"] = {p: {"complete": False, "incompleteReasons": ["trace-unavailable"]} for p in ("startup", "calls")}
    return {**answer, "seconds": round(time.monotonic() - start, 1)}


class ProtocolFailure(Exception):
    """An incomplete or incompatible MCP exchange, not an empty successful observation."""


def modern_params(params: dict[str, Any] | None = None, version: str = MODERN_PROTOCOL) -> dict[str, Any]:
    return {**(params or {}), "_meta": {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "histor-sandbox", "version": VERSION},
    }}


def complete_result(reply: dict[str, Any], method: str) -> dict[str, Any]:
    if "error" in reply:
        raise ProtocolFailure(tail(json.dumps(reply["error"])))
    body = reply.get("result")
    if not isinstance(body, dict):
        raise ProtocolFailure(f"{method}: malformed result")
    kind = body.get("resultType", "complete")
    if kind != "complete":
        raise ProtocolFailure(f"{method}: {kind}; observation incomplete (no client interaction)")
    return body


def negotiate(session: Session) -> tuple[dict[str, Any], str | None, bool]:
    """One modern probe, at most one advertised-version retry, then legacy only if indicated.

    https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
    A recognized modern error must never lead to initialize, even if its supported list
    advertises legacy versions. We implement one modern version, not arbitrary future versions.
    """
    session.modern = True
    try:
        probe = session.call(0, "server/discover", modern_params(), DISCOVER_TIMEOUT_S)
    except TimeoutError:
        probe = None
    if probe is not None:
        error = probe.get("error")
        if "error" not in probe or (isinstance(error, dict) and error.get("code") in MODERN_ERRORS):
            if "error" in probe:
                if error.get("code") != -32022:
                    raise ProtocolFailure(tail(json.dumps(error)))
                data = error.get("data")
                supported = data.get("supported") if isinstance(data, dict) else None
                if not isinstance(supported, list) or MODERN_PROTOCOL not in supported:
                    raise ProtocolFailure("server/discover: no mutually supported modern protocol version")
                probe = session.call(-1, "server/discover", modern_params(), DISCOVER_TIMEOUT_S)
            body = complete_result(probe, "server/discover")
            versions = body.get("supportedVersions")
            if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
                raise ProtocolFailure("server/discover: malformed supportedVersions")
            if MODERN_PROTOCOL not in versions:
                raise ProtocolFailure("server/discover: no mutually supported modern protocol version")
            return body, MODERN_PROTOCOL, True
    # Legacy implementations use different errors (or silence) before initialize. Do not
    # couple this fallback to -32601 alone, and keep the original process and outer deadline.
    session.modern = False
    init = session.call(1, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": "histor-sandbox", "version": VERSION}},
                        INIT_TIMEOUT_S)
    info = complete_result(init, "initialize")
    return info, info.get("protocolVersion"), False


def talk(proc: subprocess.Popen[bytes], start: float, tag: str = "", traced: bool = False,
         marks: dict[str, Any] | None = None) -> dict[str, Any]:
    marks = marks if marks is not None else {}
    session = Session(proc, start + RUN_TIMEOUT_S)
    result: dict[str, Any] = {}
    try:
        info, version, modern = negotiate(session)
        meta = info.get("_meta") if isinstance(info.get("_meta"), dict) else {}
        result["serverInfo"] = meta.get("io.modelcontextprotocol/serverInfo") if modern else info.get("serverInfo")
        result["protocolVersion"] = version
        if not modern:
            session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[Any] = []
        cursor = None
        for page in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            listed = session.call(2 + page, "tools/list", modern_params(params, version) if modern else params, RUN_TIMEOUT_S)
            body = complete_result(listed, "tools/list")
            page_tools = body.get("tools")
            if not isinstance(page_tools, list):
                raise ProtocolFailure("tools/list: malformed tools; observation incomplete")
            tools.extend(page_tools)
            if len(tools) > MAX_TOOLS:
                return {**result, "status": "too-many-tools", "detail": f"more than {MAX_TOOLS} tools"}
            cursor = body.get("nextCursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor:
                raise ProtocolFailure("tools/list: invalid pagination cursor; observation incomplete")
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
                    params = {"name": tool["name"], "arguments": arguments}
                    reply = session.call(10_000 + i, "tools/call", modern_params(params, version) if modern else params, CALL_TIMEOUT_S)
                    valid = (isinstance(reply.get("result"), dict)
                             and reply["result"].get("resultType", "complete") == "complete"
                             and isinstance(reply["result"].get("content"), list))
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
    except ProtocolFailure as exc:
        return {**result, "status": "protocol", "detail": str(exc)}
    except TimeoutError:
        return {**result, "status": "timeout", "detail": ended_with(proc, session, exited=False)}
    except EOFError:
        # Exited before answering: most often a server that needs an API key or a path to start.
        return {**result, "status": "exited", "detail": ended_with(proc, session, exited=True)}
    except OverflowError:
        return {**result, "status": "too-large", "detail": f"more than {MAX_OUTPUT} bytes on stdout"}


def ended_with(proc: subprocess.Popen[bytes], session: Any, *, exited: bool) -> str:
    """What a server that did not answer left behind: its exit code (when it exited on its own),
    stderr, and the stdout that was not protocol — a CLI prints its usage there, and a server
    that logs to stdout says there why it stopped. Without stdout, those read as "silent"."""
    if exited and proc is not None:
        try:
            proc.wait(timeout=5)  # stdout closes a moment before docker run reports the exit
        except subprocess.TimeoutExpired:
            pass
    err = stderr_of(proc)
    noise = getattr(session, "noise", b"") + (getattr(session, "buf", b"") if exited else b"")
    out = head_tail(noise.decode("utf-8", "replace"), 300, 300) if noise.strip() else ""
    code = proc.returncode if exited and proc is not None else None
    parts = [f"[exit code {code}]"] if isinstance(code, int) and code >= 0 else []
    parts += [err] if err else []
    parts += [f"stdout: {out}"] if out else []
    return " ".join(parts)


def stderr_of(proc: subprocess.Popen[bytes]) -> str:
    try:
        proc.kill()
        _, err = proc.communicate(timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    return head_tail((err or b"").decode("utf-8", "replace"))


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


def image_digest(registry: str, image: str | None = None) -> str | None:
    """The image a run used: its registry digest, or — for an image built on this host, which has
    none — its tag and content id."""
    tag = image or IMAGES[registry]
    res = docker("image", "inspect", "--format", "{{json .RepoDigests}} {{.Id}}", tag)
    try:
        digests, _, ident = res.stdout.strip().partition(" ")
        repo = json.loads(digests) if digests else []
    except ValueError:
        return None
    if repo:
        return repo[0]
    return f"{tag}@{ident}" if ident.startswith("sha256:") else None


def observe(registry: str, name: str, version: str | None, env: list[str] | None = None,
            args: list[str] | None = None, pins: list[str] | None = None, runtime: str | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {"type": "histor.package-observation/v1", "observer": VERSION, "registry": registry,
                           "name": name, "runtime": RUNTIME, "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "supplied": {"env": list(env or []), "args": list(args or []), "pins": list(pins or []),
                                        **({"runtime": runtime} if runtime else {})}}
    try:
        meta = resolve(registry, name, version)
    except Exception as exc:  # noqa: BLE001 - every failure is a status, never a crash
        return {**doc, "status": "not-found", "detail": tail(f"{type(exc).__name__}: {exc}")}
    if registry == "pypi" and runtime is None:
        runtime, runnable = python_for(meta.get("requires_python"))
        if not runnable:
            return {**doc, "version": meta["version"], "status": "install-failed",
                    "detail": tail(f"Requires-Python {meta.get('requires_python')}: none of the sandbox's Python images "
                                   f"({', '.join('.'.join(map(str, v)) for _, v in PYTHONS)}) is admitted")}
    image = image_for(registry, runtime)
    doc.update(version=meta["version"], integrity=meta.get("integrity"), image=image_digest(registry, image), imageTag=image)
    if registry == "pypi" and not meta.get("has_wheel"):
        return {**doc, "status": "no-wheel", "detail": "only an sdist: building it would run the package's code"}
    lock = slot()
    run_id = secrets.token_hex(6)
    work = STATE / "runs" / run_id
    try:
        (work / "pkg").mkdir(parents=True)
        (work / ".home").mkdir()
        plant_decoys(work, run_id)
        ok, log = install(registry, name, meta["version"], work, run_id, pins, image)
        if not ok:
            return {**doc, "status": "install-failed", "detail": log}
        traced = traced_available()
        pkgs = lifecycle_packages(work) if registry == "npm" else []
        scripts = (run_scripts(work, run_id, pkgs, image) if traced else {"complete": False, "incompleteReasons": ["trace-unavailable"]}) if pkgs else {"complete": True, "status": "not-applicable", "packages": []}
        found = npm_entry(work, name) if registry == "npm" else pypi_entry(work, name)
        if found:
            entry, doc["entry"] = found
        result = run(registry, work, entry, run_id, traced, env, args, image) if found else {
            "status": "no-entry-point", "detail": "the package declares no command to start" + ("" if registry == "npm" else ", and has no __main__ module")}
        behaviour = result.setdefault("behaviour", {})
        behaviour["installScripts"] = scripts
        behaviour["observer"] = TRACE_OBSERVER
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
            "trace": TRACE_RUNTIME in info, "traceSyscallGaps": trace_syscall_gaps(info),
            "dnsLogger": DNS is not None,
            "observeNetwork": docker("network", "inspect", OBSERVE_NETWORK).returncode == 0,
            "images": {k: image_digest(k) for k in IMAGES},
            "runtimeImages": {f"{r}:{n}": image_digest(r, img) for r, by in RUNTIMES.items() for n, img in by.items()}}


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
                    config = check_config(body.get("env"), body.get("args"), body.get("pins"), req[1], body.get("runtime"))
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
                              else observe(req[1], req[2], req[3] if len(req) > 3 else None, *config))
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
