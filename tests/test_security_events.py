import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from histor.app import create_app
from histor.logbook import verify_document
from histor.security_events import append_event, census, deliver, events
from tests.conftest import tool
from tests.test_app import target_id

pytest_plugins = ["tests.test_app"]

PUBLIC = "93.184.215.14"


def public_resolver(host, port, *args, **kwargs):
    return [(2, 1, 6, "", (PUBLIC, port))]


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
    r = public_resolver
    assert deliver(s.store, subscription, client=client, now=100, resolver=r)["status"] == "retry"
    assert deliver(s.store, subscription, client=client, now=101, resolver=r)["status"] == "waiting"
    assert deliver(s.store, subscription, client=client, now=200, resolver=r)["status"] == "delivered"
    assert calls[0].content == calls[1].content
    # Pinned: the checked address in the URL, the name in Host and as the TLS name.
    assert calls[0].url.host == PUBLIC and calls[0].headers["host"] == "operator.test"
    assert calls[0].extensions.get("sni_hostname") == "operator.test"
    assert deliver(s.store, subscription, client=client, now=201, resolver=r)["status"] == "idle"
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
    desk = TestClient(create_app(s))
    feed = desk.get("/security-feed.xml")
    assert feed.status_code == 200 and "<entry>" in feed.text


def test_a_webhook_destination_that_resolves_inward_is_not_called(world):
    """Check-then-connect-by-name was the class the 2026-10-25 audit fixed elsewhere: a
    subscriber's name that resolves to a private address gets no request and backs off."""
    s = world["services"]
    with s.store.db.transaction() as tx:
        append_event(tx, s.key, "target", "2026-10-10T00:00:00Z", {}, {"v": 1})
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(204)))

    def inward(host, port, *a, **k):
        return [(2, 1, 6, "", ("10.0.0.5", port))]

    sub = {"id": "inward", "url": "https://hooks.customer.example/x", "secret": "s" * 32}
    assert deliver(s.store, sub, client=client, now=100, resolver=inward)["status"] == "retry"
    assert calls == []


def test_watch_webhooks_deliver_definition_changes_of_watched_servers_only(api):
    """HISTOR Watch for teams: a `changes` subscription gets each change of the servers it
    follows, in order, once, with links to the diff and the server page."""
    client_app, world = api
    world["mcp"].tools["/a"] = [tool("get_weather", "changed for the watch")]
    world["clock"].advance(days=1)
    world["services"].crawler.run()
    s = world["services"]
    watched = target_id(world, "io.example/weather")
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(204)))
    sub = {"id": "team-a", "url": "https://hooks.team.example/histor", "secret": "t" * 32,
           "source": "changes", "watch": [watched]}
    kw = dict(client=client, resolver=public_resolver, base_url="https://histor.example")
    assert deliver(s.store, sub, now=100, **kw)["status"] == "delivered"
    body = json.loads(calls[0].content)
    assert body["type"] == "histor.change/v1" and body["target"] == watched
    assert body["modified"] == ["get_weather"]
    assert body["diff"].startswith("https://histor.example/api/v1/changes/")
    assert calls[0].headers["X-Histor-Event-ID"] == body["id"]
    assert deliver(s.store, sub, now=101, **kw)["status"] == "idle"
    other = {**sub, "id": "team-b", "watch": ["0" * 16]}
    assert deliver(s.store, other, now=102, **kw)["status"] == "idle"
    with pytest.raises(ValueError, match="watch"):
        deliver(s.store, {**sub, "id": "bad", "watch": []}, now=103, **kw)
    with pytest.raises(ValueError, match="source"):
        deliver(s.store, {**sub, "id": "bad2", "source": "everything"}, now=104, **kw)
