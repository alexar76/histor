"""Fetch the immutable public tool lists of HISTOR change 28848; execute no server tools."""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

from histor.subject import tool_set_digest

SNAPSHOTS = {
    "before": "sha256-IP5W+kYoXogt7RDDPbG0FmUE97uEAqyIdxDoPjuocUA=",
    "after": "sha256-zIV5QNaJd0IQp+5yFiYc4gtjQVAtMjfSc3Kq2tcE5uc=",
}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, digest in SNAPSHOTS.items():
        url = "https://histor.modelmarket.dev/api/v1/toolsets/" + urllib.parse.quote(digest, safe="")
        request = urllib.request.Request(url, headers={"User-Agent": "HISTOR-cost-example/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("tool list exceeds the example's size limit")
        tools = json.loads(raw)
        if not isinstance(tools, list):
            raise ValueError("expected a tool array")
        actual, _ = tool_set_digest(tools)
        if actual != digest:
            raise ValueError(f"digest mismatch for {name}: {actual}")
        (args.out / f"{name}-tools.json").write_text(json.dumps(tools, ensure_ascii=False, indent=2) + "\n")
        print(f"{name}: {len(tools)} tools, {actual}")


if __name__ == "__main__":
    main()
