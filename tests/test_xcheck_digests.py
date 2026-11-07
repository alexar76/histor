"""scripts/xcheck_digests.py: another observer's tool-set digests against HISTOR's timeline, with room for a server
that changed between the two reads."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "xcheck_digests.py"
spec = importlib.util.spec_from_file_location("xcheck_digests", SCRIPT)
xc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xc)

A = "sha256-" + "A" * 43 + "="
B = "sha256-" + "B" * 43 + "="
C = "sha256-" + "C" * 43 + "="
NAME, URL = "io.github.example/tools", "https://tools.example/mcp"
HISTOR = "https://histor.test"

# HISTOR read A from the 1st to the 3rd at noon, B from the 4th at noon on; one failed read without a digest.
TIMELINE = [
    {"from": "2026-11-01T12:00:00Z", "until": "2026-11-03T12:00:00Z", "toolSetDigest": A, "status": "ok"},
    {"from": "2026-11-03T18:00:00Z", "until": "2026-11-03T18:00:00Z", "toolSetDigest": None, "status": "error"},
    {"from": "2026-11-04T12:00:00Z", "until": "2026-11-06T12:00:00Z", "toolSetDigest": B, "status": "ok"},
]


def fetcher(records):
    calls = []

    def fetch(url):
        calls.append(url)
        tid = url.rsplit("/", 1)[-1]
        if tid not in records:
            raise xc.NotListed(url)
        return records[tid]

    fetch.calls = calls
    return fetch


def run(rows, records=None, window=24):
    tid = xc.target_id(NAME, URL)
    return xc.xcheck(rows, fetcher(records if records is not None else {tid: {"timeline": TIMELINE}}), HISTOR, window)


def row(digest, at, name=NAME, url=URL):
    return {"name": name, "endpoint": url, "digest": digest, "read_at": at}


def test_target_id_is_histors():
    # The id HISTOR serves for CrawlCheck's own server (checked against the live API on 2026-11-07).
    assert xc.target_id("io.github.emmanuelorta/crawlcheck", "https://crawlcheck.io/mcp") == "5a5b50f1ee6c3444"


def test_verdicts():
    report = run([
        row(A, "2026-11-02T00:00:00Z"),   # inside HISTOR's A run
        row(B, "2026-11-04T00:00:00Z"),   # between the A and B runs: either neighbour counts
        row(A, "2026-11-04T13:00:00Z"),   # B was in force, A seen 25 h earlier: outside a 24 h window
        row(A, "2026-11-04T11:00:00Z"),   # gap: A is the earlier neighbour
        row(B, "2026-11-03T00:00:00Z"),   # A in force, B first seen 36 h later: outside the window
        row(B, "2026-11-03T12:00:00Z"),   # HISTOR read A then; B seen 24 h later, within the window: changed between reads
        row(C, "2026-11-05T00:00:00Z"),   # HISTOR never saw C
        row(C, "2026-12-20T00:00:00Z"),   # far after HISTOR's last read: nothing to compare inside the window
    ])
    assert [r["verdict"] for r in report["rows"]] == [
        "agree", "agree", "disagree", "agree", "disagree", "agree_nearby", "disagree", "histor_no_digest"]
    assert report["counts"]["disagree"] == 3
    assert report["rows"][6]["histor_at_read"] == B
    assert all(s["digest"] for r in report["rows"] for s in r.get("histor_seen", []))


def test_wider_window_turns_changes_into_nearby():
    report = run([row(A, "2026-11-04T13:00:00Z"), row(B, "2026-11-03T00:00:00Z")], window=48)
    assert [r["verdict"] for r in report["rows"]] == ["agree_nearby", "agree_nearby"]


def test_not_listed_bad_rows_and_hex_digests():
    hex_a = xc.base64.b64decode(A[len("sha256-"):]).hex()
    report = run([
        row("sha256:" + hex_a, "2026-11-02T00:00:00Z"),
        row(A, "2026-11-02T00:00:00Z", name="io.github.other/x"),
        {"name": NAME, "endpoint": URL, "digest": "not-a-digest", "read_at": "2026-11-02T00:00:00Z"},
        {"server": NAME, "url": URL, "tools_jcs_sha256": A},
    ])
    assert [r["verdict"] for r in report["rows"]] == ["agree", "not_listed", "bad_row", "bad_row"]
    assert report["rows"][0]["digest"] == A
    assert "digest" in report["rows"][2]["note"] and "read_at" in report["rows"][3]["note"]


def test_input_formats():
    data = [row(A, "2026-11-02T00:00:00Z")]
    as_json = json.dumps(data)
    as_object = json.dumps({"servers": data})
    as_lines = "\n".join(json.dumps(r) for r in data) + "\n"
    as_csv = "name,endpoint,digest,read_at\n" + f"{NAME},{URL},{A},2026-11-02T00:00:00Z\n"
    for text in (as_json, as_object, as_lines, as_csv):
        assert xc.read_rows(text) == [{k: str(v) for k, v in data[0].items()}] or xc.read_rows(text) == data


def test_crawlcheck_change_feed_is_read_as_published():
    feed = {"kind": "crawlcheck-mcp-tool-changes", "v": 1, "events": [
        # Recorded before CrawlCheck added RFC 8785 digests: no row.
        {"server": NAME, "url": URL, "at": "2026-11-06T10:00:00Z", "tools_sha256_after": "ab" * 32},
        {"server": NAME, "url": URL, "at": "2026-11-05T00:00:00Z", "event_id": "e1",
         "tools_jcs_sha256_after": B, "tools_jcs_sha256_before": A, "previous_read_at": "2026-11-02T00:00:00Z"},
        {"server": NAME, "url": URL, "at": "2026-11-05T00:00:00Z", "tools_jcs_sha256_after": B},  # the same read again
    ]}
    rows = xc.read_rows(json.dumps(feed))
    assert [(r["digest"], r["read_at"]) for r in rows] == [(B, "2026-11-05T00:00:00Z"), (A, "2026-11-02T00:00:00Z")]
    tid = xc.target_id(NAME, URL)
    fetch = fetcher({tid: {"timeline": TIMELINE}})
    report = xc.xcheck(rows, fetch, HISTOR)
    assert [r["verdict"] for r in report["rows"]] == ["agree", "agree"]
    assert report["rows"][0]["event_id"] == "e1"
    assert len(fetch.calls) == 1, "one HISTOR request per server"


def test_markdown_lists_only_rows_that_need_a_look(tmp_path):
    report = run([row(A, "2026-11-02T00:00:00Z"), row(C, "2026-11-05T00:00:00Z")])
    md = xc.markdown(report)
    assert "| disagree | 1 |" in md and C in md and A not in md.split("| verdict | server |")[1]


def test_cli_exit_status(tmp_path, monkeypatch):
    theirs = tmp_path / "theirs.json"
    theirs.write_text(json.dumps([row(C, "2026-11-05T00:00:00Z")]))
    tid = xc.target_id(NAME, URL)
    monkeypatch.setattr(xc, "fetch_json", fetcher({tid: {"timeline": TIMELINE}}))
    out = tmp_path / "report.json"
    assert xc.main([str(theirs), "--histor", HISTOR, "--out", str(out), "--pause", "0"]) == 1
    assert json.loads(out.read_text())["counts"]["disagree"] == 1
    theirs.write_text(json.dumps([row(A, "2026-11-02T00:00:00Z")]))
    assert xc.main([str(theirs), "--histor", HISTOR, "--pause", "0"]) == 0
    assert xc.main([str(tmp_path / "missing.json")]) == 2
