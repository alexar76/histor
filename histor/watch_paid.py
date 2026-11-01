"""HISTOR Watch for teams: the paid push plan, sold by card through Stripe Checkout.

    POST /api/v1/watch/subscriptions        open_subscription()  subscription + Checkout Session
    (the team pays on checkout.stripe.com)  —                    HISTOR never sees a card
    POST /api/v1/watch/stripe               handle_event()       checkout.session.completed
    GET  /api/v1/watch/subscriptions/{id}   view()               the team is back: refresh() first
    worker(), every minute                  sweep()              tab closed AND webhook lost
    worker(), every 20 seconds              deliver_paid()       each change → the team's endpoint

The free Watch (/feed.xml?watch=, /api/v1/changes?watch=) needs none of this. What is sold is
push: each change of the servers a team follows, signed, to the HTTPS endpoint the team named
at checkout, through histor.security_events.deliver — HTTPS only, public addresses only,
pinned connection, no redirects, checked again at every send.

Every figure settle() checks comes from the payment row HISTOR wrote before asking Stripe,
never from the event, so a forged or replayed event cannot choose the period or the amount.
A paid period starts from the later of now and the current end. A refund or a dispute takes
back the period it bought. A test key opens only outside the prod profile and a live key only
in it, so a test card never buys a real subscription and a development instance never charges
a real card.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from awr import canonicalize

from histor import __version__
from histor.netguard import resolve_public
from histor.security_events import deliver, destination_binding, post_signed

log = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com"
STRIPE_VERSION = "2024-06-20"
SIGNATURE_TOLERANCE_S = 300
#: Stripe accepts a Checkout Session expiry between 30 minutes and 24 hours from now.
SESSION_TTL = timedelta(hours=2)
PAID_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")
#: One Stripe read per open payment per this many seconds, whoever asks.
REFRESH_EVERY_S = 5
SWEEP_EVERY_S = 60
SWEEP_BATCH = 20
DELIVER_EVERY_S = 20
#: Changes sent to one subscription per worker pass; the rest follow on the next pass.
DELIVER_PER_PASS = 20
MAX_SERVERS = 100
TS = "%Y-%m-%dT%H:%M:%SZ"
_SERVER_ID = re.compile(r"[0-9a-f]{16}")
_SUB_ID = re.compile(r"w[0-9a-f]{16}")

StripeCall = Callable[..., tuple[int, dict[str, Any]]]


class WatchError(ValueError):
    """The request is wrong: 400, with a message the team can act on."""


class NotFound(LookupError):
    """No such subscription, or the token is not its token: the same 404 for both."""


class Unavailable(RuntimeError):
    """Card sales are closed or Stripe cannot be reached: 503, nothing charged."""


class Refused(ValueError):
    """Stripe's answer does not match the payment HISTOR opened; nothing is granted."""


def _now() -> datetime:
    # One clock for everything here: periods, delivery and the signature window all read time.time().
    return datetime.fromtimestamp(int(time.time()), UTC)


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS)


def _parse(value: str | None) -> datetime | None:
    return datetime.strptime(value, TS).replace(tzinfo=UTC) if value else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------------------ the plan


def _live_key(key: str) -> bool:
    return key.startswith(("sk_live_", "rk_live_"))


DEMO_CLOSED = "demo mode: the plan and the price are real, card payment is switched off on this instance for now"


def closed_reason(settings) -> str:
    key, whsec = settings.stripe_secret_key, settings.stripe_webhook_secret
    if settings.watch_demo:
        return DEMO_CLOSED
    if not key:
        return "card payment is not configured on this instance (HISTOR_STRIPE_SECRET_KEY)"
    if not whsec:
        return "card payment is not configured on this instance (HISTOR_STRIPE_WEBHOOK_SECRET)"
    if not key.startswith(("sk_live_", "rk_live_", "sk_test_", "rk_test_")):
        return "HISTOR_STRIPE_SECRET_KEY is not a Stripe secret or restricted key"
    if settings.is_prod and not _live_key(key):
        return "a Stripe test key on a production instance would sell real subscriptions for test cards"
    if not settings.is_prod and _live_key(key):
        return "a Stripe live key outside production would charge real cards"
    return ""


def plan(settings) -> dict[str, Any]:
    reason = closed_reason(settings)
    return {
        "name": "HISTOR Watch for teams",
        "priceUsd": settings.watch_price_cents / 100,
        "priceCents": settings.watch_price_cents,
        "days": settings.watch_days,
        "renews": "no — pay again to extend; nothing is charged automatically",
        "maxServers": MAX_SERVERS,
        "includes": [
            "a signed POST to your HTTPS endpoint for each change of the servers you follow",
            "Slack incoming webhooks, ticketing systems or your own service",
            "retries with backoff for a day when your endpoint is down",
            "a test event on demand",
        ],
        "demo": settings.watch_demo,
        "card": {"enabled": not reason, "testMode": not reason and not _live_key(settings.stripe_secret_key),
                 "closedReason": reason},
    }


# ------------------------------------------------------------------------------ Stripe


def _form(obj: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's form encoding: nested keys as ``a[b][0][c]``."""
    out: list[tuple[str, str]] = []
    for key, value in obj.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.extend(_form(value, name))
        elif isinstance(value, list):
            out.extend(_form({str(i): v for i, v in enumerate(value)}, name))
        elif value is not None:
            out.append((name, str(value).lower() if isinstance(value, bool) else str(value)))
    return out


def stripe_request(settings, method: str, path: str, *, form: dict[str, Any] | None = None,
                   idempotency_key: str = "") -> tuple[int, dict[str, Any]]:
    import httpx

    headers = {
        "Authorization": f"Bearer {settings.stripe_secret_key}",
        "Stripe-Version": STRIPE_VERSION,
        "User-Agent": f"HISTOR/{__version__} (+{settings.public_base})",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    body = None
    if form is not None:
        body = urlencode(_form(form)).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
        response = client.request(method, f"{STRIPE_API}{path}", headers=headers, content=body)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


def _call(settings, stripe: StripeCall | None) -> StripeCall:
    if stripe is not None:
        return stripe
    return lambda method, path, **kw: stripe_request(settings, method, path, **kw)


def verify_signature(payload: bytes, header: str, secret: str, *, now: float | None = None,
                     tolerance_s: int = SIGNATURE_TOLERANCE_S) -> bool:
    """``Stripe-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "t.payload">`` (several v1 while a
    secret is rolled), fresh within ``tolerance_s``, compared in constant time."""
    if not secret or not header:
        return False
    stamp, signatures = "", []
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            stamp = value
        elif key == "v1" and value:
            signatures.append(value)
    if not stamp.isdigit() or not signatures:
        return False
    now = time.time() if now is None else now
    if abs(now - int(stamp)) > tolerance_s:
        return False
    want = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in signatures)


# ------------------------------------------------------------------------------ input


def validate_servers(store, ids: Any) -> list[str]:
    if isinstance(ids, str):
        ids = re.split(r"[\s,]+", ids)
    if not isinstance(ids, list):
        raise WatchError("`watch` is a list of server ids")
    seen: list[str] = []
    for value in ids:
        value = str(value).strip().lower()
        if not value:
            continue
        if not _SERVER_ID.fullmatch(value):
            raise WatchError(f"{value[:40]!r} is not a server id (16 hex characters, from the server's page)")
        if value not in seen:
            seen.append(value)
    if not seen or len(seen) > MAX_SERVERS:
        raise WatchError(f"follow 1 to {MAX_SERVERS} servers")
    unknown = [i for i in seen if store.target(i) is None]
    if unknown:
        raise WatchError(f"HISTOR has no server with id {', '.join(unknown[:5])}")
    return seen


def validate_url(url: Any, settings, *, resolver=None) -> str:
    url = str(url or "").strip()
    parts = urlsplit(url)
    if len(url) > 500 or parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise WatchError("the endpoint must be an https:// URL without credentials in it")
    try:
        resolve_public(url, allow_private=settings.allow_private_targets, resolver=resolver or socket.getaddrinfo)
    except ValueError as exc:
        raise WatchError(f"the endpoint is not reachable as a public HTTPS address: {exc}") from None
    return url


# ------------------------------------------------------------------------------ subscriptions


def _subscription(tx, sub_id: str) -> dict[str, Any] | None:
    return tx.one("SELECT * FROM watch_subscriptions WHERE id=?", (sub_id,))


def _authorized(store, sub_id: str, token: str) -> dict[str, Any]:
    if not _SUB_ID.fullmatch(sub_id or "") or not token:
        raise NotFound(sub_id)
    with store.db.read() as tx:
        row = _subscription(tx, sub_id)
    if row is None or not hmac.compare_digest(row["token_sha256"], _token_hash(token)):
        raise NotFound(sub_id)
    return row


def _open_payment(store, settings, sub_id: str, stripe: StripeCall) -> dict[str, Any]:
    payment_id = "wp" + secrets.token_hex(8)
    now = _now()
    expires = now + SESSION_TTL
    with store.db.transaction() as tx:
        tx.execute(
            "INSERT INTO watch_payments(id, subscription_id, amount_cents, days, status, created_at, expires_at)"
            " VALUES(?, ?, ?, ?, 'opening', ?, ?)",
            (payment_id, sub_id, settings.watch_price_cents, settings.watch_days, _ts(now), _ts(expires)),
        )
    base = settings.public_base
    form = {
        "mode": "payment",
        "client_reference_id": payment_id,
        "success_url": f"{base}/watch?subscription={sub_id}&paid=1",
        "cancel_url": f"{base}/watch?subscription={sub_id}",
        "expires_at": int(expires.timestamp()),
        # Cards settle at once; a delayed method would leave the team with no period and no
        # way to tell when one comes.
        "payment_method_types": ["card"],
        "metadata": {"histor_watch": sub_id, "payment": payment_id},
        "payment_intent_data": {"metadata": {"histor_watch": sub_id, "payment": payment_id}},
        "line_items": [{"quantity": 1, "price_data": {
            "currency": "usd",
            "unit_amount": settings.watch_price_cents,
            "product_data": {"name": f"HISTOR Watch for teams · {settings.watch_days} days"},
        }}],
    }

    def failed(note: str) -> None:
        with store.db.transaction() as tx:
            tx.execute("UPDATE watch_payments SET status='failed', note=? WHERE id=?", (note[:300], payment_id))

    try:
        status, session = stripe("POST", "/v1/checkout/sessions", form=form, idempotency_key=payment_id)
    except Exception as exc:  # noqa: BLE001 - Stripe unreachable
        failed(type(exc).__name__)
        raise Unavailable("the card processor could not be reached; nothing was charged") from None
    url, session_id = str(session.get("url") or ""), str(session.get("id") or "")
    if status >= 300 or not url.startswith("https://") or not session_id.startswith("cs_"):
        message = str((session.get("error") or {}).get("message") or f"HTTP {status}")
        failed(message)
        log.error("watch: Stripe refused a session for %s: %s", payment_id, message)
        raise Unavailable("the card processor refused to open a checkout; nothing was charged")
    with store.db.transaction() as tx:
        tx.execute("UPDATE watch_payments SET status='open', session_id=?, checkout_url=?, livemode=? WHERE id=?",
                   (session_id, url, 1 if session.get("livemode") else 0, payment_id))
    return {"id": payment_id, "checkoutUrl": url}


def open_subscription(store, settings, *, watch: Any, url: Any, stripe: StripeCall | None = None,
                      resolver=None) -> dict[str, Any]:
    """A pending subscription and its first checkout. The manage token is returned here and
    nowhere else: HISTOR keeps only its hash."""
    reason = closed_reason(settings)
    if reason:
        raise Unavailable(reason)
    servers = validate_servers(store, watch)
    url = validate_url(url, settings, resolver=resolver)
    sub_id, token, secret = "w" + secrets.token_hex(8), secrets.token_urlsafe(32), secrets.token_hex(24)
    now = _ts(_now())
    with store.db.transaction() as tx:
        tx.execute(
            "INSERT INTO watch_subscriptions(id, token_sha256, url, secret, watch, status, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)",
            (sub_id, _token_hash(token), url, secret, json.dumps(servers), now, now),
        )
    payment = _open_payment(store, settings, sub_id, _call(settings, stripe))
    return {"id": sub_id, "manageToken": token, "checkoutUrl": payment["checkoutUrl"], "payment": payment["id"]}


def renew(store, settings, sub_id: str, token: str, *, stripe: StripeCall | None = None) -> dict[str, Any]:
    """A checkout for one more period. An open one is handed back instead of a second."""
    row = _authorized(store, sub_id, token)
    reason = closed_reason(settings)
    if reason:
        raise Unavailable(reason)
    if row["status"] == "ended":
        raise WatchError("this subscription was ended by a dispute; start a new one")
    with store.db.read() as tx:
        open_rows = tx.execute(
            "SELECT id, checkout_url, expires_at FROM watch_payments WHERE subscription_id=? AND status='open' ORDER BY created_at DESC",
            (sub_id,))
    for p in open_rows:
        if _parse(p["expires_at"]) - _now() > timedelta(minutes=10):
            return {"id": sub_id, "checkoutUrl": p["checkout_url"], "payment": p["id"]}
    payment = _open_payment(store, settings, sub_id, _call(settings, stripe))
    return {"id": sub_id, "checkoutUrl": payment["checkoutUrl"], "payment": payment["id"]}


def set_servers(store, sub_id: str, token: str, watch: Any) -> None:
    _authorized(store, sub_id, token)
    servers = validate_servers(store, watch)
    with store.db.transaction() as tx:
        tx.execute("UPDATE watch_subscriptions SET watch=?, updated_at=? WHERE id=?",
                   (json.dumps(servers), _ts(_now()), sub_id))


def _cursor_id(sub_id: str) -> str:
    return f"watch-{sub_id}"


def _state(row: dict[str, Any], now: datetime) -> str:
    if row["status"] in ("pending", "ended"):
        return row["status"]
    until = _parse(row["active_until"])
    return "active" if until and until > now else "lapsed"


def view(store, settings, sub_id: str, token: str, *, stripe: StripeCall | None = None) -> dict[str, Any]:
    _authorized(store, sub_id, token)
    try:
        refresh(store, settings, sub_id, stripe=stripe)
    except (Unavailable, Refused) as exc:
        log.warning("watch: refresh of %s: %s", sub_id, exc)
    now = _now()
    with store.db.read() as tx:
        row = _subscription(tx, sub_id)
        payments = tx.execute("SELECT * FROM watch_payments WHERE subscription_id=? ORDER BY created_at", (sub_id,))
        cursor = tx.one("SELECT cursor, attempts, next_attempt FROM webhook_cursors WHERE id=?", (_cursor_id(sub_id),))
    servers = []
    for target in json.loads(row["watch"]):
        t = store.target(target)
        servers.append({"id": target, "name": (t or {}).get("name"), "page": f"{settings.public_base}/s/{target}"})
    return {
        "id": sub_id,
        "state": _state(row, now),
        "activeUntil": row["active_until"],
        "endpoint": row["url"],
        "secret": row["secret"],
        "servers": servers,
        "delivery": {
            "lastChange": cursor["cursor"] if cursor else None,
            "failedAttempts": cursor["attempts"] if cursor else 0,
            "nextAttemptAt": _ts(datetime.fromtimestamp(cursor["next_attempt"], UTC)) if cursor and cursor["next_attempt"] else None,
        },
        "payments": [{
            "id": p["id"], "status": p["status"], "amountUsd": p["amount_cents"] / 100, "days": p["days"],
            "createdAt": p["created_at"], "paidAt": p["paid_at"],
            "checkoutUrl": p["checkout_url"] if p["status"] == "open" else None,
        } for p in payments if p["status"] != "opening"],
        "note": row["note"],
    }


# ------------------------------------------------------------------------------ settlement


def _payment_intent(session: dict[str, Any]) -> str:
    value = session.get("payment_intent")
    if isinstance(value, dict):
        value = value.get("id")
    return str(value or "")


def _start_cursor(tx, sub: dict[str, Any]) -> None:
    """A new or renewed-after-a-gap subscription starts at the newest change: the team paid for
    what happens from now on, and a backlog would arrive as a flood in its channel."""
    newest = tx.one("SELECT COALESCE(MAX(id), 0) AS n FROM changes")["n"]
    binding = destination_binding("changes", sub["url"])
    cid = _cursor_id(sub["id"])
    if tx.one("SELECT id FROM webhook_cursors WHERE id=?", (cid,)):
        tx.execute("UPDATE webhook_cursors SET url_digest=?, cursor=?, attempts=0, next_attempt=0 WHERE id=?",
                   (binding, newest, cid))
    else:
        tx.execute("INSERT INTO webhook_cursors(id, url_digest, cursor, attempts, next_attempt, lease_until)"
                   " VALUES(?, ?, ?, 0, 0, 0)", (cid, binding, newest))


def settle(store, settings, payment_id: str, session: dict[str, Any]) -> str:
    """Add the period a paid session bought, once. Shared by the webhook, refresh and sweep.
    Returns the payment's status afterwards."""
    now = _now()
    with store.db.transaction() as tx:
        payment = tx.one("SELECT * FROM watch_payments WHERE id=?", (payment_id,))
        if payment is None or str(session.get("id") or "") != (payment["session_id"] or ""):
            raise Refused("that session does not belong to this payment")
        if payment["status"] == "paid":
            return "paid"
        if payment["status"] in ("refunded", "disputed"):
            raise Refused(f"the payment was {payment['status']}")
        if session.get("payment_status") != "paid":
            return payment["status"]
        total, currency = session.get("amount_total"), str(session.get("currency") or "").lower()
        if currency != "usd" or total != payment["amount_cents"]:
            tx.execute("UPDATE watch_payments SET note=? WHERE id=?",
                       (f"paid {total} {currency}, expected {payment['amount_cents']} usd", payment_id))
            log.error("watch: %s paid %s %s, expected %s usd cents — NOT granted, check it in Stripe",
                      payment_id, total, currency, payment["amount_cents"])
            raise Refused("amount or currency differs from the checkout opened")
        if _live_key(settings.stripe_secret_key) and not session.get("livemode"):
            raise Refused("test-mode payment on a live instance")
        # The webhook and a refresh can see the same payment at once: one UPDATE wins.
        won = tx.one(
            "UPDATE watch_payments SET status='paid', paid_at=?, payment_intent=? WHERE id=? AND status IN ('open', 'expired') RETURNING id",
            (_ts(now), _payment_intent(session), payment_id),
        )
        if not won:
            return tx.one("SELECT status FROM watch_payments WHERE id=?", (payment_id,))["status"]
        sub = _subscription(tx, payment["subscription_id"])
        current = _parse(sub["active_until"])
        if sub["status"] == "pending" or current is None or current <= now:
            _start_cursor(tx, sub)
            current = now
        until = current + timedelta(days=int(payment["days"]))
        tx.execute("UPDATE watch_subscriptions SET status='active', active_until=?, updated_at=? WHERE id=?",
                   (_ts(until), _ts(now), sub["id"]))
    log.warning("watch: %s paid by %s, active until %s", sub["id"], payment_id, _ts(until))
    return "paid"


def _refresh_payment(store, settings, payment: dict[str, Any], stripe: StripeCall, every_s: int) -> str:
    checked = _parse(payment["checked_at"])
    now = _now()
    if checked is not None and (now - checked).total_seconds() < every_s:
        return payment["status"]
    with store.db.transaction() as tx:
        tx.execute("UPDATE watch_payments SET checked_at=? WHERE id=?", (_ts(now), payment["id"]))
    try:
        status, session = stripe("GET", f"/v1/checkout/sessions/{payment['session_id']}")
    except Exception:  # noqa: BLE001
        raise Unavailable("the card processor could not be reached; retry") from None
    if status >= 300:
        raise Unavailable(f"the card processor answered HTTP {status}")
    if session.get("status") == "expired" and session.get("payment_status") != "paid":
        with store.db.transaction() as tx:
            tx.execute("UPDATE watch_payments SET status='expired' WHERE id=? AND status='open'", (payment["id"],))
        return "expired"
    return settle(store, settings, payment["id"], session)


def refresh(store, settings, sub_id: str, *, stripe: StripeCall | None = None, every_s: int = REFRESH_EVERY_S) -> None:
    """Read open checkouts back from Stripe: the recovery path for a webhook that has not come.
    Needs only the secret key — a sale already opened is honoured even if sales closed since."""
    if not settings.stripe_secret_key:
        return
    with store.db.read() as tx:
        rows = tx.execute("SELECT * FROM watch_payments WHERE subscription_id=? AND status='open'", (sub_id,))
    for payment in rows:
        _refresh_payment(store, settings, payment, _call(settings, stripe), every_s)


def sweep(store, settings, *, stripe: StripeCall | None = None) -> int:
    """Open checkouts whose team closed the tab and whose webhook was lost still become periods."""
    if not settings.stripe_secret_key:
        return 0
    floor = _ts(_now() - timedelta(hours=24))
    with store.db.read() as tx:
        rows = tx.execute(
            "SELECT * FROM watch_payments WHERE status='open' AND created_at > ? ORDER BY COALESCE(checked_at, '') LIMIT ?",
            (floor, SWEEP_BATCH))
    settled = 0
    for payment in rows:
        try:
            if _refresh_payment(store, settings, payment, _call(settings, stripe), SWEEP_EVERY_S) == "paid":
                settled += 1
        except (Refused, Unavailable) as exc:
            log.warning("watch: sweep could not settle %s: %s", payment["id"], exc)
    return settled


# ------------------------------------------------------------------------------ reversals


def _take_back(store, payment: dict[str, Any], kind: str, note: str) -> None:
    """The money went back, so the period it bought goes too."""
    now = _now()
    with store.db.transaction() as tx:
        before = tx.one("SELECT status FROM watch_payments WHERE id=?", (payment["id"],))["status"]
        if before == kind or not tx.one("UPDATE watch_payments SET status=?, note=? WHERE id=? AND status=? RETURNING id",
                                        (kind, note[:300], payment["id"], before)):
            return
        if before != "paid":
            # Reversed before HISTOR saw it paid: marking it is enough, settle() now refuses it.
            return
        sub = _subscription(tx, payment["subscription_id"])
        until = _parse(sub["active_until"])
        if until is not None:
            until -= timedelta(days=int(payment["days"]))
            until = max(until, now)
        status = "ended" if kind == "disputed" else sub["status"]
        tx.execute("UPDATE watch_subscriptions SET status=?, active_until=?, note=?, updated_at=? WHERE id=?",
                   (status, _ts(until) if until else None, f"{kind}: {note}"[:300], _ts(now), sub["id"]))
    log.error("watch: %s on %s (%s) — its period taken back", kind.upper(), payment["subscription_id"], note)


def _payment_for_intent(store, payment_intent: str, stripe: StripeCall) -> dict[str, Any] | None:
    """A reversal names a payment intent. Before the paid event was seen, the intent is on no row
    yet, so ask Stripe which session it belongs to."""
    if not payment_intent:
        return None
    with store.db.read() as tx:
        row = tx.one("SELECT * FROM watch_payments WHERE payment_intent=?", (payment_intent,))
    if row is not None:
        return row
    try:
        status, found = stripe("GET", "/v1/checkout/sessions?" + urlencode({"payment_intent": payment_intent, "limit": 1}))
    except Exception:  # noqa: BLE001
        raise Unavailable("cannot associate the reversal yet") from None
    if status >= 300:
        raise Unavailable("cannot associate the reversal yet")
    for session in found.get("data") or []:
        with store.db.transaction() as tx:
            row = tx.one("SELECT * FROM watch_payments WHERE session_id=?", (str(session.get("id") or ""),))
            if row is not None and _payment_intent(session) == payment_intent:
                tx.execute("UPDATE watch_payments SET payment_intent=? WHERE id=?", (payment_intent, row["id"]))
                return {**row, "payment_intent": payment_intent}
    return None


def handle_event(store, settings, payload: bytes, signature: str, *, now: float | None = None,
                 stripe: StripeCall | None = None) -> dict[str, Any]:
    """A Stripe event, verified. Anything not about Watch payments is acknowledged and ignored,
    so Stripe does not retry it for days."""
    if not verify_signature(payload, signature, settings.stripe_webhook_secret, now=now):
        raise Refused("the Stripe-Signature does not verify")
    try:
        event = json.loads(payload)
    except ValueError:
        raise Refused("the event is not JSON") from None
    if not isinstance(event, dict):
        return {"outcome": "ignored"}
    kind = str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}
    if not isinstance(obj, dict):
        return {"outcome": "ignored"}
    if kind in PAID_EVENTS or kind == "checkout.session.expired":
        payment_id = str(obj.get("client_reference_id") or "")
        with store.db.read() as tx:
            payment = tx.one("SELECT * FROM watch_payments WHERE id=?", (payment_id,))
        if payment is None or payment["session_id"] != obj.get("id"):
            return {"outcome": "ignored"}
        if kind == "checkout.session.expired":
            if obj.get("payment_status") != "paid":
                with store.db.transaction() as tx:
                    tx.execute("UPDATE watch_payments SET status='expired' WHERE id=? AND status='open'", (payment_id,))
            return {"outcome": "expired", "payment": payment_id}
        try:
            return {"outcome": settle(store, settings, payment_id, obj), "payment": payment_id}
        except Refused as exc:
            # Acknowledged, not retried: a redelivery carries the same figures.
            return {"outcome": "refused", "payment": payment_id, "reason": str(exc)}
    if kind in ("charge.refunded", "charge.dispute.created"):
        payment = _payment_for_intent(store, str(obj.get("payment_intent") or ""), _call(settings, stripe))
        if payment is None:
            return {"outcome": "ignored"}
        if kind == "charge.dispute.created":
            _take_back(store, payment, "disputed", f"dispute {str(obj.get('id'))[:40]}: {str(obj.get('reason'))[:60]}")
            return {"outcome": "disputed", "payment": payment["id"]}
        refunded = obj.get("amount_refunded")
        if isinstance(refunded, int) and refunded >= payment["amount_cents"]:
            _take_back(store, payment, "refunded", f"refunded {refunded} cents in Stripe")
            return {"outcome": "refunded", "payment": payment["id"]}
        with store.db.transaction() as tx:
            tx.execute("UPDATE watch_payments SET note=? WHERE id=?", (f"partial refund {refunded} cents; period kept", payment["id"]))
        return {"outcome": "partial_refund", "payment": payment["id"]}
    return {"outcome": "ignored"}


# ------------------------------------------------------------------------------ delivery


def _subscription_doc(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": _cursor_id(row["id"]), "url": row["url"], "secret": row["secret"], "source": "changes",
            "watch": json.loads(row["watch"])}


def deliver_paid(store, settings, *, client=None, now: float | None = None, resolver=None,
                 per_pass: int = DELIVER_PER_PASS) -> dict[str, int]:
    """Send each active subscription its next changes, in order, at least once."""
    moment = time.time() if now is None else now
    stamp = _ts(datetime.fromtimestamp(moment, UTC))
    with store.db.read() as tx:
        rows = tx.execute("SELECT * FROM watch_subscriptions WHERE status='active' AND active_until > ?", (stamp,))
    sent = failed = 0
    for row in rows:
        doc = _subscription_doc(row)
        for _ in range(per_pass):
            try:
                result = deliver(store, doc, client=client, now=moment, resolver=resolver or socket.getaddrinfo,
                                 base_url=settings.public_base)
            except ValueError as exc:
                log.error("watch: %s cannot be delivered: %s", row["id"], exc)
                break
            if result["status"] == "delivered":
                sent += 1
                continue
            failed += result["status"] == "retry"
            break
    return {"subscriptions": len(rows), "delivered": sent, "failed": failed}


def send_test(store, settings, sub_id: str, token: str, *, client=None, resolver=None) -> dict[str, Any]:
    """One signed ``histor.test/v1`` event now, so a team can check its endpoint and its
    signature check without waiting for a server to change."""
    row = _authorized(store, sub_id, token)
    if _state(row, _now()) != "active":
        raise WatchError("the subscription is not active")
    event_id = "test-" + secrets.token_hex(8)
    body = {"type": "histor.test/v1", "id": event_id, "subscription": sub_id, "sentAt": _ts(_now()),
            "note": "A test event from HISTOR Watch. Real events are histor.change/v1, signed the same way.",
            "text": "HISTOR Watch: test event. Changes of the servers you follow will arrive here."}
    code = post_signed(row["url"], row["secret"], event_id, canonicalize(body), now=time.time(), client=client,
                       resolver=resolver or socket.getaddrinfo)
    return {"eventId": event_id, "endpointStatus": code, "ok": code is not None and 200 <= code < 300}


def worker(services, *, first_wait: float = 10.0) -> None:
    """Delivery every 20 seconds and the payment sweep every minute, for the life of the process."""
    if services.stop.wait(first_wait):
        return
    swept = 0.0
    while True:
        try:
            deliver_paid(services.store, services.settings)
        except Exception:  # noqa: BLE001 - one bad pass must not end delivery
            log.exception("watch: delivery pass failed")
        if time.monotonic() - swept >= SWEEP_EVERY_S:
            swept = time.monotonic()
            try:
                sweep(services.store, services.settings, stripe=services.stripe)
            except Exception:  # noqa: BLE001
                log.exception("watch: payment sweep failed")
        if services.stop.wait(DELIVER_EVERY_S):
            return
