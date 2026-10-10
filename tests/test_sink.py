"""Protocol fixtures prove hidden copies appear in payload evidence, not just DNS."""

import importlib.util
import json
import smtplib
import threading
import urllib.request
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "histor_sink", Path(__file__).parents[1] / "sandbox/histor_sink.py"
)
sink = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sink)


def test_http_postmark_bcc_and_isolation():
    collector = sink.Collector()
    server = sink.HTTPTrap(("127.0.0.1", 0), sink.HTTPHandler)
    server.collector = collector
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        payload = json.dumps(
            {
                "To": "canary-fixture@histor-trap.invalid",
                "Bcc": "copy@attacker.invalid",
                "TextBody": "fake data",
            }
        ).encode()
        response = urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{server.server_port}/email", data=payload), timeout=3
        )
        assert json.load(response)["ErrorCode"] == 0
        phase = collector.phase("127.0.0.1", 0, 9999999999, "fixture")
        assert phase["unexpectedRecipients"] == ["copy@attacker.invalid"] and phase["complete"]
        assert collector.phase("other", 0, 9999999999, "fixture")["requests"] == []
        assert not collector.phase(None, 0, 9999999999, "fixture")["complete"]
    finally:
        server.shutdown()
        server.server_close()


def test_smtp_hidden_copy_and_unparseable_http():
    collector = sink.Collector()
    server = sink.SMTPTrap(("127.0.0.1", 0), sink.SMTPHandler)
    server.collector = collector
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with smtplib.SMTP("127.0.0.1", server.server_address[1], timeout=3) as smtp:
            smtp.sendmail(
                "sender@fixture.invalid",
                ["canary-fixture@histor-trap.invalid", "copy@attacker.invalid"],
                "Subject: test\n\nhistor-canary-ssh-fixture",
            )
        phase = collector.phase("127.0.0.1", 0, 9999999999, "fixture")
        assert phase["unexpectedRecipients"] == ["copy@attacker.invalid"] and phase["decoyTransmitted"]
        collector.record("127.0.0.1", "http", "fixture", b"opaque compressed payload")
        assert not collector.phase("127.0.0.1", 0, 9999999999, "fixture")["complete"]
    finally:
        server.shutdown()
        server.server_close()


def test_tls_payload_capture_with_private_trap_ca(tmp_path):
    import ssl

    certs = sink.Certificates(tmp_path)
    collector = sink.Collector()
    server = sink.HTTPTrap(("127.0.0.1", 0), sink.HTTPHandler)
    server.collector = collector
    server.socket = certs.context("localhost").wrap_socket(
        server.socket, server_side=True, do_handshake_on_connect=False
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ctx = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
        data = json.dumps({"to": "canary-test@histor-trap.invalid", "bcc": "copy@extra.invalid"}).encode()
        with urllib.request.urlopen(
            urllib.request.Request(f"https://localhost:{server.server_port}/email", data=data),
            context=ctx,
            timeout=5,
        ) as response:
            assert response.status == 200
        assert collector.phase("127.0.0.1", 0, 9999999999, "test")["unexpectedRecipients"] == [
            "copy@extra.invalid"
        ]
    finally:
        server.shutdown()
        server.server_close()
