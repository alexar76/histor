"""Offline ``histor payer-audit``: seller receipts + optional chain evidence + sourced annotations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from histor.payer_audit import audit, markdown

MAX_INPUT_BYTES = 32 * 1024 * 1024


def _read(path: str) -> dict:
    with Path(path).open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("input must be a JSON object")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True, help="Base USDC seller ledger JSON")
    parser.add_argument("--chain", help="histor.payer-chain/v1 evidence JSON")
    parser.add_argument("--annotations", help="transaction-scoped, sourced annotations JSON")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("-o", "--output", help="write report to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        if args.output and any(Path(args.output).resolve() == Path(path).resolve()
                               for path in (args.receipts, args.chain, args.annotations) if path):
            raise ValueError("output must not overwrite an input file")
        result = audit(_read(args.receipts), chain=_read(args.chain) if args.chain else None,
                       annotations=_read(args.annotations) if args.annotations else None)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n" if args.format == "json" else markdown(result)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"payer-audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
