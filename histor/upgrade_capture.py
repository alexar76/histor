"""Explicit read-only HTTP capture for a local upgrade-brief bundle.

No package is installed or launched. Credentials, paid calls and automatic crawling
are not part of this command. A saved observation is unsigned local evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from histor.mcpclient import McpReader
from histor.upgrade_brief import (
    MAX_SNAPSHOT_BYTES,
    OBSERVATION_FORMAT,
    InvalidInput,
    _bounded_json,
    _validate_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture public MCP definitions to a new local observation file.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--allow-private", action="store_true", help="Explicitly permit a local/private endpoint.")
    temporary: str | None = None
    try:
        args = parser.parse_args(argv)
        parts = urlsplit(args.url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise InvalidInput("capture requires an HTTP(S) URL without credentials, query or fragment")
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
            raise InvalidInput("timeout must be finite and between 0 and 300 seconds")
        # Refuse an existing path before making a request; baselines are never replaced.
        if args.out.exists() or args.out.is_symlink():
            raise InvalidInput("capture output already exists; choose a new observation file")
        document = {"format": OBSERVATION_FORMAT, "targetId": args.target_id, "status": "unknown",
                    "extrasCoverage": "observed"}
        _validate_snapshot(document)
        observation = McpReader(timeout=args.timeout, allow_private=args.allow_private).list_tools(args.url)
        document.update(
            status="ok" if observation.status == "ok" else "failed",
            observedAt=datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            packageVersion=None,
            serverName=observation.server_info.get("name") or None,
            serverVersion=observation.server_info.get("version") or None,
            protocol=observation.protocol_version or None,
            launch={"transport": "streamable-http", "endpoint": args.url},
        )
        if observation.status == "ok":
            document["tools"] = observation.tools
        _validate_snapshot(document)
        payload = (json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise InvalidInput("observation exceeds the local prototype snapshot size limit")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".histor-observation-", dir=args.out.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        # The reducer's structural limits also apply to captures.
        _bounded_json(Path(temporary), MAX_SNAPSHOT_BYTES)
        os.link(temporary, args.out)
        print(str(args.out.resolve()))
        if observation.status != "ok":
            print("upgrade-brief capture: observation incomplete; report will mark it unknown", file=sys.stderr)
            return 1
        return 0
    except SystemExit as exc:
        return int(exc.code)
    except (InvalidInput, OSError, ValueError, UnicodeError):
        print("upgrade-brief capture: input invalid, observation too large, or output unavailable", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
