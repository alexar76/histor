"""Bounded, read-only Base RPC collection for ``histor payer-audit``.

Run as ``python -m histor.payer_collect --receipts receipts.json --rpc URL -o chain.json``.
Only eth_chainId, eth_blockNumber, eth_getBlockByNumber, eth_getLogs and
eth_getTransactionReceipt are permitted. No signing library, wallet or payment endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from histor.payer_audit import CHAIN_FORMAT, TRANSFER, USDC, ledger_rows

METHODS = {"eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getLogs", "eth_getTransactionReceipt"}
MAX_CAPTURE_BYTES = 24 * 1024 * 1024


class Rpc:
    def __init__(self, url: str, *, budget: int = 2000, pause: float = 0.35):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("--rpc must be an HTTPS endpoint without userinfo")
        self.url = url
        self.origin = f"https://{parts.netloc}"
        self.budget, self.pause = budget, pause
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.requests = 0
        self.reads: list[dict] = []

    def call(self, method: str, params: list):
        if method not in METHODS:
            raise ValueError("RPC method is not read-only or is outside this collector's allowlist")
        for attempt in range(3):
            with self.lock:
                if self.requests >= self.budget:
                    raise ValueError("RPC request budget exhausted")
                self.requests += 1
                identifier = self.requests
                wait = max(0.0, self.next_at - time.monotonic())
                self.next_at = max(self.next_at, time.monotonic()) + self.pause
            if wait:
                time.sleep(wait)
            body = json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}).encode()
            request = urllib.request.Request(self.url, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "HISTOR-payer-audit/1.0"})
            record = {"method": method, "params": params, "at": datetime.now(UTC).isoformat()}
            retry_after = 1.0
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("RPC response exceeds 16 MiB")
                record["sha256"] = hashlib.sha256(raw).hexdigest()
                data = json.loads(raw)
                if not isinstance(data, dict) or data.get("id") != identifier or data.get("jsonrpc") != "2.0":
                    raise ValueError("malformed or mismatched JSON-RPC response")
                if "error" in data:
                    raise ValueError(f"RPC error: {data['error']}")
                if "result" not in data:
                    raise ValueError("RPC response has no result")
                record["ok"] = True
                with self.lock:
                    self.reads.append(record)
                return data["result"]
            except (OSError, ValueError) as exc:
                record["ok"] = False
                # Do not echo endpoint URLs, which can carry an operator's RPC API key.
                record["error"] = str(exc).replace(self.url, self.origin)[:1000]
                with self.lock:
                    self.reads.append(record)
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        retry_after = max(1.0, min(30.0, float(exc.headers.get("Retry-After", "1"))))
                    except ValueError:
                        retry_after = 1.0
                if attempt == 2:
                    raise ValueError(record["error"]) from None
                with self.lock:
                    self.next_at = max(self.next_at, time.monotonic() + retry_after * (attempt + 1))
        raise AssertionError("unreachable")


def collect(ledger: dict, rpc: Rpc, *, days: float = 14, chunk_size: int = 1000) -> dict:
    if not 0 < days <= 31 or not 1 <= chunk_size <= 1000:
        raise ValueError("days must be in (0,31], chunk size in [1,1000]")
    rows, _ = ledger_rows(ledger)
    payers = sorted({r["payer"] for r in rows if r["own"] is not True})
    if len(payers) > 100:
        raise ValueError("collector is bounded to 100 non-own payers per run")
    if int(rpc.call("eth_chainId", []), 16) != 8453:
        raise ValueError("RPC endpoint is not Base mainnet")
    end = int(rpc.call("eth_blockNumber", []), 16) - 12
    if end < 0:
        raise ValueError("chain is too short for twelve-block confirmation buffer")
    end_header = rpc.call("eth_getBlockByNumber", [hex(end), False])
    end_at = int(end_header["timestamp"], 16)
    target = end_at - int(days * 86400)
    # Base targets 2-second blocks. Verify the estimate; binary search if the boundary differs.
    lo = max(0, end - int(days * 86400 / 2))
    start_header = rpc.call("eth_getBlockByNumber", [hex(lo), False])
    start_at = int(start_header["timestamp"], 16)
    if not target <= start_at <= target + 2:
        left, right = 0, end
        while left < right:
            mid = (left + right) // 2
            header = rpc.call("eth_getBlockByNumber", [hex(mid), False])
            if int(header["timestamp"], 16) < target:
                left = mid + 1
            else:
                right = mid
        lo = left
        start_header = rpc.call("eth_getBlockByNumber", [hex(lo), False])
        start_at = int(start_header["timestamp"], 16)
    topics = [TRANSFER, ["0x" + payer[2:].zfill(64) for payer in payers]]
    ranges = [(start, min(start + chunk_size - 1, end)) for start in range(lo, end + 1, chunk_size)] if payers else []
    capture_lock = threading.Lock()
    capture_stopped = threading.Event()
    captured_bytes = 0

    def reserve(value):
        nonlocal captured_bytes
        size = len(json.dumps(value, separators=(",", ":")).encode())
        with capture_lock:
            if capture_stopped.is_set() or captured_bytes + size > MAX_CAPTURE_BYTES:
                capture_stopped.set()
                return False
            captured_bytes += size
            return True

    def read_range(pair):
        if capture_stopped.is_set():
            raise ValueError("total evidence capture limit reached; range not queried")
        start, stop = pair
        result = rpc.call("eth_getLogs", [{"address": USDC, "fromBlock": hex(start),
                                           "toBlock": hex(stop), "topics": topics}])
        if not isinstance(result, list):
            raise ValueError("eth_getLogs did not return a list")
        if not reserve(result):
            raise ValueError("total evidence capture limit reached; range not retained")
        return result

    logs, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(read_range, pair): pair for pair in ranges}
        for completed, future in enumerate(as_completed(jobs), 1):
            pair = jobs[future]
            try:
                logs.extend(future.result())
            except (OSError, ValueError, KeyError, TypeError) as exc:
                failures.append({"from_block": pair[0], "to_block": pair[1], "error": str(exc)[:1000]})
            if completed % 50 == 0 or completed == len(ranges):
                print(f"payer collect: {completed}/{len(ranges)} ranges; {len(logs)} logs; "
                      f"{len(failures)} unavailable ranges", file=sys.stderr, flush=True)
    settlements = []
    for tx in sorted({r["transaction"] for r in rows if r["own"] is not True}):
        record = {"transaction": tx}
        try:
            if capture_stopped.is_set():
                raise ValueError("total evidence capture limit reached; settlement not queried")
            receipt = rpc.call("eth_getTransactionReceipt", [tx])
            if not reserve(receipt):
                raise ValueError("total evidence capture limit reached; settlement not retained")
            record["receipt"] = receipt
        except (OSError, ValueError) as exc:
            record["error"] = str(exc)[:1000]
        settlements.append(record)
    stable = rpc.call("eth_getBlockByNumber", [hex(end), False])
    if stable.get("hash") != end_header.get("hash"):
        failures.append({"error": "end block hash changed during collection; rerun against a stable chain"})
    def iso(seconds):
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    return {"schema": CHAIN_FORMAT, "chain_id": 8453, "token": USDC, "rpc_origin": rpc.origin,
            "collected_at": datetime.now(UTC).isoformat(),
            "window": {"from_block": lo, "to_block": end, "from_at": iso(start_at), "to_at": iso(end_at),
                       "from_block_hash": start_header["hash"], "to_block_hash": end_header["hash"],
                       "complete": not failures, "failed_ranges": failures, "confirmations_buffer": 12},
            "queried_payers": payers, "logs": logs, "settlements": settlements,
            "capture_limit_bytes": MAX_CAPTURE_BYTES,
            "rpc_reads": sorted(rpc.reads, key=lambda r: (r["at"], r["method"]))}


def main(argv=None) -> int:
    from histor.payer_audit_cli import MAX_INPUT_BYTES, _read

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--rpc", required=True)
    parser.add_argument("--days", type=float, default=14)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--max-requests", type=int, default=2000)
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.max_requests <= 5000:
            raise ValueError("max requests must be in [1,5000]")
        if Path(args.output).resolve() == Path(args.receipts).resolve():
            raise ValueError("output must not overwrite the receipts input")
        result = collect(_read(args.receipts), Rpc(args.rpc, budget=args.max_requests),
                         days=args.days, chunk_size=args.chunk_size)
        path = Path(args.output)
        temporary = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(text.encode()) > MAX_INPUT_BYTES:
            raise ValueError("collected evidence exceeds the reducer's total input limit")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        return 0 if result["window"]["complete"] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"payer collect: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
