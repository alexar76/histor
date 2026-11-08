#!/usr/bin/env python3
"""Compare another observer's MCP tool-set digests with HISTOR's public record: histor/digest_xcheck.py.

    python3 scripts/xcheck_digests.py THEIRS [--histor URL] [--window-hours 24] [--out report.json] [--md report.md]

The module is loaded by path, so this runs from a checkout with Python 3.11+ and nothing installed. The input
formats, the verdicts and the exit status are in the module's docstring and in docs/cross-check.md.
"""

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "histor" / "digest_xcheck.py"
_spec = importlib.util.spec_from_file_location("digest_xcheck", _PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["digest_xcheck"] = _mod
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    sys.exit(_mod.main())
