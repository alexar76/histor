"""histor/peers.py: one configurable list of observers HISTOR cross-checks with; no real network."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from histor import digest_xcheck as dx
from histor import peers as pr

DEPLOY_CONFIG = Path(__file__).resolve().parent.parent / "deploy" / "peers.json"
A = "sha256-" + "A" * 43 + "="
C = "sha256-" + "C" * 43 + "="
NAME, URL = "io.github.example/tools", "https://tools.example/mcp"
TIMELINE = [{"from": "2026-11-01T12:00:00Z", "until": "2026-11-03T12:00:00Z", "toolSetDigest": A, "status": "ok"}]


SW = {"slug": "scraperwatch", "name": "ScraperWatch", "method": "field-diff",
      "changes": "https://sw.example/changes.json", "panel": "https://sw.example/panel.txt",
      "agent": "sw-observatory", "run_start": "02:50:00", "run_minutes": 10,
      "first_run": ["2026-10-30T10:49:43Z", "2026-10-30T10:59:39Z"]}


def digest_peer(**over):
    return {"slug": "obs", "name": "Observer", "method": "digest",
            "sources": [{"name": "list", "url": "https://obs.example/list.jsonl"},
                        {"name": "feed", "url": "https://obs.example/feed"}], **over}


def test_the_shipped_config_is_valid_and_lists_both_peers():
    peers = pr.load_config(DEPLOY_CONFIG.read_text())
    assert {p["slug"]: p["method"] for p in peers} == {
        "crawlcheck": "digest",
        "scraperwatch": "field-diff",
        "mcp-transparency-log": "digest",
        "heldfast": "fingerprints",
    }
    # Censuses of the whole registry (~51,000 rows, ~44,000 files): compared a bounded share a day, one HISTOR
    # request per row.
    rows = {p["slug"]: p.get("max_rows") for p in peers}
    assert rows["mcp-transparency-log"] == 1000 and rows["heldfast"] == 1000


@pytest.mark.parametrize("config, message", [
    ("{}", "non-empty JSON list"),
    ("[]", "non-empty JSON list"),
    (json.dumps([{"slug": "Bad Slug", "name": "x", "method": "digest"}]), "slug must match"),
    (json.dumps([{"slug": "a", "name": "x", "method": "guess"}]), "method must be one of"),
    (json.dumps([{"slug": "a", "name": "x", "method": "digest"}]), "needs sources"),
    (json.dumps([{"slug": "a", "name": "x", "method": "digest", "sources": [{"name": "s", "url": "http://plain"}]}]),
     "needs an https url"),
    (json.dumps([SW] * 2), "slug used twice"),
    (json.dumps([{**SW, "aliases": ["peers"]}]), "bad alias"),
    (json.dumps([{**SW, "changes": "http://sw.example/c"}]), "needs an https changes url"),
    (json.dumps([{**SW, "agent": ""}]), "agent its robots.txt rules name"),
    (json.dumps([{**SW, "run_start": "2:50"}]), "run_start must be HH:MM:SS"),
    (json.dumps([{**SW, "days": 0}]), "days must be between 1 and 31"),
    (json.dumps([{**SW, "first_run": ["only one"]}]), "first_run must be two"),
    (json.dumps([{**SW, "first_run": ["yesterday", "today"]}]), "first_run must be two"),
    (json.dumps([{k: v for k, v in SW.items() if k != "first_run"}]), "first_run must be two"),
    (json.dumps([digest_peer(max_rows=0)]), "max_rows must be a whole number"),
    (json.dumps([digest_peer(max_rows="500")]), "max_rows must be a whole number"),
    (json.dumps([digest_peer(max_rows=True)]), "max_rows must be a whole number"),
])
def test_config_errors_are_named(config, message):
    with pytest.raises(pr.ConfigError, match=message):
        pr.load_config(config)


def test_a_digest_peer_runs_every_source_and_a_failing_one_does_not_stop_the_others(tmp_path):
    listed = "\n".join(json.dumps(r) for r in [
        {"name": NAME, "endpoint": URL, "digest": A, "read_at": "2026-11-02T00:00:00Z"},
        {"name": NAME, "endpoint": URL, "digest": C, "read_at": "2026-11-02T00:00:00Z"},
    ]) + "\n"

    def get_text(url):
        if url.endswith("/feed"):
            raise OSError("connection refused")
        return listed

    tid = dx.target_id(NAME, URL)

    def get_json(url):
        assert url == f"https://histor.test/api/v1/servers/{tid}"
        return {"timeline": TIMELINE}

    res = pr.run_peer(digest_peer(), tmp_path / "obs", histor="https://histor.test", get_text=get_text,
                      get_json=get_json, pause=0)
    assert res["list"]["counts"]["agree"] == 1 and res["list"]["counts"]["disagree"] == 1
    assert res["feed"]["error"].startswith("OSError")
    files = sorted(p.name for p in (tmp_path / "obs").iterdir())
    assert files == ["SHA256SUMS", "list.input.jsonl", "list.report.json", "list.report.md"]
    report = json.loads((tmp_path / "obs" / "list.report.json").read_text())
    assert report["peer"] == "obs" and report["source"]["name"] == "list"
    sums = (tmp_path / "obs" / "SHA256SUMS").read_text()
    assert "list.input.jsonl" in sums and "SHA256SUMS" not in sums


def test_the_index_lists_every_peer_and_the_newest_published_day(tmp_path):
    published = tmp_path / "published"
    (published / "scraperwatch" / "2026-11-01").mkdir(parents=True)
    (published / "scraperwatch" / "2026-11-02").mkdir()
    os.symlink("2026-11-02", published / "scraperwatch" / "latest")
    (published / "quiet" / "2026-10-30").mkdir(parents=True)
    (published / "quiet" / "2026-10-30" / "results.json").write_text(json.dumps(
        {"days": ["2026-10-29"], "forward": {"verdicts": {"seen": 3}}, "reverse": {"why": {"timing": 1}, "likelyMisses": [1, 2]}}))
    (published / "old" / "2026-10-31").mkdir(parents=True)
    (published / "old" / "2026-10-31" / "feed.report.json").write_text(json.dumps({"rows_in": 4, "counts": {"agree": 4}}))
    peers = [digest_peer(aliases=["xcheck-obs"]),
             {"slug": "scraperwatch", "name": "ScraperWatch", "method": "field-diff", "contact": "ops@x"},
             {"slug": "quiet", "name": "Quiet", "method": "field-diff"},
             {"slug": "new", "name": "New", "method": "field-diff"},
             digest_peer(slug="old")]
    ran = {"obs": {"list": {"rows": 2, "counts": {"agree": 2}}}}
    idx = pr.build_index(peers, ran, "2026-11-08", published, "2026-11-08T12:00:00Z")
    by = {e["slug"]: e for e in idx["peers"]}
    assert by["obs"]["last_day"] == "2026-11-08" and by["obs"]["last_results"] == ran["obs"]
    assert by["obs"]["aliases"] == ["/datasets/xcheck-obs/"] and by["obs"]["path"] == "/datasets/peers/obs/"
    assert by["scraperwatch"]["last_day"] == "2026-11-02" and by["scraperwatch"]["contact"] == "ops@x"
    assert by["scraperwatch"]["last_results"] is None  # that day has no results.json
    assert by["quiet"]["last_day"] == "2026-10-30"
    assert by["quiet"]["last_results"] == {"days": ["2026-10-29"], "forward": {"seen": 3}, "reverse": {"timing": 1},
                                           "likelyMisses": 2}
    assert by["old"]["last_results"] == {"feed": {"rows": 4, "counts": {"agree": 4}}}
    assert by["new"]["last_day"] is None and by["new"]["last_results"] is None


def test_main_runs_only_digest_peers_and_writes_the_index(tmp_path, monkeypatch):
    config = tmp_path / "peers.json"
    config.write_text(json.dumps([digest_peer(sources=[{"name": "list", "url": "https://obs.example/l.jsonl"}]), SW]))
    rows = json.dumps({"name": NAME, "endpoint": URL, "digest": A, "read_at": "2026-11-02T00:00:00Z"}) + "\n"
    monkeypatch.setattr(pr, "fetch_text", lambda url, timeout=120: rows)
    monkeypatch.setattr(dx, "fetch_json", lambda url, timeout=30: {"timeline": TIMELINE})
    # run_peer's defaults were bound at import; route through the patched functions.
    real = pr.run_peer
    monkeypatch.setattr(pr, "run_peer", lambda p, o, **k: real(p, o, get_text=pr.fetch_text, get_json=dx.fetch_json, **k))
    out = tmp_path / "out"
    args = ["--config", str(config), "--out", str(out), "--day", "2026-11-08", "--pause", "0"]
    assert pr.main([*args, "--method", "digest"]) == 0
    assert (out / "obs" / "list.report.md").is_file() and not (out / "scraperwatch").exists()
    idx = json.loads((out / "index.json").read_text())
    assert [e["slug"] for e in idx["peers"]] == ["obs", "scraperwatch"]
    assert idx["peers"][0]["last_results"]["list"]["counts"]["agree"] == 1
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert pr.main(["--config", str(bad), "--out", str(out)]) == 2


def test_field_diff_peers_run_with_their_settings_and_a_failure_is_reported(tmp_path, monkeypatch):
    config = tmp_path / "peers.json"
    config.write_text(json.dumps([digest_peer(), {**SW, "days": 2}]))
    seen = {}

    def fake(peer, out):
        seen.update(peer)
        out.mkdir()
        (out / "results.json").write_text("{}")
        return {"days": ["2026-11-07"], "forward": {"seen": 5}, "reverse": {}, "likelyMisses": 0}

    monkeypatch.setattr(pr, "run_field_diff", fake)
    out = tmp_path / "out"
    assert pr.main(["--config", str(config), "--out", str(out), "--method", "field-diff"]) == 0
    assert seen["days"] == 2 and seen["changes"] == SW["changes"] and seen["agent"] == SW["agent"]
    assert (out / "scraperwatch" / "results.json").is_file() and not (out / "obs").exists()
    idx = {e["slug"]: e for e in json.loads((out / "index.json").read_text())["peers"]}
    assert idx["scraperwatch"]["last_results"]["forward"] == {"seen": 5}

    def broken(peer, out):
        raise ConnectionError("database unreachable")

    monkeypatch.setattr(pr, "run_field_diff", broken)
    assert pr.main(["--config", str(config), "--out", str(tmp_path / "out2"), "--method", "field-diff"]) == 1


def test_field_diff_summary_keeps_what_the_index_shows():
    result = {"days": ["d"], "forward": {"verdicts": {"seen": 1}, "rows": []},
              "reverse": {"why": {"robots": 2}, "likelyMisses": [{}, {}, {}]}, "generatedAt": "x"}
    assert pr.field_diff_summary(result) == {"days": ["d"], "forward": {"seen": 1}, "reverse": {"robots": 2},
                                             "likelyMisses": 3}


def _census(n):
    return [{"name": f"io.example/s{i}", "endpoint": f"https://s{i}.example/mcp", "digest": A,
             "read_at": "2026-11-02T00:00:00Z"} for i in range(n)]


def test_a_large_source_is_compared_in_daily_parts_that_cover_every_row():
    """MCP Transparency Log publishes a census of the whole registry (51,525 rows on 2026-11-26). The digest
    method makes one HISTOR request per row, so a run over all of it took hours on 2026-11-27 and was stopped;
    max_rows bounds each day's share and the parts rotate through the whole file."""
    import datetime as dtm
    import hashlib

    rows = _census(2500)
    assert pr.daily_share(rows, None, "2026-11-27") == (rows, None)
    assert pr.daily_share(rows, 5000, "2026-11-27") == (rows, None)

    seen: list[str] = []
    for day in ("2026-11-27", "2026-11-28", "2026-11-29"):
        chosen, sample = pr.daily_share(rows, 1000, day)
        assert sample["parts"] == 3 and sample["rows_in_source"] == 2500 and sample["max_rows"] == 1000
        assert sample["part"] == dtm.date.fromisoformat(day).toordinal() % 3
        assert "capped" not in sample and 0 < len(chosen) <= 1000
        assert pr.daily_share(rows, 1000, day) == (chosen, sample)             # the same day, the same rows
        # The published rule recomputes the day's rows from outside.
        by_rule = {r["endpoint"] for r in rows
                   if int.from_bytes(hashlib.sha256(f"{r['name']}\n{r['endpoint']}".encode()).digest()[:8], "big")
                   % 3 == sample["part"]}
        assert {r["endpoint"] for r in chosen} == by_rule
        seen += [r["endpoint"] for r in chosen]
    assert sorted(seen) == sorted(r["endpoint"] for r in rows)                # three days: every row, once


def test_a_part_larger_than_max_rows_is_capped_and_says_so():
    same = _census(1) * 5                                                      # one name and endpoint: one part
    shares = [pr.daily_share(same, 2, f"2026-11-{d}") for d in (27, 28, 29)]
    assert all(s["parts"] == 3 for _, s in shares)
    full = [(chosen, s) for chosen, s in shares if chosen]
    assert len(full) == 1                                                      # its part comes round once in 3 days
    chosen, sample = full[0]
    assert len(chosen) == 2 and sample["capped"] is True


def test_a_bounded_digest_peer_asks_histor_only_for_its_share(tmp_path):
    rows = _census(2500)
    listed = "\n".join(json.dumps(r) for r in rows) + "\n"
    asked: list[str] = []

    def get_json(url):
        asked.append(url)
        return {"timeline": TIMELINE}

    peer = digest_peer(sources=[{"name": "list", "url": "https://obs.example/list.jsonl"}], max_rows=1000)
    res = pr.run_peer(peer, tmp_path / "obs", histor="https://histor.test", get_text=lambda url: listed,
                      get_json=get_json, pause=0, day="2026-11-27")
    report = json.loads((tmp_path / "obs" / "list.report.json").read_text())
    assert len(asked) == report["rows_in"] <= 1000
    assert report["sample"]["rows_in_source"] == 2500 and report["sample"]["parts"] == 3
    assert res["list"]["rows_in_source"] == 2500 and res["list"]["part"] == f"{report['sample']['part'] + 1}/3"
    assert "Today's share of a larger file: part" in (tmp_path / "obs" / "list.report.md").read_text()
    index = pr.build_index([peer], {"obs": res}, "2026-11-27", None, "2026-11-27T12:00:00Z")
    assert index["peers"][0]["max_rows"] == 1000

