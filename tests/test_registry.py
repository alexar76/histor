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


def test_the_shipped_curated_list_is_valid_and_in_its_own_namespace():
    from histor.packages import parse_package
    from histor.registry import curated_targets
    from histor.subject import REGISTRY_CURATED

    targets = curated_targets(observe_packages=True)
    assert targets, "the curated list ships with the package"
    assert {t.registry for t in targets} == {REGISTRY_CURATED}, "a curated name never claims a registry listing"
    assert len({t.endpoint for t in targets}) == len(targets) and len({t.id for t in targets}) == len(targets)
    assert all(t.skip_reason is None and t.title for t in targets)
    remote = [t for t in targets if t.transport == "streamable-http"]
    packages = [t for t in targets if t.transport == "stdio"]
    assert len(remote) + len(packages) == len(targets)
    assert all(t.endpoint.startswith("https://") for t in remote)
    assert all(parse_package(t.endpoint) for t in packages), "package entries are normalised npm:/pypi: keys"
    assert "https://mcp.deepwiki.com/mcp" in {t.endpoint for t in remote}
    assert {"npm:@upstash/context7-mcp", "pypi:mcp-server-fetch"} <= {t.endpoint for t in packages}
    assert all(t.skip_reason == "sandbox-not-configured" for t in curated_targets() if t.transport == "stdio"), \
        "without a sandbox a curated package is listed, not run"


    import json

    from histor.registry import CURATED_PATH
    entries = json.loads(CURATED_PATH.read_text(encoding="utf-8"))["servers"]
    assert all(e.get("source") for e in entries), "every entry says where it came from"


def test_curated_entries_fill_gaps_and_give_way_to_the_registry(tmp_path):
    import json

    from histor.registry import OPT_OUT, curated_targets, with_curated
    from histor.subject import REGISTRY_CURATED, REGISTRY_OFFICIAL

    path = tmp_path / "curated.json"
    path.write_text(json.dumps({"servers": [
        {"name": "com.both/x", "endpoint": "https://both.example/mcp", "title": "Both", "description": "d"},
        {"name": "com.only/y", "endpoint": "https://only.example/mcp", "title": "Only", "description": "d"},
        {"name": "com.dup/z", "endpoint": "https://only.example/mcp"},
        {"name": "com.clear/w", "endpoint": "http://clear.example/mcp"},
        {"name": "com.quiet/v", "endpoint": "https://quiet.example/mcp"},
        {"name": "", "endpoint": "https://nameless.example/mcp"},
    ]}))
    curated = curated_targets(path, opt_out=("quiet.example",))
    reasons = {t.name: t.skip_reason for t in curated}
    assert reasons == {"com.both/x": None, "com.only/y": None, "com.clear/w": "cleartext-url", "com.quiet/v": OPT_OUT}

    listed = targets_from_servers([entry("io.example/both", [{"type": "streamable-http", "url": "https://both.example/mcp"}])])
    merged = with_curated(listed, curated)
    by_endpoint = {t.endpoint: t for t in merged}
    assert len(by_endpoint) == len(merged), "one endpoint, one record"
    assert by_endpoint["https://both.example/mcp"].registry == REGISTRY_OFFICIAL, "the registry's listing wins"
    assert by_endpoint["https://only.example/mcp"].registry == REGISTRY_CURATED
