"""Money-tool changes: the part of HISTOR's change feed that touches tools which move value or hold keys.

    python -m histor.money_changes --state DIR --out FEED.json [--api https://histor.modelmarket.dev]

HISTOR records every change of every MCP server's tool list it reads (about 6,000 a day). Most are
copy edits. The ones that matter to anyone whose agent can pay are few: a server adds a tool that
sends, swaps or withdraws; a tool that moves value changes its description or schema; a tool's
description gains a wallet address or replaces one; a description starts mentioning private keys or
seed phrases. This module reads the public change feed incrementally (``/api/v1/changes?after=``),
keeps the changes that match the rules below, adds each server's name and endpoint, and writes a
compact feed of the last ``--days`` days (default 7) that fits in one fetch of an agent sandbox.

Rules ``money-tools/v1`` work on what a change row already says (tool names and the text of
description edits); they read no tool schema and call no model, so anyone can recompute them:

- a tool name *moves value* when it has no read or analysis word (get, list, check, verify, quote,
  simulation, alerts, …) and has a value-moving verb (swap, withdraw, stake, mint, borrow, repay,
  liquidate), or a payment verb (send, sign, approve, pay, buy, sell, trade, deposit, claim, …) or
  transfer / bridge / burn together with a crypto word (token, usdc, eth, wallet, tx, onchain, …);
- it *creates or imports a wallet* when a wallet word goes with create, provision, register, import
  or export; it *handles keys* when it names a private key, mnemonic, seed phrase, keypair or keystore;
- in a description edit, an EVM (0x + 40 hex) or Bitcoin bech32 address that is in the new text and
  not in the old one (well-known token contracts such as USDC are not counted) is ``address_added``, or ``address_replaced`` when the edit also removed one;
  a phrase about private keys or seed phrases that the old text did not have is ``mentions_keys``
  (often a warning; the added text is kept so a reader can tell).

Severity: high for an address added or replaced and for a tool added that handles keys; medium for a
value-moving or wallet tool added or modified; low for mentions_keys and removals.
Standard library only, so it runs on the host beside the log, reading the public API like anyone.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "histor.money-tool-changes/v1"
RULES = "money-tools/v1"
API = "https://histor.modelmarket.dev"
PAGE = 200
MAX_PAGES = 200  # 40,000 changes per run: a week of backlog at today's rate
MAX_ITEMS = 400
#: An agent sandbox takes 256 KiB per fetch; the feed stays under this, dropping its oldest items.
MAX_FEED_BYTES = 200_000
USER_AGENT = "histor-money-changes/1 (+https://histor.modelmarket.dev)"

VALUE_VERBS = {"swap", "withdraw", "stake", "unstake", "mint", "borrow", "repay", "liquidate"}
#: Words that also mean something else (an airport transfer, a bridge in a structure, a domain transfer):
#: they count only beside a crypto word.
AMBIGUOUS_VERBS = {"transfer", "bridge", "burn"}
PAY_VERBS = {"send", "sign", "approve", "pay", "buy", "sell", "trade", "deposit", "claim", "execute",
             "broadcast", "fund", "redeem", "settle", "withdrawal"}
WALLET_VERBS = {"create", "provision", "register", "import", "export", "generate", "new"}
WALLET_WORDS = {"wallet", "wallets"}
KEY_WORDS = {"privatekey", "mnemonic", "keypair", "keystore", "seedphrase"}
CRYPTO = {"token", "tokens", "eth", "ether", "usdc", "usdt", "sol", "btc", "bitcoin", "crypto", "wallet",
          "tx", "transaction", "transactions", "onchain", "chain", "coin", "coins", "nft", "erc20", "defi",
          "gas", "lamports", "wei", "solana", "ethereum", "polygon", "spl", "jetton", "ton", "dex", "perp",
          "perps", "position", "polymarket", "hyperliquid", "x402", "usd", "stablecoin", "lightning", "sats"}
READ_VERBS = {"get", "list", "check", "verify", "search", "find", "analyze", "analysis", "analytics",
              "quote", "estimate", "track", "tracker", "monitor", "balance", "balances", "price", "prices",
              "history", "info", "lookup", "scan", "simulate", "preview", "status", "read", "fetch",
              "leaderboard", "revenue", "explain", "decode", "calculate", "validate", "inspect", "watch",
              "count", "show", "describe", "query", "is", "has", "can", "will", "should", "match",
              "audit", "consistency", "simulation", "report", "alert", "alerts", "intel", "safety", "risk",
              "detect", "detector", "compare", "classify", "score"}
#: Token contracts that descriptions name as the asset, not as a payee: USDC (Base, Ethereum, Arbitrum,
#: Optimism, Polygon), USDT (Ethereum), WETH (Base, OP chains) and the zero address.
KNOWN_CONTRACTS = {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                   "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
                   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", "0xdac17f958d2ee523a2206206994597c13d831ec7",
                   "0x4200000000000000000000000000000000000006", "0x" + "0" * 40}
EVM = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
BECH32 = re.compile(r"\bbc1[02-9ac-hj-np-z]{11,71}\b")
KEY_PHRASE = re.compile(r"private keys?|seed phrases?|recovery phrases?|mnemonic|secret recovery", re.I)
SEVERITY = {"address_replaced": 3, "address_added": 3, "added_handles_keys": 3,
            "added_moves_value": 2, "added_creates_or_imports_wallet": 2, "modified_moves_value": 2,
            "modified_creates_or_imports_wallet": 2, "modified_handles_keys": 2,
            "mentions_keys": 1, "removed_moves_value": 1, "removed_creates_or_imports_wallet": 1,
            "removed_handles_keys": 1}
LEVEL = {3: "high", 2: "medium", 1: "low"}


def name_tokens(name: Any) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or ""))
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def tool_kind(name: Any) -> str | None:
    """moves_value, creates_or_imports_wallet, handles_keys or None, from the tool's name alone."""
    toks = name_tokens(name)
    if not toks:
        return None
    words = set(toks)
    joined = "".join(toks)
    if (words & KEY_WORDS or ("private" in words and "key" in words) or ("seed" in words and "phrase" in words)
            or "privatekey" in joined or "seedphrase" in joined):
        return "handles_keys"
    if words & READ_VERBS:  # verify_usdt_transfer, bridge_quote: reading about money, not moving it
        return None
    if words & WALLET_WORDS and words & WALLET_VERBS:
        return "creates_or_imports_wallet"
    if words & VALUE_VERBS:
        return "moves_value"
    if words & (PAY_VERBS | AMBIGUOUS_VERBS) and words & CRYPTO:
        return "moves_value"
    return None


def _edit_text(ops: Any) -> tuple[str, str]:
    """(added text, old text) from a description diff: [["=", t], ["+", t], ["-", t], …].

    The old text is what stayed plus what was removed: something already in the description is not new."""
    plus, old = [], []
    for op in ops if isinstance(ops, list) else []:
        if isinstance(op, list) and len(op) == 2 and isinstance(op[1], str):
            if op[0] == "+":
                plus.append(op[1])
            elif op[0] in ("-", "="):
                old.append(op[1])
    return " ".join(plus), " ".join(old)


def _removed_text(ops: Any) -> str:
    return " ".join(op[1] for op in (ops if isinstance(ops, list) else [])
                    if isinstance(op, list) and len(op) == 2 and op[0] == "-" and isinstance(op[1], str))


def _addresses(text: str) -> set[str]:
    found = {a.lower() for a in EVM.findall(text)} | {a.lower() for a in BECH32.findall(text)}
    return found - KNOWN_CONTRACTS


def classify(row: dict[str, Any]) -> dict[str, Any] | None:
    """The flags and tools of one change row, or None when it touches no money tool."""
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    flags: set[str] = set()
    tools: list[dict[str, Any]] = []
    for change in ("added", "removed"):
        for name in summary.get(change) or []:
            kind = tool_kind(name)
            if kind:
                flags.add(change + "_" + kind)
                tools.append({"tool": str(name)[:200], "change": change, "kind": kind})
    for mod in summary.get("modified") or []:
        if not isinstance(mod, dict):
            continue
        name = str(mod.get("tool") or "")[:200]
        fields = mod.get("fields") if isinstance(mod.get("fields"), dict) else {}
        kind = tool_kind(name)
        entry: dict[str, Any] = {"tool": name, "change": "modified", "kind": kind or "text",
                                 "fields": sorted(str(f) for f in fields)}
        plus, old = _edit_text(fields.get("description"))
        removed = _removed_text(fields.get("description"))
        new = sorted(_addresses(plus) - _addresses(old))
        gone = sorted(_addresses(removed) - _addresses(plus))
        noted = False
        if new:
            flags.add("address_replaced" if gone else "address_added")
            entry["new_addresses"], entry["removed_addresses"] = new[:5], gone[:5]
            noted = True
        found = KEY_PHRASE.search(plus)
        if found and not KEY_PHRASE.search(old):
            flags.add("mentions_keys")
            start = max(0, found.start() - 100)
            entry["added_text"] = plus[start:found.end() + 100].strip()
            noted = True
        if kind:
            flags.add("modified_" + kind)
            noted = True
        if noted:
            tools.append(entry)
    if not flags:
        return None
    level = max(SEVERITY.get(f, 1) for f in flags)
    return {"flags": sorted(flags), "severity": LEVEL[level], "tools": tools[:20]}


# -- the feed -------------------------------------------------------------------------------------


def _get(api: str, path: str, *, attempts: int = 3) -> Any:
    url = api.rstrip("/") + path
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as reply:  # noqa: S310 — https API, fixed host
                return json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt + 1 == attempts:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise
        time.sleep(2 * (attempt + 1))
    return None


def _server(api: str, target_id: str, cache: dict[str, Any]) -> dict[str, Any]:
    if target_id not in cache:
        doc = _get(api, "/api/v1/servers/" + target_id) or {}
        server = doc.get("server") if isinstance(doc.get("server"), dict) else {}
        cache[target_id] = {k: server.get(k) for k in ("name", "endpoint", "website", "repository")}
    return cache[target_id]


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def update(api: str, state: dict[str, Any], *, days: int = 7, now: datetime | None = None,
           fetch_server=None, bootstrap_days: int = 1, pause: float = 0.5) -> dict[str, Any]:
    """Read the changes after the state's cursor, add the matching ones, drop what fell out of the window."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    items: list[dict[str, Any]] = list(state.get("items") or [])
    cache: dict[str, Any] = dict(state.get("servers") or {})
    cursor = state.get("cursor")
    if cursor is None:  # first run: start bootstrap_days back (about 6,500 changes a day), not a whole window
        newest = (_get(api, "/api/v1/changes?limit=1") or {}).get("changes") or []
        cursor = max(0, int(newest[0]["id"]) - 6500 * min(days, bootstrap_days)) if newest else 0
    scanned = 0
    for _ in range(MAX_PAGES):
        page = (_get(api, f"/api/v1/changes?limit={PAGE}&after={cursor}") or {}).get("changes") or []
        if not page:
            break
        for row in sorted(page, key=lambda r: int(r["id"])):
            cursor = max(cursor, int(row["id"]))
            scanned += 1
            found = classify(row)
            if not found:
                continue
            at = _parse(str(row.get("observed_at") or ""))
            if at is None or at < cutoff:
                continue
            target = str(row.get("target_id") or "")
            server = (fetch_server or _server)(api, target, cache) if target else {}
            items.append({"change_id": int(row["id"]), "observed_at": row.get("observed_at"),
                          "target_id": target, **{k: v for k, v in server.items() if v},
                          "page": api.rstrip("/") + "/s/" + target, **found})
        if len(page) < PAGE:
            break
        time.sleep(pause)  # every page is a read the public log serves to everyone else too
    kept = sorted((i for i in items if (_parse(str(i.get("observed_at"))) or now) >= cutoff),
                  key=lambda i: -int(i["change_id"]))
    seen: set[int] = set()
    unique = [i for i in kept if not (i["change_id"] in seen or seen.add(i["change_id"]))][:MAX_ITEMS]
    live_targets = {i["target_id"] for i in unique}
    return {"cursor": cursor, "items": unique, "scanned_last_run": scanned,
            "servers": {k: v for k, v in cache.items() if k in live_targets}}


def feed(state: dict[str, Any], *, api: str, days: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    items = list(state["items"])
    doc = _feed_doc(state, items, api=api, days=days, now=now)
    while items and len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_FEED_BYTES:
        items = items[: max(0, len(items) - max(1, len(items) // 10))]  # newest first: drop the oldest tenth
        doc = _feed_doc(state, items, api=api, days=days, now=now)
    return doc


def _feed_doc(state: dict[str, Any], items: list[dict[str, Any]], *, api: str, days: int,
              now: datetime) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "rules": RULES,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": api.rstrip("/") + "/api/v1/changes",
        "cursor": state["cursor"],
        "window_days": days,
        "counts": {level: sum(1 for i in items if i["severity"] == level) for level in ("high", "medium", "low")},
        "about": "Changes of public MCP servers' tool lists that touch tools which move value or hold keys, "
                 "by the rules in histor/money_changes.py (" + RULES + "). A flag says what changed, not "
                 "that the server is malicious; open the page for the full change.",
        "items": items,
    }


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default=API)
    ap.add_argument("--state", required=True, type=Path, help="directory for the cursor and cache")
    ap.add_argument("--out", required=True, type=Path, help="where to write the feed (JSON)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--bootstrap-days", type=int, default=1, help="how far back the first run starts")
    args = ap.parse_args(argv)
    if not 1 <= args.days <= 30:
        ap.error("--days must be 1 to 30")
    state_file = args.state / "money-changes-state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    new = update(args.api, state, days=args.days, bootstrap_days=args.bootstrap_days)
    _write(state_file, new)
    _write(args.out, feed(new, api=args.api, days=args.days))
    print(f"money changes: scanned {new['scanned_last_run']}, cursor {new['cursor']}, "
          f"{len(new['items'])} in the window", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
