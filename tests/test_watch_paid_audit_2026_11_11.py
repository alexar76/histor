"""Audit 2026-11-11, desks-stripe-2: refunding an OLD, used-up Watch payment took its days out of
the period a NEWER payment had bought; the subscription lapsed although that payment stood."""
from __future__ import annotations

import socket
from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from histor import watch_paid
from histor.app import create_app
from tests.conftest import make_target
from tests.test_app import TOOLS, target_id
from tests.test_security_events import public_resolver
from tests.test_watch_paid import HOOK, WHSEC, FakeStripe, auth, signed


def _setup(world, monkeypatch):
    world["mcp"].tools["/a"] = TOOLS
    world["targets"] += [make_target("io.example/weather", "/a")]
    world["services"].crawler.run()
    s = world["services"]
    s.settings = replace(s.settings, stripe_secret_key="sk_test_fixture", stripe_webhook_secret=WHSEC,
                         public_base="https://histor.example")
    s.stripe = FakeStripe()
    monkeypatch.setattr(socket, "getaddrinfo", public_resolver)
    return s


def _two_payments(world, client, s):
    stripe = s.stripe
    sub = client.post("/api/v1/watch/subscriptions",
                      json={"watch": [target_id(world, "io.example/weather")], "url": HOOK}).json()
    token = sub["manageToken"]
    a = stripe.pay(next(iter(stripe.sessions)))
    assert watch_paid.settle(s.store, s.settings, a["client_reference_id"], a) == "paid"
    with s.store.db.transaction() as tx:      # A's 30 days are used up and the subscription lapsed
        tx.execute("UPDATE watch_subscriptions SET active_until=? WHERE id=?",
                   (watch_paid._ts(watch_paid._now() - timedelta(days=1)), sub["id"]))
        tx.execute("UPDATE watch_payments SET paid_at=? WHERE id=?",
                   (watch_paid._ts(watch_paid._now() - timedelta(days=31)), a["client_reference_id"]))
    client.post(f"/api/v1/watch/subscriptions/{sub['id']}/renew", headers=auth(token))
    b = stripe.pay(list(stripe.sessions)[1])
    assert watch_paid.settle(s.store, s.settings, b["client_reference_id"], b) == "paid"
    return sub, token, a, b


def _refund(client, payment, event_id):
    refund = {"id": "ch_" + event_id, "object": "charge", "payment_intent": payment["payment_intent"],
              "amount_refunded": 2900}
    body, sig = signed({"id": event_id, "type": "charge.refunded", "data": {"object": refund}})
    return client.post("/api/v1/watch/stripe", content=body, headers={"Stripe-Signature": sig}).json()


def test_refunding_an_old_used_payment_keeps_the_new_period(world, monkeypatch):
    s = _setup(world, monkeypatch)
    with TestClient(create_app(s)) as client:
        sub, token, a, _b = _two_payments(world, client, s)
        assert _refund(client, a, "evt_ra")["outcome"] == "refunded"
        after = client.get(f"/api/v1/watch/subscriptions/{sub['id']}", headers=auth(token)).json()
        assert after["state"] == "active"
        assert (watch_paid._parse(after["activeUntil"]) - watch_paid._now()).days >= 29


def test_refunding_the_payment_in_use_still_takes_its_period_back(world, monkeypatch):
    s = _setup(world, monkeypatch)
    with TestClient(create_app(s)) as client:
        sub, token, _a, b = _two_payments(world, client, s)
        assert _refund(client, b, "evt_rb")["outcome"] == "refunded"
        after = client.get(f"/api/v1/watch/subscriptions/{sub['id']}", headers=auth(token)).json()
        assert after["state"] == "lapsed"


def test_two_payments_settling_at_once_both_add_their_period(world, monkeypatch):
    """desks-stripe-6: settle() read active_until, computed the end in Python and wrote it back;
    on PostgreSQL two settles of one subscription both read the same end, so a paid period was
    lost. The delay below forces that interleaving."""
    import threading
    import time as _time

    s = _setup(world, monkeypatch)
    stripe = s.stripe
    with TestClient(create_app(s)) as client:
        sub = client.post("/api/v1/watch/subscriptions",
                          json={"watch": [target_id(world, "io.example/weather")], "url": HOOK}).json()
        with s.store.db.transaction() as tx:     # the first checkout is about to expire: renew opens a second
            tx.execute("UPDATE watch_payments SET expires_at=? WHERE subscription_id=?",
                       (watch_paid._ts(watch_paid._now() + timedelta(minutes=5)), sub["id"]))
        client.post(f"/api/v1/watch/subscriptions/{sub['id']}/renew", headers=auth(sub["manageToken"]))
        sessions = [stripe.pay(sid) for sid in list(stripe.sessions)[:2]]
        assert len({p["client_reference_id"] for p in sessions}) == 2

        read = watch_paid._subscription

        def slow_read(tx, sub_id):
            row = read(tx, sub_id)
            _time.sleep(0.4)            # the other settle runs while this one holds what it read
            return row

        monkeypatch.setattr(watch_paid, "_subscription", slow_read)
        results: list[str] = []
        threads = [threading.Thread(target=lambda p=p: results.append(
            watch_paid.settle(s.store, s.settings, p["client_reference_id"], p))) for p in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert results == ["paid", "paid"]
        monkeypatch.setattr(watch_paid, "_subscription", read)
        after = client.get(f"/api/v1/watch/subscriptions/{sub['id']}", headers=auth(sub["manageToken"])).json()
        assert (watch_paid._parse(after["activeUntil"]) - watch_paid._now()).days >= 59, after["activeUntil"]
