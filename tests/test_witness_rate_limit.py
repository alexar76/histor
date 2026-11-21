"""witness-submit and a witness that answers 429: left alone for a while, not a failure of the run."""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from histor import cosign_cli

ROOT = base64.b64encode(b"\x01" * 32).decode()
SIG = base64.b64encode(b"\x00" * 68).decode()
REAL_CLIENT = httpx.Client
NOTE = f"example.org/log\n5\n{ROOT}\n\n— example.org/log {SIG}\n"


def run(monkeypatch, tmp_path, witness, retry_after=None):
    """main() against a log serving NOTE and one witness answering ``witness(request)``; returns (code, state, calls)."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/checkpoint":
            return httpx.Response(200, text=NOTE)
        calls.append(str(request.url))
        return witness(request)

    monkeypatch.setattr(cosign_cli.httpx, "Client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    state = tmp_path / "state.json"
    code = cosign_cli.main(["--log", "http://log.test", "--witness", "http://w.test", "--state", str(state)])
    return code, json.loads(state.read_text()), calls


def test_429_is_not_a_failure_and_holds_the_witness_for_retry_after(monkeypatch, tmp_path, capsys):
    code, state, calls = run(monkeypatch, tmp_path,
                             lambda r: httpx.Response(429, text="rate limit of 144 requests per day exceeded",
                                                      headers={"Retry-After": "7200"}))
    assert code == 0 and len(calls) == 1
    held = state["http://w.test#retry-after"]
    assert 7200 - 5 <= held - time.time() <= 7200
    assert "429 (rate limit of 144 requests per day exceeded); not asked for 120 min" in capsys.readouterr().err

    # the next run does not ask that witness at all while the hold lasts
    code, state, calls = run(monkeypatch, tmp_path, lambda r: pytest.fail("asked a held witness"))
    assert code == 0 and calls == [] and state["http://w.test#retry-after"] == held
    assert "rate-limited this log; not asked until" in capsys.readouterr().err


def test_429_without_retry_after_holds_six_hours_and_bounds_hold(monkeypatch, tmp_path):
    _, state, _ = run(monkeypatch, tmp_path, lambda r: httpx.Response(429, text="slow down"))
    assert 6 * 3600 - 5 <= state["http://w.test#retry-after"] - time.time() <= 6 * 3600
    assert cosign_cli.retry_after_seconds("5") == 600
    assert cosign_cli.retry_after_seconds("999999") == 24 * 3600
    assert cosign_cli.retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT") == 6 * 3600


def test_an_expired_hold_is_dropped_and_the_witness_asked_again(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"http://w.test#retry-after": int(time.time()) - 1}))
    code, state, calls = run(monkeypatch, tmp_path, lambda r: httpx.Response(500, text="broken"))
    assert len(calls) == 1 and code == 1  # other refusals still fail the run
    assert "http://w.test#retry-after" not in state
