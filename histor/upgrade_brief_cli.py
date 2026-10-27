"""``histor upgrade-brief --watchlist bundle/watchlist.json --out-dir output`` (offline)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from histor.upgrade_brief import InvalidInput, build_report, markdown
from histor.upgrade_brief_i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES


def _write(path: Path, content: str) -> None:
    # Replace the directory entry itself, never follow an existing output symlink.
    descriptor, temporary = tempfile.mkstemp(prefix=".upgrade-brief-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Personal offline review of two saved MCP observations.")
    parser.add_argument("--watchlist", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--lang", choices=SUPPORTED_LANGUAGES, default=DEFAULT_LANGUAGE,
                        help=f"Report and checklist language (default: {DEFAULT_LANGUAGE}).")
    try:
        args = parser.parse_args(argv)
        report = build_report(args.watchlist, lang=args.lang)
        out = args.out_dir.resolve()
        sources = {args.watchlist.resolve()}
        for dep in report["dependencies"]:
            for src in dep["sources"].values():
                if src["path"]:
                    sources.add((args.watchlist.resolve().parent / src["path"]).resolve())
        if any((out / name).resolve() in sources for name in ("report.json", "report.md")):
            raise InvalidInput("output would overwrite an input file")
        out.mkdir(parents=True, exist_ok=True)
        _write(out / "report.json", json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n")
        _write(out / "report.md", markdown(report))
    except SystemExit as exc:
        return int(exc.code)
    except (InvalidInput, OSError, ValueError) as exc:
        # No raw JSON/parser snippets or launch/environment content in diagnostics.
        message = str(exc) if isinstance(exc, InvalidInput) else "report could not be written"
        print("upgrade-brief: " + message, file=sys.stderr)
        return 2
    print(str(out / "report.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
