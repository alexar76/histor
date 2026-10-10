import hashlib
import hmac

import httpx
import pytest
from fastapi.testclient import TestClient

from histor.app import create_app
from histor.logbook import verify_document
from histor.security_events import append_event, census, deliver, events


def test_signed_events_are_deduplicated_and_cursor_is_lossless(world):
    s = world["services"]
    with s.store.db.transaction() as tx:
        for i in range(3):
            append_event(tx, s.key, "0123456789abcdef", f"2026-10-10T00:00:0{i}Z", {}, {"version": str(i)})
        append_event(tx, s.key, "0123456789abcdef", "2026-10-10T00:00:02Z", {}, {"version": "2"})
        append_event(
            tx, s.key, "0123456789abcdef", "2026-10-10T00:00:03Z", {"version": "2"}, {"version": "2"}
        )
    first = events(s.store, limit=2)
    last = events(s.store, after=first[-1]["cursor"])
    assert len(first) == 2 and len(last) == 1
    assert verify_document(last[0]["event"], s.key.did, "histor.security-event/v1")
    assert events(s.store, targets=["other"]) == []
    result = TestClient(create_app(s)).get("/api/v1/security-events?limit=2").json()
    assert len(result["events"]) == 2
    assert TestClient(create_app(s)).get("/api/v1/security-events?watch=bad").status_code == 400


def test_webhook_retries_same_event_and_signs_exact_bytes(world):
    s = world["services"]
    calls = []
    subscription = {"id": "test", "url": "https://operator.test/hook", "secret": "s" * 32}
    with s.store.db.transaction() as tx:
        append_event(tx, s.key, "target", "2026-10-10T00:00:00Z", {}, {"behaviour": {"complete": False}})

    def handle(req):
        calls.append(req)
        expected = hmac.new(
            subscription["secret"].encode(),
            req.headers["X-Histor-Timestamp"].encode() + b"." + req.content,
            hashlib.sha256,
        ).hexdigest()
        assert req.headers["X-Histor-Signature"] == "sha256=" + expected
        return httpx.Response(503 if len(calls) == 1 else 204)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    assert deliver(s.store, subscription, client=client, now=100)["status"] == "retry"
    assert deliver(s.store, subscription, client=client, now=101)["status"] == "waiting"
    assert deliver(s.store, subscription, client=client, now=200)["status"] == "delivered"
    assert calls[0].content == calls[1].content
    assert deliver(s.store, subscription, client=client, now=201)["status"] == "idle"
    with pytest.raises(ValueError):
        deliver(s.store, {**subscription, "url": "https://changed.test"}, client=client, now=300)
    with pytest.raises(ValueError):
        deliver(s.store, {**subscription, "url": "http://plain.test"}, client=client)
    assert census(s.store, s.key)["completed"] == 0


def test_census_keeps_incomplete_packages_in_denominator(world):
    import json

    from tests.conftest import tool
    from tests.test_packages import FakePackages, package_target

    s = world["services"]
    fake = FakePackages()
    s.crawler.packages = fake
    target = package_target()
    world["targets"].append(target)
    fake.latest["npm:@a/mem"] = "1.0.0"
    fake.tools[("npm:@a/mem", "1.0.0")] = [tool("fixture", "ordinary")]
    s.crawler.run()
    with s.store.db.transaction() as tx:
        row = tx.one("SELECT package_signals FROM targets WHERE id=?", (target.id,))
        signals = json.loads(row["package_signals"])
        signals["behaviour"] = {
            "complete": False,
            "startup": {"complete": True},
            "calls": {"complete": False, "total": 4, "successful": 2},
        }
        tx.execute("UPDATE targets SET package_signals=? WHERE id=?", (json.dumps(signals), target.id))
    report = census(s.store, s.key)
    assert report["packages"] == 1 and report["completed"] == 0 and report["incomplete"] == 1
    assert report["toolsAdvertised"] == 4 and report["toolsSuccessfullyCalled"] == 2
    assert report["phases"]["startup"]["complete"] == 1
    assert verify_document(report, s.key.did, "histor.behaviour-census/v1")
    api = TestClient(create_app(s))
    feed = api.get("/security-feed.xml")
    assert feed.status_code == 200 and "<entry>" in feed.text
