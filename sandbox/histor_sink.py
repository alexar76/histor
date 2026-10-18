"""Local protocol trap. No forwarding code, upstream sockets or real credentials.

DNS redirects the isolated container to this host. Records are attributed by source
IP and phase timestamps. Fake HTTP/SMTP successes let code reach later branches;
unparseable or oversized payloads explicitly leave coverage incomplete.
"""

from __future__ import annotations

import hashlib
import json
import re
import socketserver
import ssl
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LIMIT = 256 * 1024
EMAIL = re.compile(r'[^\s<>,;"\[\]]+@[^\s<>,;"\[\]]+')
RECIPIENT_KEYS = {
    "to",
    "cc",
    "bcc",
    "recipient",
    "recipients",
    "destination",
    "destinations",
    "personalizations",
}


# The run a trap record belongs to, read from its content: every placeholder HISTOR plants or passes
# carries the run's 12-hex tag (canary-<tag>@histor-trap.invalid, histor-canary-ssh-<tag>,
# histor-envkey-<tag>, env-<tag>.histor-trap.invalid ...). Docker hands a freed address to the next
# container, and a connection the previous holder queued can be accepted after the next one started:
# matched by address and time alone, that run would inherit the other's canary.
RUN_TAG = re.compile(
    rb"(?:histor-canary-[a-z0-9]+-|canary-|histor-envkey-|env-|historcanary|histor-trap\.invalid/|histor canary (?:note )?)"
    rb"([0-9a-f]{12})(?![0-9a-f])"
)


def run_tags(payload: bytes, recipient_list) -> list[str]:
    found = {m.group(1).decode() for m in RUN_TAG.finditer(payload)}
    for r in recipient_list or []:
        found.update(m.group(1).decode() for m in RUN_TAG.finditer(str(r).encode(errors="ignore")))
    return sorted(found)


def recipients(value, active=False):
    found = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.update(recipients(v, active or str(k).lower() in RECIPIENT_KEYS))
    elif isinstance(value, list):
        for v in value:
            found.update(recipients(v, active))
    elif active and isinstance(value, str):
        found.update(EMAIL.findall(value))
    return found


class Collector:
    def __init__(self):
        self.records = deque(maxlen=100_000)
        self.lock = threading.Lock()
        self.lost_at = 0.0

    def record(self, source, protocol, destination, body=b"", *, recipient_list=None, complete=True, opened=None):
        if len(body) > LIMIT:
            complete = False
        payload = body[:LIMIT]
        if recipient_list is None:
            try:
                recipient_list = sorted(recipients(json.loads(payload))) if payload else []
            except (ValueError, UnicodeError, RecursionError):
                recipient_list, complete = [], False
        if len(recipient_list) > 40:
            complete = False
        recipient_list = [str(value)[:320] for value in recipient_list[:40]]
        tags = run_tags(payload, recipient_list)
        now = time.time()
        record = {
            "at": now,
            # When the connection was accepted: its sender was alive then. A record is finished a
            # moment later, possibly after that container is gone and its address reused.
            "opened": opened if opened is not None else now,
            "source": source,
            "protocol": protocol,
            "destination": str(destination)[:300],
            "recipients": recipient_list,
            "payloadSha256": hashlib.sha256(payload).hexdigest(),
            "complete": complete,
            "runTags": tags[:8],
            # A placeholder key HISTOR set because the server asked for one: where it went is a
            # fact to show (its own API, or somewhere else), not a decoy theft.
            "envKeySent": b"histor-envkey-" in payload,
            "decoyTransmitted": b"HISTOR_DECOY" in payload
            or b"histor-decoy-" in payload
            or b"histor-canary-" in payload,
        }
        with self.lock:
            if len(self.records) == self.records.maxlen:
                self.lost_at = record["at"]
            self.records.append(record)

    def phase(self, source, since, until, tag):
        if not source:
            return {"complete": False, "incompleteReasons": ["sink-source-missing"]}
        with self.lock:
            rows = [dict(r) for r in self.records if r["source"] == source and since <= r.get("opened", r["at"]) <= until]
            lost = self.lost_at > 0 and self.lost_at >= since
        # A record that carries only other runs' tags was sent by whoever held this address before: it
        # is not this run's evidence (no recipient, decoy or key destination of its own), and the run is
        # not clean either — the observer saw traffic on this address it cannot account to this run.
        # A record that carries this run's tag and another's stays this run's, and is flagged the same way.
        foreign = [r for r in rows if set(r.get("runTags") or []) - {tag}]
        rows = [r for r in rows if not (r.get("runTags") and tag not in r["runTags"])]
        reasons = ["foreign-run-traffic"] if foreign else []
        expected = f"canary-{tag}@histor-trap.invalid"
        unexpected = sorted({r for row in rows for r in row["recipients"] if r.lower() != expected})
        return {
            "complete": not lost and not foreign and all(r["complete"] for r in rows),
            "incompleteReasons": reasons,
            "foreignRequests": len(foreign),
            "requests": [{k: v for k, v in row.items() if k not in ("source", "at", "opened")} for row in rows[:100]],
            "unexpectedRecipients": unexpected[:40],
            "decoyTransmitted": any(r["decoyTransmitted"] for r in rows),
            "envKeyDestinations": sorted({row["destination"] for row in rows if row.get("envKeySent")})[:20],
            "truncated": len(rows) > 100,
        }


class LimitedThreads:
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.collector.record(client_address[0], "overload", "", complete=False)
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class HTTPTrap(LimitedThreads, ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        self.collector.record(client_address[0], "http", "connection-incomplete", complete=False)


class HTTPHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.opened = time.time()
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.server.collector.record(self.client_address[0], "http", "unsupported-request", complete=False,
                                     opened=getattr(self, "opened", None))
        super().send_error(code, message, explain)

    def do_request(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            complete = 0 <= size <= LIMIT and not self.headers.get("Transfer-Encoding")
            body = self.rfile.read(size) if complete else b""
            complete = complete and len(body) == size
        except (ValueError, OSError):
            body, complete = b"", False
        self.server.collector.record(
            self.client_address[0],
            "https" if isinstance(self.connection, ssl.SSLSocket) else "http",
            self.headers.get("Host", "") + self.path,
            body,
            complete=complete,
            opened=getattr(self, "opened", None),
        )
        # Common Postmark success shape; other protocols are never represented as real provider service.
        response = (
            b'{"ErrorCode":0,"Message":"OK","MessageID":"histor-synthetic","ok":true,"id":"histor-synthetic"}'
        )
        self.send_response(200 if complete else 413)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_request


class SMTPTrap(LimitedThreads, socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        opened = time.time()
        self.connection.settimeout(5)
        rcpts, data = [], b""
        self.wfile.write(b"220 histor synthetic SMTP\r\n")
        try:
            for _ in range(100):
                line = self.rfile.readline(8193)
                if not line or len(line) > 8192:
                    break
                command = line.split(b" ", 1)[0].upper().strip()
                if command in (b"EHLO", b"HELO"):
                    self.wfile.write(b"250 histor\r\n")
                elif command == b"RCPT":
                    rcpts.extend(EMAIL.findall(line.decode("utf8", "replace")))
                    self.wfile.write(b"250 OK\r\n")
                elif command == b"DATA":
                    self.wfile.write(b"354 end with dot\r\n")
                    complete = False
                    for _ in range(10000):
                        line = self.rfile.readline(8193)
                        if line == b".\r\n":
                            complete = True
                            break
                        data += line
                        if not line or len(data) > LIMIT:
                            break
                    self.server.collector.record(
                        self.client_address[0], "smtp", "smtp", data, recipient_list=rcpts, complete=complete,
                        opened=opened,
                    )
                    self.wfile.write(b"250 queued synthetically\r\n")
                    rcpts, data = [], b""
                elif command == b"QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    return
                elif command in (b"MAIL", b"RSET", b"NOOP"):
                    self.wfile.write(b"250 OK\r\n")
                else:
                    self.server.collector.record(
                        self.client_address[0], "smtp", "unsupported-command", complete=False, opened=opened
                    )
                    self.wfile.write(b"502 unsupported\r\n")
        except OSError:
            self.server.collector.record(self.client_address[0], "smtp", "incomplete", complete=False, opened=opened)


class Certificates:
    """Dedicated trap CA: its private key is never mounted into a package container."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cache = {}
        if not (path / "ca.pem").exists():
            self.run(
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "365",
                "-subj",
                "/CN=HISTOR trap only",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-keyout",
                str(path / "ca.key"),
                "-out",
                str(path / "ca.pem"),
            )
        (path / "ca.key").chmod(0o600)

    def run(self, *args):
        subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=15)

    def context(self, host):
        if not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host):
            raise ValueError("invalid SNI")
        with self.lock:
            if host in self.cache:
                return self.cache[host]
            if len(self.cache) >= 128:
                self.cache.clear()
            prefix = self.path / hashlib.sha256(host.encode()).hexdigest()
            ext = prefix.with_suffix(".ext")
            ext.write_text(f"subjectAltName=DNS:{host}\n")
            key, csr, cert = (str(prefix.with_suffix(x)) for x in (".key", ".csr", ".pem"))
            self.run(
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                f"/CN={host}",
                "-keyout",
                key,
                "-out",
                csr,
            )
            Path(key).chmod(0o600)
            self.run(
                "x509",
                "-req",
                "-in",
                csr,
                "-CA",
                str(self.path / "ca.pem"),
                "-CAkey",
                str(self.path / "ca.key"),
                "-set_serial",
                str(int(hashlib.sha256(host.encode()).hexdigest()[:30], 16)),
                "-days",
                "30",
                "-extfile",
                str(ext),
                "-out",
                cert,
            )
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.cache[host] = ctx
            for suffix in (".key", ".csr", ".pem", ".ext"):
                prefix.with_suffix(suffix).unlink(missing_ok=True)
            return ctx


def start(host, state):
    collector = Collector()
    certs = Certificates(state / "trap-ca")
    servers = []
    # Host web/mail services commonly bind 0.0.0.0 on the standard ports. The
    # root-owned firewall redirects ONLY the sandbox bridge to these listeners.
    for port in (18080, 18443):
        server = HTTPTrap((host, port), HTTPHandler)
        server.collector = collector
        if port == 18443:
            ctx = certs.context("histor-trap.invalid")

            def sni(sock, name, initial):
                if name:
                    sock.context = certs.context(name)

            ctx.set_servername_callback(sni)
            server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        servers.append(server)
    for port in (18025, 18587):
        server = SMTPTrap((host, port), SMTPHandler)
        server.collector = collector
        servers.append(server)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return collector
