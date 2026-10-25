"""Evidence-based labels for Base USDC receipts, separate from settled amounts.

This is a local reducer, not a registry of wallet identities. Labels describe only
the supplied observations; a broad payment pattern cannot establish buyer intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

FORMAT = "histor.payer-audit/v1"
CHAIN_FORMAT = "histor.payer-chain/v1"
ANNOTATIONS_FORMAT = "histor.payer-annotations/v1"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
POLICY = {
    "id": "broad-small-singleton-transfers/v1",
    "min_transfers": 50,
    "min_recipients": 50,
    "min_singleton_recipient_fraction": "0.9",
    "small_transfer_max_atomic": 10_000,
    "min_small_transfer_fraction": "0.9",
    "calibrated": False,
}


def address(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("expected a full EVM address")
    return value.lower()


def txhash(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise ValueError("expected a full transaction hash")
    return value.lower()


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("expected an ISO timestamp with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("expected an ISO timestamp with timezone") from None
    if result.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return result


def atomic(value: Any) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("amountAtomic must be a non-negative integer")
    return int(value)


def usdc(value: int) -> str:
    return format(Decimal(value) / Decimal(1_000_000), ".6f")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def ledger_rows(ledger: dict) -> tuple[list[dict], int]:
    if not isinstance(ledger, dict) or ledger.get("network") != "eip155:8453":
        raise ValueError("this adapter requires a Base ledger: network=eip155:8453")
    seller = address(ledger.get("seller"))
    if not isinstance(ledger.get("sales"), list):
        raise ValueError("ledger.sales must be an array")
    seen: set[str] = set()
    rows, duplicate_count = [], 0
    ownership: dict[str, set[bool]] = defaultdict(set)
    for raw in ledger["sales"]:
        if not isinstance(raw, dict):
            raise ValueError("each sale must be an object")
        row_hash = digest(raw)
        if row_hash in seen:
            duplicate_count += 1
            continue
        seen.add(row_hash)
        own = raw.get("ownWallet")
        if own is not None and not isinstance(own, bool):
            raise ValueError("ownWallet must be boolean or absent")
        payer = address(raw.get("payer"))
        if own is not None:
            ownership[payer].add(own)
        at = raw.get("at")
        timestamp(at)
        service = raw.get("service")
        if service is not None and not isinstance(service, str):
            raise ValueError("service must be a string or absent")
        rows.append({"payer": payer, "seller": seller, "at": at, "own": own,
                     "transaction": txhash(raw.get("transaction")),
                     "amount_atomic": atomic(raw.get("amountAtomic")), "service": service})
    if any(len(flags) > 1 for flags in ownership.values()):
        raise ValueError("conflicting ownWallet claims for the same payer")
    return rows, duplicate_count


def decode_transfer(log: dict) -> dict | None:
    """Decode the official USDC Transfer event, including transfers relayed by a facilitator."""
    if not isinstance(log, dict):
        raise ValueError("each chain log must be an object")
    if log.get("removed") or str(log.get("address", "")).lower() != USDC:
        return None
    topics = log.get("topics")
    if not isinstance(topics, list) or len(topics) != 3 or str(topics[0]).lower() != TRANSFER:
        return None
    if any(not isinstance(t, str) or not re.fullmatch(r"0x0{24}[0-9a-fA-F]{40}", t)
           for t in topics[1:]):
        raise ValueError("invalid indexed Transfer address")
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(log.get("data", ""))):
        raise ValueError("invalid Transfer amount")
    block, index = int(log["blockNumber"], 16), int(log["logIndex"], 16)
    if block < 0 or index < 0:
        raise ValueError("negative block or log index")
    return {"payer": address("0x" + topics[1][-40:]), "recipient": address("0x" + topics[2][-40:]),
            "amount_atomic": int(log["data"], 16), "transaction": txhash(log["transactionHash"]),
            "block": block, "log_index": index}


def _chain(chain: dict | None) -> tuple[dict, list[dict], dict[str, dict]]:
    if chain is None:
        return {"available": False, "complete": False}, [], {}
    if not isinstance(chain, dict):
        raise ValueError("chain evidence must be an object")
    if chain.get("schema") != CHAIN_FORMAT or chain.get("chain_id") != 8453:
        raise ValueError("chain evidence must be histor.payer-chain/v1 on Base 8453")
    if address(chain.get("token")) != USDC:
        raise ValueError("chain evidence must describe Base USDC")
    window = chain.get("window", {})
    lo, hi = window.get("from_block"), window.get("to_block")
    if any(type(n) is not int or n < 0 for n in (lo, hi)) or lo > hi:
        raise ValueError("invalid chain observation window")
    timestamp(window.get("from_at"))
    timestamp(window.get("to_at"))
    if timestamp(window["from_at"]) > timestamp(window["to_at"]):
        raise ValueError("reversed chain timestamps")
    if type(window.get("complete")) is not bool:
        raise ValueError("chain completeness must be explicit")
    if window["complete"] and window.get("failed_ranges"):
        raise ValueError("complete chain evidence cannot contain failed ranges")
    queried = chain.get("queried_payers")
    if not isinstance(queried, list):
        raise ValueError("chain evidence must name queried_payers")
    queried = {address(payer) for payer in queried}
    if not isinstance(chain.get("logs", []), list) or not isinstance(chain.get("settlements", []), list):
        raise ValueError("chain logs and settlements must be arrays")
    seen, transfers = {}, []
    for raw in chain.get("logs", []):
        transfer = decode_transfer(raw)
        if transfer is None:
            continue
        if not lo <= transfer["block"] <= hi:
            raise ValueError("transfer outside declared observation window")
        if transfer["payer"] not in queried:
            raise ValueError("transfer does not belong to a queried payer")
        key = (transfer["transaction"], transfer["log_index"])
        if key in seen:
            if seen[key] != transfer:
                raise ValueError("conflicting chain events at the same transaction/log index")
            continue
        seen[key] = transfer
        transfers.append(transfer)
    checks = {}
    for record in chain.get("settlements", []):
        if not isinstance(record, dict):
            raise ValueError("each settlement record must be an object")
        tx = txhash(record.get("transaction"))
        result = record.get("receipt")
        if result is not None and not isinstance(result, dict):
            raise ValueError("RPC receipt must be an object or null")
        if result is not None and txhash(result.get("transactionHash")) != tx:
            raise ValueError("RPC receipt hash differs from requested transaction")
        if tx in checks:
            raise ValueError("duplicate settlement check")
        checks[tx] = record
    return {**window, "available": True, "queried_payers": sorted(queried), "rpc": chain.get("rpc_origin"),
            "source_sha256": digest(chain)}, transfers, checks


def _annotations(data: dict | None) -> list[dict]:
    if data is None:
        return []
    if not isinstance(data, dict) or data.get("schema") != ANNOTATIONS_FORMAT or not isinstance(data.get("items"), list):
        raise ValueError("annotations must be histor.payer-annotations/v1 with an items array")
    out = []
    for raw in data["items"]:
        if not isinstance(raw, dict):
            raise ValueError("each annotation must be an object")
        kind = raw.get("kind")
        if kind not in ("known-probe", "known-monitor", "example-input-match"):
            raise ValueError("unsupported annotation kind")
        source = raw.get("source", "")
        if not isinstance(source, str):
            raise ValueError("annotation source must be an HTTPS URL")
        url = urlsplit(source)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or any(char.isspace() for char in source)):
            raise ValueError("each annotation needs a public HTTPS source reference")
        if not isinstance(raw.get("transactions"), list):
            raise ValueError("annotation.transactions must be an array")
        txs = [txhash(tx) for tx in raw["transactions"]]
        if not txs:
            raise ValueError("annotations must name transactions; wallet-wide identity claims are not accepted")
        out.append({"payer": address(raw.get("payer")), "kind": kind, "source": source,
                    "transactions": txs, "note": str(raw.get("note", ""))})
    return out


def audit(ledger: dict, *, chain: dict | None = None, annotations: dict | None = None) -> dict:
    """Reduce immutable inputs. No network, database, settings, signatures or wallet keys."""
    rows, duplicates = ledger_rows(ledger)
    window, transfers, checks = _chain(chain)
    notes = _annotations(annotations)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["payer"]].append(row)
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for transfer in transfers:
        if transfer["payer"] != transfer["recipient"] and transfer["amount_atomic"] > 0:
            outgoing[transfer["payer"]].append(transfer)
    payers = []
    used_settlements: set[tuple[str, int]] = set()
    for payer, sales in sorted(groups.items()):
        flags = {s["own"] for s in sales}
        ownership = "seller-reported-own" if True in flags else "seller-reported-external" if False in flags else "unknown"
        txs = {s["transaction"] for s in sales}
        evidence = []
        verified = 0
        settlement_details = []
        for sale in sales:
            record = checks.get(sale["transaction"], {})
            receipt = record.get("receipt")
            status = "not-checked"
            if "error" in record:
                status = "unavailable"
            elif "receipt" in record:
                status = "not-found" if receipt is None else "mismatch"
                if receipt and receipt.get("status") == "0x1" and not receipt.get("removed"):
                    for log in receipt.get("logs", []):
                        event = decode_transfer(log)
                        if event is None:
                            continue
                        key = (event["transaction"], event["log_index"])
                        if (event["transaction"] == sale["transaction"] and event["payer"] == payer
                                and event["recipient"] == sale["seller"]
                                and event["amount_atomic"] == sale["amount_atomic"]
                                and key not in used_settlements
                                and event["block"] == int(receipt["blockNumber"], 16)
                                and event["block"] <= window.get("to_block", -1)):
                            used_settlements.add(key)
                            status = "verified"
                            verified += 1
                            break
            settlement_details.append({"transaction": sale["transaction"], "at": sale["at"],
                                       "amount_atomic": sale["amount_atomic"], "status": status})
        hits = []
        for note in notes:
            overlap = sorted(txs.intersection(note["transactions"])) if note["payer"] == payer else []
            if overlap:
                hits.append({**note, "transactions": overlap})
        evidence.extend({"code": "annotation:" + h["kind"], "basis": "source-reported",
                         "source": h["source"], "transactions": h["transactions"], "note": h["note"]}
                        for h in hits)
        activity = outgoing.get(payer, [])
        activity_available = payer in window.get("queried_payers", [])
        recipients = Counter(t["recipient"] for t in activity)
        singletons = sum(n == 1 for n in recipients.values())
        small = sum(t["amount_atomic"] <= POLICY["small_transfer_max_atomic"] for t in activity)
        singleton_fraction = Decimal(singletons) / len(recipients) if recipients else Decimal(0)
        small_fraction = Decimal(small) / len(activity) if activity else Decimal(0)
        broad = (len(activity) >= POLICY["min_transfers"] and len(recipients) >= POLICY["min_recipients"]
                 and Decimal(singletons) / len(recipients) >= Decimal(POLICY["min_singleton_recipient_fraction"])
                 and Decimal(small) / len(activity) >= Decimal(POLICY["min_small_transfer_fraction"]))
        if broad:
            evidence.append({"code": "broad-small-singleton-transfers", "basis": "observed-pattern",
                             "transfers": len(activity), "recipients": len(recipients),
                             "singleton_recipients": singletons, "small_transfers": small,
                             "policy": POLICY["id"], "partial_window": not window["complete"]})
        signals = []
        if activity_available:
            # Descriptive flags keep useful evidence visible even when the stricter label rule fails.
            if len(recipients) >= POLICY["min_recipients"]:
                signals.append("broad-recipient-spread")
            if small_fraction > Decimal("0.5"):
                signals.append("majority-transfers-at-most-0.01-USDC")
            if singleton_fraction > Decimal("0.5"):
                signals.append("majority-recipients-paid-once")
        probe_txs = {tx for h in hits if h["kind"] == "known-probe" for tx in h["transactions"]}
        monitor_txs = {tx for h in hits if h["kind"] == "known-monitor" for tx in h["transactions"]}
        examples = {tx for h in hits if h["kind"] == "example-input-match" for tx in h["transactions"]}
        if ownership == "seller-reported-own":
            label = "operator-owned"
        elif probe_txs == txs:
            label = "known-probe"
        elif monitor_txs == txs:
            label = "reported-monitor"
        elif broad or len(examples) >= 2:
            label = "probe-like"
        else:
            label = "unknown"
        amount = sum(s["amount_atomic"] for s in sales)
        verified_amount = sum(s["amount_atomic"] for s in settlement_details if s["status"] == "verified")
        payers.append({"payer": payer, "ownership": ownership, "label": label,
                       "buyer_intent": "unknown", "sales": len(sales), "amount_atomic": amount,
                       "amount_usdc": usdc(amount), "first_at": min(sales, key=lambda s: timestamp(s["at"]))["at"],
                       "last_at": max(sales, key=lambda s: timestamp(s["at"]))["at"],
                       "services": dict(sorted(Counter(s["service"] or "unspecified" for s in sales).items())),
                       "settlements_verified": verified, "verified_amount_atomic": verified_amount,
                       "verified_amount_usdc": usdc(verified_amount), "settlements": settlement_details,
                       "pattern_signals": signals,
                       "chain_activity": {"available": activity_available,
                                          "complete": activity_available and window["complete"],
                                          "transfers": len(activity) if activity_available else None,
                                          "recipients": len(recipients) if activity_available else None,
                                          "singleton_recipients": singletons if activity_available else None,
                                          "small_transfers": small if activity_available else None,
                                          "singleton_recipient_fraction": format(singleton_fraction, ".6f") if activity_available else None,
                                          "small_transfer_fraction": format(small_fraction, ".6f") if activity_available else None},
                       "evidence": evidence})
    external = [p for p in payers if p["ownership"] == "seller-reported-external"]
    total_amount = sum(p["amount_atomic"] for p in payers)
    warnings = []
    claimed = ledger.get("totals", {})
    actual = {"sales": len(rows), "salesFromOwnWallets": sum(s["own"] is True for s in rows),
              "externalPayers": len(external)}
    for key, value in actual.items():
        if key in claimed and claimed[key] != value:
            warnings.append(f"seller totals.{key}={claimed[key]} differs from deduplicated rows={value}")
    if any(p["ownership"] == "unknown" for p in payers):
        warnings.append("missing ownWallet flags: unknown ownership is not counted as external")
    return {"schema": FORMAT, "seller": address(ledger["seller"]), "network": "eip155:8453",
            "asset": {"address": USDC, "decimals": 6, "symbol": "USDC"},
            "ledger_generated_at": ledger.get("generatedAt"), "input_sha256": digest(ledger),
            "annotations_sha256": digest(annotations) if annotations is not None else None,
            "policy": POLICY.copy(), "chain_window": window,
            "summary": {**actual, "amount_atomic": total_amount, "amount_usdc": usdc(total_amount),
                        "external_sales": sum(p["sales"] for p in external),
                        "external_amount_atomic": sum(p["amount_atomic"] for p in external),
                        "external_amount_usdc": usdc(sum(p["amount_atomic"] for p in external)),
                        "external_settlements_verified": sum(p["settlements_verified"] for p in external),
                        "external_verified_amount_atomic": sum(p["verified_amount_atomic"] for p in external),
                        "external_verified_amount_usdc": usdc(sum(p["verified_amount_atomic"] for p in external)),
                        "external_labels": dict(sorted(Counter(p["label"] for p in external).items())),
                        "duplicate_receipts_ignored": duplicates},
            "payers": payers, "warnings": warnings,
            "limits": ["Seller ownership flags and annotations are source claims, not independently established identities.",
                       "Labels describe these observations; they do not establish a wallet's permanent purpose.",
                       "Recipients are USDC addresses, not verified sellers or x402 services.",
                       "The pattern rule is an uncalibrated heuristic, not a probability or proof of intent.",
                       "All amounts remain in totals regardless of label. Unknown is not evidence of no demand.",
                       "RPC verification trusts the supplied Base node; it is not an independently verified chain proof."]}


def markdown(report: dict) -> str:
    summary, window = report["summary"], report["chain_window"]
    lines = ["# Payer evidence report", "", f"Seller: `{report['seller']}` · Base USDC.",
             f"Ledger generated: {report['ledger_generated_at'] or 'unspecified'}.", "",
             f"Seller-reported external: **{summary['externalPayers']} wallets**, "
             f"**{summary['external_sales']} sales**, **{summary['external_amount_usdc']} USDC**.",
             f"RPC-verified external transfers: **{summary['external_settlements_verified']}**, "
             f"**{summary['external_verified_amount_usdc']} USDC**.", ""]
    if window["available"]:
        lines += [f"Chain window: {window['from_at']} — {window['to_at']}; blocks "
                  f"{window['from_block']}–{window['to_block']}; complete: **{window['complete']}**.", ""]
    else:
        lines += ["Chain evidence unavailable; settlement and cross-recipient activity are not verified.", ""]
    lines += ["| Payer | Label | Sales | USDC | Verified settlements |",
              "|---|---|---:|---:|---:|"]
    for payer in report["payers"]:
        if payer["ownership"] == "seller-reported-own":
            continue
        lines.append(f"| `{payer['payer']}` | {payer['label']} | {payer['sales']} | "
                     f"{payer['amount_usdc']} | {payer['settlements_verified']}/{payer['sales']} |")
    for payer in report["payers"]:
        if payer["ownership"] == "seller-reported-own":
            continue
        activity = payer["chain_activity"]
        lines += ["", f"## {payer['payer']}", "", f"Label: **{payer['label']}**; buyer intent: **unknown**.",
                  f"Ledger observations: {payer['first_at']} — {payer['last_at']}."]
        if activity["available"]:
            lines += [f"Observed outgoing USDC transfers: {activity['transfers']}; distinct recipients: "
                      f"{activity['recipients']}; singleton recipients: {activity['singleton_recipients']}; "
                      f"transfers ≤0.01 USDC: {activity['small_transfers']}. Complete window: {activity['complete']}."]
        else:
            lines += ["Cross-recipient chain activity was not collected for this payer."]
        if payer["pattern_signals"]:
            lines += ["Descriptive signals: " + ", ".join(payer["pattern_signals"]) + "."]
        for evidence in payer["evidence"]:
            # Source annotations are data; escape Markdown structure rather than embedding their text as markup.
            note = str(evidence.get("note", "")).replace("\n", " ").replace("|", "\\|")
            note = note.replace("<", "&lt;").replace(">", "&gt;")
            note = re.sub(r"([`*\[\]#])", r"\\\1", note)
            source = evidence.get("source")
            ref = f" ([source]({source.replace('(', '%28').replace(')', '%29')}))" if source else ""
            lines += [f"- {evidence['code']} ({evidence['basis']}){ref}: {note}"]
        lines += ["", "Settlement references:"]
        lines += [f"- [{s['transaction']}](https://basescan.org/tx/{s['transaction']}) — "
                  f"{usdc(s['amount_atomic'])} USDC; {s['status']}" for s in payer["settlements"]]
    lines += ["", "## Limits", "", *["- " + text for text in report["limits"]]]
    if report["warnings"]:
        lines += ["", "## Input warnings", "", *["- " + text for text in report["warnings"]]]
    return "\n".join(lines) + "\n"
