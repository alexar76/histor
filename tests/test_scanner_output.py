"""How HISTOR reads the scanner's output, with a stand-in script (needs only node, not WARDEN)."""

from __future__ import annotations

import shutil

import pytest

from histor.scanner import Scanner, ScannerError

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_a_line_that_is_not_json_is_a_scanner_error(tmp_path):
    """2026-11-15: a JSONDecodeError escaped the crawler's ScannerError fallback, so one bad line failed the
    whole remote poll. As a ScannerError the crawler retries the batch job by job and only that job fails."""
    (tmp_path / "scan.mjs").write_text('process.stdout.write(\'{"id": "a", "patternMatches": [\\n\');\n')
    with pytest.raises(ScannerError, match="not JSON"):
        Scanner(tmp_path).scan([{"id": "a", "server": {"id": "x"}, "tools": []}])


def test_raw_line_separators_inside_a_json_string_are_one_line(tmp_path):
    (tmp_path / "scan.mjs").write_text(
        'process.stdout.write(JSON.stringify({id: "a", patternMatches: [{span: "x\\u2028y\\u2029z\\u0085"}],'
        ' recordMatches: []}) + "\\n");\n')
    out = Scanner(tmp_path).scan([{"id": "a", "server": {"id": "x"}, "tools": []}])
    assert out["a"]["patternMatches"][0]["span"] == "x\u2028y\u2029z\x85"
