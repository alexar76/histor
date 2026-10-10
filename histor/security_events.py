"""Signed package/definition events, durable webhook cursors and honest census counts."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from awr import canonicalize

from histor.logbook import sign_document
from histor.store import dumps


def _stable(value):
    if isinstance(value, dict):
        return {
            k: _stable(v) for k, v in value.items() if k not in ("observedAt", "seconds", "payloadSha256")
        }
    if isinstance(value, list):
        return [_stable(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?<=canary-)[a-f0-9]{12}(?=[@/\s]|$)", "<run>", value)
    return value


def append_event(tx, key, target, observed_at, previous, current, *, kind="package"):
    if _stable(previous) == _stable(current):
        return
    body = {
        "type": "histor.security-event/v1",
        "issuer": key.did,
        "target": target,
        "observedAt": observed_at,
        "kind": kind,
        "previousDigest": hashlib.sha256(canonicalize(previous)).hexdigest(),
        "evidence": current,
    }
    event_id = hashlib.sha256(canonicalize(body)).hexdigest()
    signed = sign_document(key, {**body, "id": event_id})
    tx.execute(
        "INSERT INTO security_events(event_id, target_id, observed_at, body) VALUES(?, ?, ?, ?) ON CONFLICT(event_id) DO NOTHING",
        (event_id, target, observed_at, dumps(signed)),
    )


def events(store, *, after=0, limit=100, targets=None):
    sql = "SELECT seq, body FROM security_events WHERE seq > ?"
    args = [after]
    if targets:
        sql += " AND target_id IN (" + ",".join("?" for _ in targets) + ")"
        args.extend(targets)
    sql += " ORDER BY seq LIMIT ?"
    args.append(min(200, max(1, limit)))
    with store.db.read() as tx:
        rows = tx.execute(sql, args)
    return [{"cursor": r["seq"], "event": json.loads(r["body"])} for r in rows]


def deliver(store, subscription, *, client=None, now=None):
    """One ordered delivery per invocation. At least once, stable ID, lease and bounded backoff.

    Subscription URLs and secrets come ONLY from an operator-owned local configuration.
    Nothing in package metadata or a public API can select a destination. Redirects and
    environment proxies are disabled. Callers deduplicate X-Histor-Event-ID.
    """
    now = time.time() if now is None else now
    name, url, secret = (subscription[k] for k in ("id", "url", "secret"))
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or len(secret) < 32:
        raise ValueError("webhooks require a fixed HTTPS destination and a 32+ character secret")
    binding = hashlib.sha256(url.encode()).hexdigest()
    token = secrets.token_hex(16)
    with store.db.transaction() as tx:
        tx.execute(
            "INSERT INTO webhook_cursors(id, url_digest, cursor, attempts, next_attempt, lease_until) VALUES(?, ?, 0, 0, 0, 0) ON CONFLICT(id) DO NOTHING",
            (name, binding),
        )
        state = tx.one("SELECT * FROM webhook_cursors WHERE id=?", (name,))
        if state["url_digest"] != binding:
            raise ValueError("changed destination requires a new subscription id")
        if state["lease_until"] > now or state["next_attempt"] > now:
            return {"status": "waiting"}
        lease = tx.one(
            "UPDATE webhook_cursors SET lease_until=?, lease_token=? WHERE id=? AND lease_until<=? RETURNING id",
            (now + 60, token, name, now),
        )
        if not lease:
            return {"status": "busy"}
    rows = events(store, after=state["cursor"], limit=1)
    if not rows:
        with store.db.transaction() as tx:
            tx.execute("UPDATE webhook_cursors SET lease_until=0 WHERE id=? AND lease_token=?", (name, token))
        return {"status": "idle"}
    item = rows[0]
    payload = canonicalize(item["event"])
    event_id = item["event"]["id"]
    timestamp = str(int(now))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    own_client = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
    try:
        with client.stream(
            "POST",
            url,
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Histor-Event-ID": event_id,
                "X-Histor-Timestamp": timestamp,
                "X-Histor-Signature": "sha256=" + signature,
            },
            follow_redirects=False,
        ) as response:
            success = 200 <= response.status_code < 300
    except httpx.HTTPError:
        success = False
    finally:
        if own_client:
            client.close()
    attempts = 0 if success else state["attempts"] + 1
    with store.db.transaction() as tx:
        tx.execute(
            "UPDATE webhook_cursors SET cursor=?, attempts=?, next_attempt=?, lease_until=0 WHERE id=? AND lease_token=?",
            (
                item["cursor"] if success else state["cursor"],
                attempts,
                now + min(86400, 30 * 2 ** min(attempts, 12)) if not success else 0,
                name,
                token,
            ),
        )
    return {"status": "delivered" if success else "retry", "eventId": event_id, "attempts": attempts}


def census(store, key, *, now=None):
    """One snapshot of latest package versions, with distinct denominators and manifest."""
    now = now or datetime.now(UTC)
    with store.db.read() as tx:
        rows = tx.execute(
            "SELECT id, endpoint, package_version, last_status, last_ok, package_signals FROM targets WHERE endpoint LIKE ? OR endpoint LIKE ? ORDER BY endpoint, id",
            ("npm:%", "pypi:%"),
        )
    # One package can have several registry aliases; retain its freshest observation only.
    unique = {}
    for row in rows:
        prior = unique.get(row["endpoint"])
        if not prior or str(row["last_ok"] or "") > str(prior["last_ok"] or ""):
            unique[row["endpoint"]] = row
    manifest, phases = (
        [],
        {p: {"complete": 0, "incomplete": 0} for p in ("installScripts", "startup", "calls")},
    )
    total_tools = successful = completed = stale = 0
    for package, row in sorted(unique.items()):
        sig = json.loads(row["package_signals"] or "{}")
        behaviour = sig.get("behaviour") or {}
        complete = row["last_status"] == "ok" and behaviour.get("complete") is True
        completed += int(complete)
        try:
            observed = datetime.fromisoformat(str(row["last_ok"]).replace("Z", "+00:00"))
            age_seconds = int((now - observed).total_seconds())
        except (ValueError, TypeError):
            age_seconds = None
        fresh = age_seconds is not None and 0 <= age_seconds <= 172800
        stale += int(not fresh)
        for phase in phases:
            phases[phase][
                "complete" if behaviour.get(phase, {}).get("complete") is True else "incomplete"
            ] += 1
        calls = behaviour.get("calls") or {}
        total_tools += calls.get("total", 0)
        successful += calls.get("successful", 0)
        manifest.append(
            {
                "package": package,
                "version": row["package_version"],
                "lastObserved": row["last_ok"],
                "complete": complete,
                "fresh": fresh,
                "ageSeconds": age_seconds,
                "observer": sig.get("observer"),
                "evidenceDigest": hashlib.sha256(canonicalize(sig)).hexdigest(),
            }
        )
    return sign_document(
        key,
        {
            "type": "histor.behaviour-census/v1",
            "issuer": key.did,
            "generatedAt": now.isoformat(),
            "targetRows": len(rows),
            "packages": len(unique),
            "completed": completed,
            "staleOrUnobserved": stale,
            "freshnessWindowSeconds": 172800,
            "incomplete": len(unique) - completed,
            "phases": phases,
            "toolsAdvertised": total_tools,
            "toolsSuccessfullyCalled": successful,
            "manifest": manifest,
            "manifestSha256": hashlib.sha256(canonicalize(manifest)).hexdigest(),
            "methodology": "Latest observed version per distinct registry package. Incomplete and unobserved packages remain in the denominator. One canary per selected tool is bounded coverage, never exhaustive proof of safety.",
        },
    )
