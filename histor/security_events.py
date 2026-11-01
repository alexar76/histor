"""Signed package/definition events, durable webhook cursors and honest census counts."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from awr import canonicalize

from histor.logbook import sign_document
from histor.netguard import resolve_public
from histor.store import dumps

# What a subscription is fed. "security-events": signed package/definition evidence (the
# original feed, every target). "changes": HISTOR Watch — every recorded change of the tool
# definitions of the servers in the subscription's `watch` list, the same rows as
# /api/v1/changes?watch=…, so a team gets the change in its own system as it is recorded.
SOURCES = ("security-events", "changes")


def _change_event(base_url: str, row: dict) -> tuple[str, dict]:
    summary = row["summary"] if isinstance(row["summary"], dict) else {}
    body = {
        "type": "histor.change/v1",
        "id": f"change-{row['id']}",
        "change": row["id"],
        "target": row["target_id"],
        "server": row.get("name"),
        "endpoint": row.get("endpoint"),
        "observedAt": row["observed_at"],
        "added": summary.get("added") or [],
        "removed": summary.get("removed") or [],
        "modified": [m.get("tool") if isinstance(m, dict) else m for m in (summary.get("modified") or [])],
        "newAddresses": summary.get("newAddresses") or [],
        "label": row.get("label_id"),
        "diff": f"{base_url}/api/v1/changes/{row['id']}" if base_url else None,
        "page": f"{base_url}/s/{row['target_id']}" if base_url else None,
    }
    body["text"] = _change_text(body)
    return body["id"], body


def _slack_safe(value) -> str:
    # Names come from registries and servers: escaped the way Slack asks, so a name cannot
    # become a link or an @channel in the team's channel.
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:120]


def _change_text(body: dict) -> str:
    """One line a chat tool shows as it is: Slack and Teams incoming webhooks read `text`."""
    parts = []
    for key in ("added", "removed", "modified"):
        names = [_slack_safe(n) for n in body[key] if n]
        if names:
            parts.append(f"{key} {', '.join(names[:5])}{' …' if len(names) > 5 else ''}")
    if body["newAddresses"]:
        parts.append(f"{len(body['newAddresses'])} new outside address(es)")
    line = f"HISTOR: {_slack_safe(body['server'] or body['target'])} changed its tool definitions"
    line += f" ({'; '.join(parts)})" if parts else ""
    return line + (f" {body['page']}" if body["page"] else "")


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


def destination_binding(source: str, url: str) -> str:
    """What a cursor is bound to. The original feed keeps its binding, so existing cursors stay
    valid; a changes feed binds its source too, so reusing an id for the other feed cannot resume
    at a foreign cursor."""
    return hashlib.sha256((url if source == "security-events" else f"{source}\n{url}").encode()).hexdigest()


def post_signed(url, secret, event_id, payload: bytes, *, now, client=None, resolver=socket.getaddrinfo):
    """POST one signed event to a subscriber; the HTTP status, or None when nothing answered.

    The request goes to the address that was checked to be public (histor.netguard: resolved
    here, then pinned with Host and SNI), with redirects and environment proxies off.
    """
    try:
        pinned = resolve_public(url, resolver=resolver)
    except ValueError:
        return None
    timestamp = str(int(now))
    signature = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    own_client = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
    try:
        with client.stream(
            "POST",
            pinned.url,
            content=payload,
            headers={
                "Host": pinned.host_header,
                "Content-Type": "application/json",
                "X-Histor-Event-ID": event_id,
                "X-Histor-Timestamp": timestamp,
                "X-Histor-Signature": "sha256=" + signature,
            },
            follow_redirects=False,
            extensions={"sni_hostname": pinned.sni_hostname} if pinned.sni_hostname else {},
        ) as response:
            return response.status_code
    except httpx.HTTPError:
        return None
    finally:
        if own_client:
            client.close()


def deliver(store, subscription, *, client=None, now=None, resolver=socket.getaddrinfo, base_url=""):
    """One ordered delivery per invocation. At least once, stable ID, lease and bounded backoff.

    Subscription URLs and secrets come from an operator-owned local configuration, or from a
    team that paid for HISTOR Watch (histor.watch_paid) and named its endpoint at checkout.
    Nothing in package metadata or a free public API can select a destination. Redirects and
    environment proxies are disabled, and the request goes to the address that was checked
    to be public (post_signed), so a subscriber's name that later resolves inward cannot turn
    the worker into a way in. Callers deduplicate X-Histor-Event-ID.

    `source` (default "security-events") and, for "changes", `watch` (server ids) choose what
    the subscription is fed; see SOURCES.
    """
    now = time.time() if now is None else now
    name, url, secret = (subscription[k] for k in ("id", "url", "secret"))
    source = subscription.get("source") or "security-events"
    if source not in SOURCES:
        raise ValueError(f"webhook source must be one of {', '.join(SOURCES)}")
    watch = subscription.get("watch") or []
    if source == "changes" and (not isinstance(watch, list) or not watch or len(watch) > 100
                                or any(not (isinstance(w, str) and re.fullmatch(r"[0-9a-f]{16}", w)) for w in watch)):
        raise ValueError("a changes subscription needs `watch`: 1-100 server ids")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or len(secret) < 32:
        raise ValueError("webhooks require a fixed HTTPS destination and a 32+ character secret")
    binding = destination_binding(source, url)
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
    if source == "changes":
        changed = store.changes(limit=1, after_id=state["cursor"], target_ids=watch)
        rows = []
        if changed:
            event_id, body = _change_event(base_url, changed[0])
            rows = [{"cursor": changed[0]["id"], "event": body}]
    else:
        rows = events(store, after=state["cursor"], limit=1)
    if not rows:
        with store.db.transaction() as tx:
            tx.execute("UPDATE webhook_cursors SET lease_until=0 WHERE id=? AND lease_token=?", (name, token))
        return {"status": "idle"}
    item = rows[0]
    event_id = item["event"]["id"]
    code = post_signed(url, secret, event_id, canonicalize(item["event"]), now=now, client=client, resolver=resolver)
    success = code is not None and 200 <= code < 300
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
