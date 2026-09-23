"""Write shields.io endpoint JSON for the tests and coverage badges from one CI run.

    python scripts/badge_json.py junit.xml coverage.json docs/landing/badges
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(junit: str, coverage: str, out: str) -> int:
    """Always writes both badges — a red one when the run failed or crashed — and exits 0.

    The workflow publishes the badges and only then fails the run: a failing suite that stopped
    the pipeline before this step would leave last week's green badge up (audit F43).
    """
    Path(out).mkdir(parents=True, exist_ok=True)
    try:
        root = ET.parse(junit).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        tests, failures, errors, skipped = (int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped"))
        passed = tests - failures - errors - skipped
        ok = failures == 0 and errors == 0 and tests > 0
        tests_badge = {"message": f"{passed} passed" + ("" if ok else f", {failures + errors} failed"),
                       "color": "4c1" if ok else "e05d44"}
    except (OSError, ET.ParseError, AttributeError):
        passed = failures = errors = 0
        tests_badge = {"message": "did not run", "color": "e05d44"}
    try:
        pct = json.loads(Path(coverage).read_text())["totals"]["percent_covered"]
        cov_badge = {"message": f"{pct:.0f}%", "color": "4c1" if pct >= 90 else "dfb317" if pct >= 75 else "e05d44"}
    except (OSError, ValueError, KeyError):
        pct = None
        cov_badge = {"message": "unknown", "color": "9f9f9f"}
    Path(out, "tests.json").write_text(json.dumps({"schemaVersion": 1, "label": "tests", **tests_badge}))
    Path(out, "coverage.json").write_text(json.dumps({"schemaVersion": 1, "label": "branch coverage", **cov_badge}))
    print(f"tests: {tests_badge['message']}; coverage: {cov_badge['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
