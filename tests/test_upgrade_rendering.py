"""Reports stay readable and inert when inspected in a renderer or terminal."""

import json

from histor import upgrade_capture
from histor.mcpclient import Observation
from histor.upgrade_brief import build_report, markdown
from histor.upgrade_brief_cli import main
from tests.test_upgrade_brief import observation, tool, write_bundle
from tests.test_upgrade_capture import fake_reader


def test_report_exposes_terminal_controls_as_inert_text(tmp_path):
    attack = "changed\x1b]52;c;YXR0YWNr\x07\x9d\u202e"
    path = write_bundle(tmp_path, after=observation([tool(description=attack)], observedAt="2026-10-27T10:00:00Z"))
    text = markdown(build_report(path))
    assert not any(c in text for c in ("\x1b", "\x07", "\x9d", "\u202e"))
    assert "001b" in text and "202e" in text
    out = tmp_path / "report"
    assert main(["--watchlist", str(path), "--out-dir", str(out)]) == 0
    assert "\x9d" not in (out / "report.json").read_text()
    assert json.loads((out / "report.json").read_text())["dependencies"][0]["observedChanges"]


def test_two_explicit_remote_captures_feed_offline_brief(tmp_path, monkeypatch):
    fake_reader(monkeypatch, Observation(status="ok", tools=[tool()],
        protocol_version="2026-07-28", server_info={"name": "fixture", "version": "1"}))
    for name in ("before", "after"):
        assert upgrade_capture.main(["--url", "https://example.invalid/mcp", "--target-id", "remote",
                                     "--out", str(tmp_path / f"{name}.json")]) == 0
    # Fixed observation times make the acceptance case reproducible.
    for name, stamp in (("before", "2026-10-26T09:00:00Z"), ("after", "2026-10-27T09:00:00Z")):
        path = tmp_path / f"{name}.json"
        doc = json.loads(path.read_text())
        doc["observedAt"] = stamp
        path.write_text(json.dumps(doc))
    watchlist = {"format": "histor.upgrade-watchlist/v1", "dependencies": [{"id": "remote",
        "before": "before.json", "after": "after.json", "tools": ["search"],
        "workflows": [{"id": "read", "description": "Read records", "tools": ["search"]}]}]}
    manifest = tmp_path / "watchlist.json"
    manifest.write_text(json.dumps(watchlist))
    report = build_report(manifest)
    assert report["dependencies"][0]["comparability"] == "comparable"
    assert report["summary"]["unchanged"] == 1
