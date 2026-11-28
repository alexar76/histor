"""The "fingerprints" peer method (histor/fingerprint_xcheck.py, histor/peers.py): heldfast's per-tool fingerprints,
one state file per server in rufat325/heldfast-feed, against the ones HISTOR computes in heldfast's format.

rufat325 agreed on 2026-11-26 (modelcontextprotocol/registry#82) to a daily comparison of the remotes both read.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from histor import digest_xcheck as dx
from histor import fingerprint_xcheck as fx
from histor import peers

T = datetime(2026, 11, 27, 9, 10, tzinfo=UTC)
NAME, URL = "com.example/weather", "https://mcp.example.com/mcp"
TID = dx.target_id(NAME, URL)


def answer(fps: dict, *, since="2026-11-20T00:00:00Z", last="2026-11-27T11:00:00Z", extras=True, incomplete=()):
    return {"fingerprints": fps, "unchangedSince": since, "lastObserved": last, "extrasRecorded": extras,
            "incomplete": list(incomplete)}


def never() -> None:
    return None


def test_a_state_file_reads_as_a_row():
    row = fx.read_state(json.dumps({"package": "remote/" + NAME, "url": URL, "checked_at": "2026-11-27T09:10:00+00:00",
                                    "tools": {"forecast": "a" * 64, "odd": 3}, "version": "2026-11-27T091000"}))
    assert row == {"name": NAME, "endpoint": URL, "read_at": "2026-11-27T09:10:00+00:00", "tools": {"forecast": "a" * 64}}
    assert fx.read_state(json.dumps({"package": "0nmcp", "tools": {}}))["name"] is None   # an npm package, not a remote
    assert fx.read_state("not json")["tools"] is None
    # A hosted server's file: `published` is the read that produced the list; `attempted` a later read that failed.
    hosted = fx.read_state(json.dumps({"package": "remote/" + NAME, "url": URL, "published": "2026-11-09T16:55:04+00:00",
                                       "version": "2026-11-09T165504702248", "tools": {"a": "1"},
                                       "attempted": {"at": "2026-11-16T12:22:18+00:00", "version": "2026-11-16",
                                                     "why": "HTTP 410"}}))
    assert hosted["read_at"] == "2026-11-09T16:55:04+00:00" and hosted["attempted"] == "HTTP 410 at 2026-11-16T12:22:18+00:00"
    never_read = fx.read_state(json.dumps({"package": "remote/x/auth", "tools": {},
                                           "attempted": {"at": "2026-11-23T18:33:37+00:00", "why": "HTTP 401"}}))
    r = fx.xcheck([{"file": "remote__x__auth.json", **never_read}], lambda u: pytest.fail(u), "http://h")["rows"][0]
    assert r["verdict"] == "theirs_no_tools" and r["note"] == "HTTP 401 at 2026-11-23T18:33:37+00:00"


def test_same_fingerprints_agree_and_skipped_tools_are_left_out_on_both_sides():
    v = fx.judge({"a": "1", "b": "2", "big": "x"}, answer({"a": "1", "b": "2", "big": "y"}, incomplete=["big"]), T, never)
    assert v["verdict"] == "agree" and v["tools_compared"] == 2 and v["tools_skipped"] == ["big"]


def test_a_difference_inside_histors_unchanged_period_is_a_disagreement():
    v = fx.judge({"a": "1", "b": "2", "c": "3"}, answer({"a": "1", "b": "9", "d": "4"}), T, never)
    assert v["verdict"] == "disagree"
    assert (v["only_theirs"], v["only_histor"], v["different"]) == (["c"], ["d"], ["b"])
    assert v["counts"] == {"only_theirs": 1, "only_histor": 1, "different": 1}


def test_a_difference_after_a_change_histor_saw_later_is_not_judged():
    assert fx.judge({"a": "1"}, answer({"a": "2"}, since="2026-11-27T10:00:00Z"), T, never)["verdict"] == "changed_after"
    # The tool set is old, but a title or an annotation changed after their read.
    v = fx.judge({"a": "1"}, answer({"a": "2"}), T, lambda: datetime(2026, 11, 27, 10, tzinfo=UTC))
    assert v["verdict"] == "changed_after" and v["histor_extras_changed_at"] == "2026-11-27T10:00:00Z"
    # An earlier title change does not excuse a difference.
    assert fx.judge({"a": "1"}, answer({"a": "2"}), T, lambda: datetime(2026, 11, 21, tzinfo=UTC))["verdict"] == "disagree"


def test_their_read_after_histors_last_read_is_not_judged():
    assert fx.judge({"a": "1"}, answer({"a": "2"}, last="2026-11-27T06:00:00Z"), T, never)["verdict"] == "read_after"


def test_without_kept_titles_and_annotations_histor_cannot_fingerprint():
    assert fx.judge({"a": "1"}, answer({"a": "1"}, extras=False), T, never)["verdict"] == "histor_no_extras"


def test_xcheck_asks_for_the_server_record_only_when_fingerprints_differ():
    rows = [{"file": "remote__ok.json", "name": NAME, "endpoint": URL, "read_at": "2026-11-27T09:10:00Z", "tools": {"a": "1"}},
            {"file": "remote__diff.json", "name": "x/y", "endpoint": "https://y.example/mcp", "read_at": "2026-11-27T09:10:00Z",
             "tools": {"a": "1"}},
            {"file": "remote__gone.json", "name": "x/gone", "endpoint": "https://gone.example/mcp",
             "read_at": "2026-11-27T09:10:00Z", "tools": {"a": "1"}},
            {"file": "remote__auth.json", "name": "x/auth", "endpoint": "https://auth.example/mcp",
             "read_at": "2026-11-27T09:10:00Z", "tools": {"a": "1"}},
            {"file": "remote__empty.json", "name": "x/e", "endpoint": "https://e.example/mcp", "read_at": "2026-11-27T09:10:00Z",
             "tools": {}},
            {"file": "remote__bad.json", "name": None, "endpoint": None, "read_at": None, "tools": None}]
    diff_id = dx.target_id("x/y", "https://y.example/mcp")
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith(f"/servers/{TID}/fingerprints?format=heldfast"):
            return answer({"a": "1"})
        if url.endswith(f"/servers/{diff_id}/fingerprints?format=heldfast"):
            return answer({"a": "2"})
        if url.endswith(f"/servers/{diff_id}"):
            return {"server": {"lastStatus": "ok"},
                    "extrasChanges": [{"observedAt": "2026-11-27T10:30:00Z"}, {"observedAt": "2026-11-01T00:00:00Z"}]}
        if "/servers/" + dx.target_id("x/auth", "https://auth.example/mcp") in url:
            raise fx.NoToolSet(url)
        raise dx.NotListed(url)

    r = fx.xcheck(rows, fetch, "http://127.0.0.1:9490/")
    assert [x["verdict"] for x in r["rows"]] == ["agree", "changed_after", "not_listed", "histor_no_toolset",
                                                 "theirs_no_tools", "bad_row"]
    assert r["counts"]["agree"] == 1 and r["counts"]["changed_after"] == 1 and r["rows_in"] == 6
    assert sum(1 for u in calls if not u.endswith("heldfast")) == 1          # one record lookup, for the difference
    assert all(u.startswith("http://127.0.0.1:9490/api/v1/servers/") for u in calls)
    assert "| changed_after | x/y |" in fx.markdown(r) and r["rows"][1]["histor_last_status"] == "ok"
    assert "histor_last_status" not in r["rows"][0]                        # agreeing rows cost no record lookup


def _peer(**over):
    return {"slug": "heldfast", "name": "heldfast", "method": "fingerprints", "format": "heldfast",
            "github": {"repo": "rufat325/heldfast-feed", "ref": "main", "folder": "state", "prefix": "remote__"}, **over}


def test_the_config_checks_a_fingerprints_peer():
    assert peers.load_config(json.dumps([_peer(max_rows=1000)]))[0]["format"] == "heldfast"
    for bad, msg in ((_peer(format="sha1"), "format"), (_peer(github={"repo": "no-slash"}), "github.repo"),
                     (_peer(github={"repo": "a/b", "ref": "../x", "folder": "state"}), "github.ref"),
                     (_peer(github={"repo": "a/b", "ref": "main", "folder": "state", "prefix": "re/"}), "prefix"),
                     (_peer(max_rows=0), "max_rows")):
        with pytest.raises(peers.ConfigError, match=msg):
            peers.load_config(json.dumps([bad]))


def test_a_run_reads_one_commit_compares_todays_share_and_keeps_what_it_read(tmp_path: Path):
    files = [f"remote__s{i}.json" for i in range(30)] + ["0nmcp.json", "README.md"]
    commit = "c" * 40
    api = "https://api.github.com/repos/rufat325/heldfast-feed"

    def get_json(url):
        if url == f"{api}/commits/main":
            return {"sha": commit, "commit": {"tree": {"sha": "root"}, "committer": {"date": "2026-11-27T09:19:02Z"}}}
        if url == f"{api}/git/trees/root":
            return {"tree": [{"path": "state", "type": "tree", "sha": "st"}, {"path": "feed.json", "type": "blob"}]}
        if url == f"{api}/git/trees/st":
            return {"tree": [{"path": f, "type": "blob"} for f in files], "truncated": False}
        if "/fingerprints?format=heldfast" in url:
            return answer({"a": "1"})
        raise AssertionError(url)

    fetched = []

    def get_text(url):
        assert url.startswith(f"https://raw.githubusercontent.com/rufat325/heldfast-feed/{commit}/state/remote__")
        fetched.append(url)
        i = url.rsplit("remote__s", 1)[1].split(".")[0]
        return json.dumps({"package": f"remote/x/s{i}", "url": f"https://s{i}.example/mcp",
                           "checked_at": "2026-11-27T09:10:00+00:00", "tools": {"a": "1"}})

    res = peers.run_fingerprints(_peer(max_rows=10), tmp_path / "heldfast", histor="http://127.0.0.1:9490",
                                 get_text=get_text, get_json=get_json, pause=0, day="2026-11-27")
    out = res["state"]
    parts = -(-30 * 11 // 100)
    assert out["rows_in_source"] == 30 and out["part"].endswith(f"/{parts}") and out["commit"] == commit
    assert out["rows"] == len(fetched) == out["counts"]["agree"]
    chosen = [f for f in files[:30]
              if int.from_bytes(hashlib.sha256(f.encode()).digest()[:8], "big") % parts
              == datetime(2026, 11, 27).toordinal() % parts]
    assert [u.rsplit("/", 1)[1] for u in fetched] == sorted(chosen)          # in file-name order
    d = tmp_path / "heldfast"
    report = json.loads((d / "state.report.json").read_text())
    assert report["source"]["files_with_prefix"] == 30 and report["source"]["files_in_folder"] == 32
    assert report["sample"]["rule"].startswith("parts = ceil(1.1 * rows / max_rows); a row is compared when the "
                                               "first 8 bytes of sha256(file name)")
    kept = [json.loads(line) for line in (d / "state.input.jsonl").read_text().splitlines()]
    assert [k["file"] for k in kept] == sorted(chosen) and json.loads(kept[0]["body"])["tools"] == {"a": "1"}
    sums = (d / "SHA256SUMS").read_text()
    assert "state.input.jsonl" in sums and "state.report.md" in sums
    index = peers.build_index([_peer(max_rows=10)], {"heldfast": res}, "2026-11-27", None, "now")
    e = index["peers"][0]
    assert e["sources"] == [{"name": "state", "url": "https://github.com/rufat325/heldfast-feed/tree/main/state"}]
    assert e["format"] == "heldfast" and e["max_rows"] == 10 and e["last_results"] == res


def test_a_listing_that_fails_is_reported_not_raised(tmp_path: Path):
    def get_json(url):
        raise OSError("rate limited")

    res = peers.run_fingerprints(_peer(), tmp_path / "h", histor="http://x", get_json=get_json,
                                 get_text=lambda u: "", pause=0, day="2026-11-27")
    assert res == {"state": {"error": "OSError: rate limited"}}


def test_the_digest_share_rule_is_unchanged():
    rows = [{"name": f"n{i}", "endpoint": f"https://e{i}"} for i in range(50)]
    chosen, sample = peers.daily_share(rows, 10, "2026-11-27")
    assert 'sha256(name + "\\n" + endpoint)' in sample["rule"]
    part = sample["part"]
    assert all(int.from_bytes(hashlib.sha256(f"{r['name']}\n{r['endpoint']}".encode()).digest()[:8], "big")
               % sample["parts"] == part for r in chosen)
