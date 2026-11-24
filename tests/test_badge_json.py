"""The Pages workflow publishes an SVG next to the old shields.io JSON."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "badge_json", Path(__file__).parents[1] / "scripts" / "badge_json.py")
badge_json = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(badge_json)


def test_a_run_writes_svg_beside_the_json(tmp_path):
    (tmp_path / "junit.xml").write_text(
        '<testsuite tests="4" failures="1" errors="0" skipped="1"></testsuite>')
    (tmp_path / "coverage.json").write_text('{"totals": {"percent_covered": 87.4}}')
    out = tmp_path / "badges"
    assert badge_json.main(str(tmp_path / "junit.xml"), str(tmp_path / "coverage.json"), str(out)) == 0
    assert '"message": "2 passed, 1 failed"' in (out / "tests.json").read_text()
    assert ">2 passed, 1 failed<" in (out / "tests.svg").read_text()
    assert ">87%<" in (out / "coverage.svg").read_text()


def test_a_missing_report_still_writes_a_red_badge(tmp_path):
    out = tmp_path / "badges"
    assert badge_json.main(str(tmp_path / "nope.xml"), str(tmp_path / "nope.json"), str(out)) == 0
    text = (out / "tests.svg").read_text()
    assert "did not run" in text and "#e05d44" in text
