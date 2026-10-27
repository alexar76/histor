"""Capture writes an unsigned local snapshot without operator settings or package execution."""

import json

import pytest

from histor import __main__ as cli
from histor import upgrade_capture
from histor.mcpclient import Observation


def fake_reader(monkeypatch, observation):
    calls = []

    class Reader:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def list_tools(self, url):
            calls.append(url)
            return observation

    monkeypatch.setattr(upgrade_capture, "McpReader", Reader)
    return calls


def test_explicit_capture_records_protocol_and_rejects_overwrite(tmp_path, monkeypatch):
    calls = fake_reader(monkeypatch, Observation(status="ok", tools=[{
        "name": "read", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}],
        protocol_version="2026-07-28", server_info={"name": "fixture", "version": "1"}))
    out = tmp_path / "after.json"
    args = ["upgrade-brief", "capture", "--url", "https://example.invalid/mcp", "--target-id", "demo",
            "--out", str(out)]
    assert cli.main(args) == 0
    saved = out.read_bytes()
    document = json.loads(saved)
    assert document["protocol"] == "2026-07-28" and document["status"] == "ok"
    assert document["packageVersion"] is None and document["tools"][0]["annotations"]["readOnlyHint"]
    assert calls[0]["allow_private"] is False
    assert cli.main(args) == 2 and out.read_bytes() == saved
    assert len(calls) == 2, "an existing baseline must be refused before making any request"


def test_failed_capture_is_saved_as_failed_not_an_empty_success(tmp_path, monkeypatch):
    fake_reader(monkeypatch, Observation(status="http-401"))
    out = tmp_path / "failed.json"
    assert upgrade_capture.main(["--url", "http://127.0.0.1/mcp", "--target-id", "local", "--out", str(out),
                                 "--allow-private"]) == 1
    document = json.loads(out.read_text())
    assert document["status"] == "failed" and "tools" not in document


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:secret@example.invalid/mcp",
                                 "https://example.invalid/mcp?key=secret", "https://example.invalid/mcp#fragment"])
def test_capture_rejects_credential_or_non_http_urls_before_reading(tmp_path, monkeypatch, url):
    calls = fake_reader(monkeypatch, Observation(status="ok", tools=[]))
    assert upgrade_capture.main(["--url", url, "--target-id", "demo", "--out", str(tmp_path / "out")]) == 2
    assert not calls


def test_capture_limits_also_match_reducer_limits(tmp_path, monkeypatch):
    fake_reader(monkeypatch, Observation(status="ok", tools=[{"name": "x", "inputSchema": {"type": "object"},
                                                             "description": "x" * 200_001}]))
    out = tmp_path / "out.json"
    assert upgrade_capture.main(["--url", "https://example.invalid/mcp", "--target-id", "demo", "--out", str(out)]) == 2
    assert not out.exists() and not list(tmp_path.glob(".histor-observation-*"))
