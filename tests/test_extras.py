"""Tool members beyond the four MTL/1 digests (histor.extras): kept beside the tool set, under a digest
of their own, with their changes recorded — and the tool-set digest and the label chain untouched."""

from __future__ import annotations

import json
import re
from xml.etree import ElementTree

from histor.extras import MAX_DIFF_TOOLS, MAX_TOOL_BYTES, digest_of, extras_diff, tool_extras
from histor.store import dumps
from histor.subject import tool_set_digest
from tests.conftest import make_target, tool


def annotated(name: str, description: str = "", **extra):
    return {**tool(name, description), **extra}


def test_only_members_outside_the_digest_are_kept():
    tools = [annotated("read", "Read a file.", annotations={"readOnlyHint": True}, _meta={"price": 0.01},
                       title="Read file"),
             tool("plain", "Nothing extra.")]
    digest, body = tool_extras(tools)
    assert digest and digest.startswith("sha256-")
    assert body == {"tools": {"read": {"annotations": {"readOnlyHint": True}, "_meta": {"price": 0.01},
                                       "title": "Read file"}}}


def test_no_extras_is_none_and_the_digest_ignores_order():
    assert tool_extras([tool("a"), tool("b")]) == (None, None)
    one = [annotated("a", annotations={"x": 1}), annotated("b", icons=[{"src": "i"}])]
    assert tool_extras(one)[0] == tool_extras(list(reversed(one)))[0]


def test_hostile_values_are_cleaned_and_capped():
    big = "x" * (MAX_TOOL_BYTES + 10)
    _, body = tool_extras([annotated("nul", _meta={"k\x00": "v\ud800"}), annotated("huge", _meta={"blob": big})])
    assert body["tools"]["nul"]["_meta"] == {"k�": "v�"}
    assert "huge" not in body["tools"] and body["oversized"]["huge"] > MAX_TOOL_BYTES


def test_the_tool_set_digest_does_not_see_extras():
    bare = [tool("delete", "Delete a file.")]
    hinted = [annotated("delete", "Delete a file.", annotations={"readOnlyHint": True})]
    assert tool_set_digest(bare)[0] == tool_set_digest(hinted)[0]


def test_diff_spells_out_annotation_flips_and_flags_escalations():
    old = tool_extras([annotated("rm", annotations={"readOnlyHint": True}, _meta={"v": 1})])[1]
    new = tool_extras([annotated("rm", annotations={"readOnlyHint": False, "destructiveHint": True}, _meta={"v": 2}),
                       annotated("new", title="New")])[1]
    d = extras_diff(old, new)
    assert d["tools"]["rm"]["annotations"] == {"destructiveHint": [None, True], "readOnlyHint": [True, False]}
    assert d["tools"]["rm"]["members"] == {"_meta": "changed"}
    assert d["tools"]["new"]["members"] == {"title": "added"}
    assert d["escalations"] == ["rm"] and d["changedTools"] == 2


def _extras_rows(s, target_id):
    with s.store.db.read() as tx:
        target = tx.one("SELECT current_extras, current_toolset, changes FROM targets WHERE id=?", (target_id,))
        changes = tx.execute("SELECT * FROM extras_changes WHERE target_id=? ORDER BY seq", (target_id,))
        observed = [r["extras"] for r in tx.execute(
            "SELECT extras FROM observations WHERE target_id=? ORDER BY observed_at", (target_id,))]
    return target, changes, observed


def test_the_crawler_keeps_extras_and_records_a_flip_without_a_tool_set_change(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/x"] = [annotated("delete_file", "Delete a file.", annotations={"readOnlyHint": True})]
    target = make_target("io.example/files", "/x")
    world["targets"].append(target)
    s.crawler.run()
    first, changes, observed = _extras_rows(s, target.id)
    assert first["current_extras"] and changes == [] and observed == [first["current_extras"]]
    assert json.loads(s.store.blob_text(first["current_extras"], "toolextras"))["tools"]["delete_file"] == {
        "annotations": {"readOnlyHint": True}}

    clock.advance(days=1)
    mcp.tools["/x"] = [annotated("delete_file", "Delete a file.", annotations={"readOnlyHint": False})]
    s.crawler.run()
    second, changes, _ = _extras_rows(s, target.id)
    # Same tool set: no MTL/1 change, no new digest — the flip is recorded beside it.
    assert second["current_toolset"] == first["current_toolset"] and second["changes"] == 0
    assert len(changes) == 1 and changes[0]["from_extras"] == first["current_extras"]
    summary = json.loads(changes[0]["summary"])
    assert summary["escalations"] == ["delete_file"]
    assert summary["tools"]["delete_file"]["annotations"] == {"readOnlyHint": [True, False]}


def test_a_target_first_seen_by_this_code_is_a_baseline_and_losing_extras_is_a_change(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/y"] = [tool("plain", "No extras.")]
    target = make_target("io.example/plain", "/y")
    world["targets"].append(target)
    s.crawler.run()
    row, changes, observed = _extras_rows(s, target.id)
    assert row["current_extras"] == "" and changes == [] and observed == [""]

    clock.advance(days=1)
    mcp.tools["/y"] = [annotated("plain", "No extras.", _meta={"x402": {"price": "0.01"}})]
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/y"] = [tool("plain", "No extras.")]
    s.crawler.run()
    row, changes, _ = _extras_rows(s, target.id)
    assert [(c["from_extras"] is None, c["to_extras"] is None) for c in changes] == [(True, False), (False, True)]
    assert row["current_extras"] == ""


def test_the_api_and_the_dataset_carry_the_extras(world, tmp_path):
    import gzip

    from fastapi.testclient import TestClient

    from histor import dataset
    from histor.app import create_app

    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/z"] = [annotated("pay", "Pay a bill.", annotations={"destructiveHint": False}, _meta={"price": 0.25})]
    target = make_target("io.example/pay", "/z")
    world["targets"].append(target)
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/z"] = [annotated("pay", "Pay a bill.", annotations={"destructiveHint": True}, _meta={"price": 0.25})]
    s.crawler.run()

    with TestClient(create_app(s)) as client:
        page = client.get(f"/api/v1/servers/{target.id}").json()
        digest = page["server"]["toolExtrasDigest"]
        assert page["server"]["toolExtrasDigestScheme"] == "legacy"
        assert page["toolExtras"]["tools"]["pay"]["annotations"] == {"destructiveHint": True}
        assert page["extrasChanges"][0]["summary"]["escalations"] == ["pay"]
        blob = client.get(f"/api/v1/tool-extras/{digest}")
        assert blob.status_code == 200 and blob.json()["tools"]["pay"]["_meta"] == {"price": 0.25}

    manifest = dataset.export(s.store, s.key, tmp_path / "snap", now="2026-10-23T15:00:00Z")
    assert manifest["files"]["tool_extras.jsonl.gz"]["rows"] == 2
    assert manifest["files"]["extras_changes.jsonl.gz"]["rows"] == 1
    rows = [json.loads(line) for line in gzip.open(tmp_path / "snap" / "servers.jsonl.gz", "rt")]
    assert [r["toolExtrasDigest"] for r in rows if r["id"] == target.id] == [digest]
    card = (tmp_path / "snap" / "README.md").read_text()
    assert "config_name: tool_extras" in card and "**tool_extras**" in card


def test_capped_extras_can_hide_annotation_changes_from_both_digests(world):
    from fastapi.testclient import TestClient

    from histor.app import create_app
    from histor.check import CHECK_TYPE
    from histor.logbook import verify_document

    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    target = make_target("io.example/pay", "/pay")
    world["targets"].append(target)
    metadata = {"padding": "x" * MAX_TOOL_BYTES}
    before = [annotated("pay", "Pay a bill.", _meta=metadata,
                        annotations={"readOnlyHint": False, "destructiveHint": True})]
    after = [annotated("pay", "Pay a bill.", _meta=metadata,
                       annotations={"readOnlyHint": True, "destructiveHint": False})]
    mcp.tools["/pay"] = before
    s.crawler.run()

    with TestClient(create_app(s)) as client:
        path = f"/api/v1/servers/{target.id}"
        first = client.get(path).json()
        assert first["server"]["toolExtrasDigestScheme"] == "legacy"
        assert first["toolExtras"]["tools"] == {}
        assert first["toolExtras"]["oversized"]["pay"] > MAX_TOOL_BYTES

        clock.advance(days=1)
        mcp.tools["/pay"] = after
        s.crawler.run()
        second = client.get(path).json()
        assert second["server"]["toolSetDigest"] == first["server"]["toolSetDigest"]
        assert second["server"]["toolExtrasDigest"] == first["server"]["toolExtrasDigest"]
        assert second["toolExtras"] == first["toolExtras"]
        assert second["extrasChanges"] == []

        check = client.post("/api/v1/check", json={"endpoint": target.endpoint, "tools": before}).json()
        assert check["match"] == "same"
        assert verify_document(check, s.key.did, CHECK_TYPE)


def test_single_server_approval_filter_and_atom_include_annotation_only_observations(world):
    from fastapi.testclient import TestClient

    from histor.app import create_app

    services, mcp, clock = world["services"], world["mcp"], world["clock"]
    pilot = make_target("io.example/board", "/board")
    other = make_target("io.example/other", "/other")
    world["targets"] += [pilot, other]
    mcp.tools["/board"] = [
        annotated("board & layers", annotations={"readOnlyHint": False, "destructiveHint": True}),
        annotated("another", annotations={"readOnlyHint": True}),
        annotated("numeric", annotations={"readOnlyHint": 0, "destructiveHint": 1}),
        tool("added-hint", ""),
    ]
    mcp.tools["/other"] = [annotated("other_tool", annotations={"readOnlyHint": False})]
    services.crawler.run()
    clock.advance(days=1)
    mcp.tools["/board"] = [
        annotated("board & layers", annotations={"readOnlyHint": True, "destructiveHint": False}),
        annotated("another", annotations={"readOnlyHint": False}),
        annotated("numeric", annotations={"readOnlyHint": True, "destructiveHint": False}),
        annotated("added-hint", annotations={"readOnlyHint": True}),
    ]
    mcp.tools["/other"] = [annotated("other_tool", annotations={"readOnlyHint": True})]
    services.crawler.run()

    query = "approval_relaxing=true"
    with TestClient(create_app(services)) as client:
        server_url = f"/api/v1/servers/{pilot.id}"
        normal = client.get(server_url).json()
        assert normal["changes"] == []
        original, = normal["extrasChanges"]
        filtered = client.get(f"{server_url}?{query}").json()
        assert set(filtered) == {"server", "extrasChanges"}
        picked, = filtered["extrasChanges"]
        assert picked["id"] == original["id"]
        assert picked["summary"]["tools"] == {
            "board & layers": {"annotations": {
                "readOnlyHint": [False, True],
                "destructiveHint": [True, False],
            }},
        }
        assert picked["summary"]["approvalRelaxing"] == ["board & layers"]

        page = client.get(f"/s/{pilot.id}?{query}").text
        assert f'id="approval-change-{picked["id"]}"' in page
        assert f'href="/feed.xml?watch={pilot.id}&amp;{query}"' in page
        assert re.search(r'<link rel="alternate" type="application/atom\+xml" href="[^"]*'
                         rf'/feed.xml\?watch={pilot.id}&amp;{query}"/>', page)
        assert "destructiveHint true → false" in page and "board &amp; layers" in page
        assert query in client.get(f"/s/{pilot.id}").text

        ns = {"a": "http://www.w3.org/2005/Atom"}
        watch_url = f"/feed.xml?watch={pilot.id}"
        plain_feed = ElementTree.fromstring(client.get(watch_url).text)
        assert not plain_feed.findall("a:entry", ns)
        response = client.get(f"{watch_url}&{query}")
        assert response.headers["content-type"] == "application/atom+xml"
        feed = ElementTree.fromstring(response.text)
        assert feed.find("a:link[@rel='self']", ns).attrib["href"].endswith(f"{watch_url}&{query}")
        entry, = feed.findall("a:entry", ns)
        assert entry.findtext("a:id", namespaces=ns).endswith(f"?{query}#approval-change-{picked['id']}")
        summary = entry.findtext("a:summary", namespaces=ns)
        assert "board & layers" in summary and "readOnlyHint false → true" in summary
        assert "another" not in summary and "other_tool" not in summary
        assert client.get(f"{watch_url},{other.id}&{query}").status_code == 400
        assert client.get(f"/feed.xml?watch={'0' * 16}&{query}").status_code == 404


def test_global_approval_filter_and_atom_cover_all_servers_with_stable_event_ids(world):
    from fastapi.testclient import TestClient

    from histor.app import create_app

    services, mcp, clock = world["services"], world["mcp"], world["clock"]
    board = make_target("io.example/board", "/board")
    files = make_target("io.example/files", "/files")
    noise = make_target("io.example/noise", "/noise")
    world["targets"] += [board, files, noise]
    mcp.tools["/board"] = [annotated("board & layers", annotations={"readOnlyHint": False})]
    mcp.tools["/files"] = [annotated("move_file", annotations={"destructiveHint": True})]
    mcp.tools["/noise"] = [
        annotated("numeric_hint_tool", annotations={"readOnlyHint": 0, "destructiveHint": 1}),
        annotated("strict", annotations={"readOnlyHint": True, "destructiveHint": False}),
        tool("missing", ""),
    ]
    services.crawler.run()

    clock.advance(days=1)
    mcp.tools["/board"] = [annotated("board & layers", annotations={"readOnlyHint": True})]
    services.crawler.run()

    clock.advance(days=1)
    mcp.tools["/files"] = [annotated("move_file", annotations={"destructiveHint": False})]
    mcp.tools["/noise"] = [
        annotated("numeric_hint_tool", annotations={"readOnlyHint": True, "destructiveHint": False}),
        annotated("strict", annotations={"readOnlyHint": False, "destructiveHint": True}),
        annotated("missing", annotations={"readOnlyHint": True}),
    ]
    services.crawler.run()

    query = "approval_relaxing=true"
    api = f"/api/v1/changes?{query}"
    feed_path = f"/feed.xml?{query}"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    public = services.settings.public_base
    with TestClient(create_app(services)) as client:
        feed_response = client.get(feed_path)
        assert feed_response.status_code == 200
        assert feed_response.headers["content-type"] == "application/atom+xml"
        feed = ElementTree.fromstring(feed_response.content)
        assert feed.findtext("a:id", namespaces=ns) == public + feed_path
        assert feed.find("a:link[@rel='self']", ns).attrib["href"] == public + feed_path
        newest, older = feed.findall("a:entry", ns)
        assert feed.findtext("a:updated", namespaces=ns) == newest.findtext("a:updated", namespaces=ns)
        assert "move_file: destructiveHint true → false" in newest.findtext("a:summary", namespaces=ns)
        assert "board & layers: readOnlyHint false → true" in older.findtext("a:summary", namespaces=ns)

        first = client.get(f"{api}&limit=1").json()
        latest, = first["changes"]
        assert latest["targetId"] == files.id and latest["targetName"] == files.name
        assert first["nextCursor"] == latest["id"]
        second = client.get(f"{api}&limit=1&before={first['nextCursor']}").json()
        previous, = second["changes"]
        assert previous["targetId"] == board.id and previous["targetName"] == board.name
        assert previous["id"] < latest["id"]
        assert client.get(f"{api}&after=0").json() == {
            "changes": [previous, latest], "nextCursor": latest["id"],
        }
        assert client.get(f"{api}&after={latest['id']}").json() == {
            "changes": [], "nextCursor": latest["id"],
        }
        assert client.get(f"{api}&watch={noise.id}").json()["changes"] == []
        assert client.get(f"{api}&before=2&after=0").status_code == 400
        assert client.get("/api/v1/changes").json()["changes"] == []

        page = client.get(f"/changes?{query}&lang=ru")
        assert page.status_code == 200
        assert f'href="{feed_path}"' in page.text
        assert re.search(r'<link rel="alternate" type="application/atom\+xml" href="[^"]*'
                         rf'/feed.xml\?{query}"\s*/?>', page.text)
        assert "board &amp; layers" in page.text and "move_file" in page.text
        assert "numeric_hint_tool" not in page.text and "io.example/noise" not in page.text

        for event, target, entry in ((latest, files, newest), (previous, board, older)):
            event_id = event["id"]
            event_url = f"/s/{target.id}?{query}"
            anchor = f"#approval-change-{event_id}"
            stable_id = f"{public}{event_url}{anchor}"
            assert entry.findtext("a:id", namespaces=ns) == stable_id
            assert entry.find("a:link", ns).attrib["href"] == f"{public}{event_url}&event={event_id}{anchor}"
            scoped = ElementTree.fromstring(client.get(f"/feed.xml?watch={target.id}&{query}").content)
            scoped_entry, = scoped.findall("a:entry", ns)
            assert scoped_entry.findtext("a:id", namespaces=ns) == stable_id
            event_page = client.get(f"{event_url}&event={event_id}")
            assert event_page.status_code == 200
            assert f'id="approval-change-{event_id}"' in event_page.text
            exact = client.get(f"/api/v1/servers/{target.id}?{query}&event={event_id}")
            assert exact.status_code == 200
            exact_change, = exact.json()["extrasChanges"]
            assert exact_change["id"] == event_id
        assert client.get(f"/s/{board.id}?{query}&event={latest['id']}").status_code == 404


def test_approval_filter_recovers_older_matches_beyond_both_display_caps(world):
    from fastapi.testclient import TestClient

    from histor.app import create_app

    services = world["services"]
    store = services.store
    target = make_target("io.example/board", "/board")
    world["targets"].append(target)
    world["mcp"].tools["/board"] = [tool("plain")]
    services.crawler.run()
    many = [f"earlier_{n:03d}" for n in range(MAX_DIFF_TOOLS)]
    before = {"tools": {name: {"annotations": {"readOnlyHint": True}} for name in many}}
    after = {"tools": {name: {"annotations": {"readOnlyHint": False}} for name in many}}
    before["tools"]["last"] = {"annotations": {"destructiveHint": True}}
    after["tools"]["last"] = {"annotations": {"destructiveHint": False}}
    old_digest, new_digest = digest_of(before), digest_of(after)
    summary = extras_diff(before, after)
    assert summary["truncated"] and "last" not in summary["tools"]

    insert = "INSERT INTO extras_changes(target_id, observed_at, from_extras, to_extras, summary) VALUES(?, ?, ?, ?, ?)"
    truncated = dumps(summary)
    with store.db.transaction() as tx:
        store.put_blob(tx, old_digest, "toolextras", before)
        store.put_blob(tx, new_digest, "toolextras", after)
        tx.execute(insert, (target.id, "2026-11-14T01:14:40Z", old_digest, new_digest, truncated))
        noise = dumps({"tools": {"last": {"annotations": {"destructiveHint": [False, True]}}}, "changedTools": 1})
        for _ in range(105):
            tx.execute(insert, (target.id, "2026-11-15T01:14:40Z", new_digest, old_digest, noise))

    result, = store.extras_changes(target.id, limit=1, approval_relaxing=True)
    assert result["observedAt"] == "2026-11-14T01:14:40Z"
    assert result["summary"]["tools"] == {"last": {"annotations": {"destructiveHint": [True, False]}}}
    global_result, = store.approval_relaxing_changes(limit=1)
    assert global_result == {**result, "targetId": target.id, "targetName": target.name, "targetTitle": target.title}

    with store.db.transaction() as tx:
        for _ in range(50):
            tx.execute(insert, (target.id, "2026-11-16T01:14:40Z", old_digest, new_digest, truncated))

    query = "approval_relaxing=true"
    event_id = result["id"]
    anchor = f"approval-change-{event_id}"
    ns = {"a": "http://www.w3.org/2005/Atom"}
    with TestClient(create_app(services)) as client:
        feed = ElementTree.fromstring(client.get(f"/feed.xml?{query}").content)
        feed_ids = [entry.findtext("a:id", namespaces=ns) for entry in feed.findall("a:entry", ns)]
        assert len(feed_ids) == 50
        assert all(not item.endswith(f"#{anchor}") for item in feed_ids)
        scoped_path = f"/s/{target.id}?{query}"
        assert f'id="{anchor}"' not in client.get(scoped_path).text
        old_event = client.get(f"{scoped_path}&event={event_id}")
        assert old_event.status_code == 200
        assert f'id="{anchor}"' in old_event.text
        assert "last: destructiveHint true → false" in old_event.text


def test_relaxations_name_tools_that_now_claim_less_and_never_new_tools():
    old = tool_extras([annotated("rm", annotations={"readOnlyHint": False}),
                       annotated("pay", annotations={"destructiveHint": True}),
                       annotated("ls", annotations={"readOnlyHint": True})])[1]
    new = tool_extras([annotated("rm", annotations={"readOnlyHint": True}),
                       annotated("pay", annotations={"destructiveHint": False}),
                       annotated("ls", annotations={"readOnlyHint": True, "title": "List"}),
                       annotated("cat", annotations={"readOnlyHint": True}),
                       annotated("fresh", annotations={"readOnlyHint": True})])[1]
    # "cat" was listed before without hints (in the previous tool set, not in the extras body);
    # "fresh" is new: its hints are its first word, not a change.
    d = extras_diff(old, new, listed={"rm", "pay", "ls", "cat"})
    assert d["relaxations"] == ["cat", "pay", "rm"]
    assert "escalations" not in d
    # Without the previous tool set only the tools in the earlier extras body count.
    assert extras_diff(old, new)["relaxations"] == ["pay", "rm"]
    # Absent destructiveHint means destructive: absent -> false relaxes; absent readOnlyHint is false.
    assert extras_diff({"tools": {"w": {"title": "W"}}},
                       {"tools": {"w": {"annotations": {"destructiveHint": False}}}})["relaxations"] == ["w"]
    # The other way round is an escalation and not a relaxation.
    back = extras_diff(new, old, listed={"rm", "pay", "ls", "cat", "fresh"})
    assert back["escalations"] == ["cat", "fresh", "pay", "rm"] and "relaxations" not in back
    assert back["tools"]["pay"]["annotations"] == {"destructiveHint": [False, True]}


def test_the_crawler_records_a_listed_tool_that_starts_claiming_read_only(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/r"] = [tool("drop_table", "Drop a table."), annotated("query", "Run a query.", title="Query")]
    target = make_target("io.example/db", "/r")
    world["targets"].append(target)
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/r"] = [annotated("drop_table", "Drop a table.", annotations={"readOnlyHint": True}),
                       annotated("query", "Run a query.", title="Query")]
    s.crawler.run()
    _, changes, _ = _extras_rows(s, target.id)
    summary = json.loads(changes[-1]["summary"])
    assert summary["relaxations"] == ["drop_table"] and "escalations" not in summary


def test_the_backfill_adds_relaxations_to_older_changes_once(world):
    from histor.extras_relaxations import relaxations

    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    mcp.tools["/b"] = [tool("wipe", "Wipe."), annotated("pay", "Pay.", annotations={"destructiveHint": True})]
    target = make_target("io.example/b", "/b")
    world["targets"].append(target)
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/b"] = [annotated("wipe", "Wipe.", annotations={"readOnlyHint": True}),
                       annotated("pay", "Pay.", annotations={"destructiveHint": False})]
    s.crawler.run()
    clock.advance(days=1)
    mcp.tools["/b"] = [annotated("wipe", "Wipe.", annotations={"readOnlyHint": False}),
                       annotated("pay", "Pay.", annotations={"destructiveHint": False})]
    s.crawler.run()
    # As code before 2026-11-23 wrote them: no relaxations.
    with s.store.db.transaction() as tx:
        for row in tx.execute("SELECT seq, summary FROM extras_changes WHERE target_id=?", (target.id,)):
            summary = json.loads(row["summary"])
            summary.pop("relaxations", None)
            tx.execute("UPDATE extras_changes SET summary=? WHERE seq=?", (json.dumps(summary), row["seq"]))
    _, changes, _ = _extras_rows(s, target.id)
    untouched = changes[1]["summary"]

    counted = relaxations(s.store.db, write=False)
    assert counted["changes"] == 2 and counted["withRelaxations"] == 1 and counted["written"] == 0
    filled = relaxations(s.store.db, write=True, page=1)
    assert filled["written"] == 1 and filled["unreadable"] == 0
    _, changes, _ = _extras_rows(s, target.id)
    assert json.loads(changes[0]["summary"])["relaxations"] == ["pay", "wipe"]
    assert changes[1]["summary"] == untouched
    again = relaxations(s.store.db, write=True)
    assert again["written"] == 0 and again["alreadySet"] == 1
