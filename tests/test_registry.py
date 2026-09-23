"""Which registry entries become targets, and why the others are recorded rather than dropped."""

from __future__ import annotations

from histor.registry import targets_from_servers


def entry(name, remotes, *, latest=True, status="active", version="1.0.0"):
    return {"server": {"name": name, "version": version, "remotes": remotes},
            "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": latest, "status": status,
                                                                     "publishedAt": "2026-01-01"}}}


def test_one_target_per_remote_with_skip_reasons():
    targets = targets_from_servers([
        entry("a/one", [{"type": "streamable-http", "url": "https://a.example/mcp"},
                        {"type": "sse", "url": "https://a.example/sse"}]),
        entry("b/templ", [{"type": "streamable-http", "url": "https://b.example/{tenant}/mcp"}]),
        entry("c/clear", [{"type": "streamable-http", "url": "http://c.example/mcp"}]),
        entry("d/local", []),
    ])
    reasons = {(t.name, t.transport): t.skip_reason for t in targets}
    assert reasons == {
        ("a/one", "streamable-http"): None,
        ("a/one", "sse"): "transport-not-observed",
        ("b/templ", "streamable-http"): "templated-url",
        ("c/clear", "streamable-http"): "cleartext-url",
    }


def test_the_latest_record_wins_and_deleted_ones_vanish():
    targets = targets_from_servers([
        entry("a/x", [{"type": "streamable-http", "url": "https://old.example"}], latest=False, version="0.9"),
        entry("a/x", [{"type": "streamable-http", "url": "https://new.example"}], latest=True, version="1.0"),
        entry("b/y", [{"type": "streamable-http", "url": "https://b.example"}], status="deleted"),
    ])
    assert [(t.name, t.endpoint) for t in targets] == [("a/x", "https://new.example")]


def test_target_ids_are_stable_and_distinct_per_endpoint():
    one = targets_from_servers([entry("a/x", [{"type": "streamable-http", "url": "https://1.example"},
                                               {"type": "streamable-http", "url": "https://2.example"}])])
    assert len({t.id for t in one}) == 2
    again = targets_from_servers([entry("a/x", [{"type": "streamable-http", "url": "https://1.example"}])])
    assert again[0].id == one[0].id


def test_an_operator_can_opt_out_by_host_or_url_and_stays_listed(tmp_path):
    from histor.registry import OPT_OUT, load_opt_out, opted_out, targets_from_servers

    (tmp_path / "opt-out.txt").write_text("# left out on request\nquiet.example   # issue 12\nhttps://api.other.test/private\n")
    entries = load_opt_out("Loud.Example, ", tmp_path / "opt-out.txt")
    assert entries == ("loud.example", "quiet.example", "https://api.other.test/private")
    assert opted_out("https://mcp.quiet.example/x", entries) and opted_out("https://loud.example/", entries)
    assert opted_out("https://api.other.test/private/mcp", entries)
    assert not opted_out("https://api.other.test/public", entries) and not opted_out("https://notquiet.example/", entries)

    servers = [{"server": {"name": "io.example/q", "remotes": [{"type": "streamable-http", "url": "https://quiet.example/mcp"}]}},
               {"server": {"name": "io.example/n", "remotes": [{"type": "streamable-http", "url": "https://normal.test/mcp"}]}}]
    targets = {t.name: t for t in targets_from_servers(servers, opt_out=entries)}
    assert targets["io.example/q"].skip_reason == OPT_OUT, "listed with the reason, never silently dropped"
    assert targets["io.example/n"].skip_reason is None
