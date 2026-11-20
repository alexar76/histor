"""The subregistry: registry entries copied from an upstream and served in the MCP registry's v0.1 API."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from histor import subregistry
from histor.app import create_app
from histor.service import scheduler
from histor.subregistry import OBSERVED_KEY, OFFICIAL_KEY, parse_allow, parse_path, sync, time_key
from tests.conftest import make_target, tool

UPSTREAM = "https://registry.test"
T0 = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


def entry(name: str, version: str = "1.0.0", *, latest: bool = True, status: str = "active",
          updated: str = "2026-10-01T00:00:00.000000Z", published: str | None = None,
          meta: dict[str, Any] | None = None, **server: Any) -> dict[str, Any]:
    official = {"status": status, "publishedAt": published or updated, "updatedAt": updated, "isLatest": latest}
    return {
        "server": {"$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
                   "name": name, "description": f"{name} description", "version": version,
                   "remotes": [{"type": "streamable-http", "url": f"https://{name.split('/')[-1]}.test/mcp"}], **server},
        "_meta": {OFFICIAL_KEY: official, **(meta or {})},
    }


class Upstream:
    """``GET /v0.1/servers`` over ``records``, as the official registry answers it: ordered by name then
    version, ``version=latest``, ``search``, ``updated_since`` (which brings deleted entries), a cursor."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.requests: list[dict[str, str]] = []
        self.fail_on: int | None = None   # the request number that answers 404

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        if self.fail_on == len(self.requests):
            return httpx.Response(404)
        assert request.url.path == "/v0.1/servers"
        rows = sorted(self.records, key=lambda r: (r["server"]["name"], r["server"]["version"]))
        blank = {"isLatest": True, "status": "active", "updatedAt": "2026-10-01T00:00:00Z"}
        official = lambda r: {**blank, **(r["_meta"].get(OFFICIAL_KEY) or {})}  # noqa: E731
        if params.get("version") == "latest":
            rows = [r for r in rows if official(r)["isLatest"]]
        if params.get("search"):
            rows = [r for r in rows if params["search"].lower() in r["server"]["name"].lower()]
        if params.get("updated_since"):
            rows = [r for r in rows if time_key(official(r)["updatedAt"]) >= time_key(params["updated_since"])]
        elif params.get("include_deleted") != "true":
            rows = [r for r in rows if official(r)["status"] != "deleted"]
        if params.get("cursor"):
            name, _, version = params["cursor"].partition(":")
            rows = [r for r in rows if (r["server"]["name"], r["server"]["version"]) > (name, version)]
        size = int(params.get("limit", 30))
        page, metadata = rows[:size], {"count": min(len(rows), size)}
        if len(rows) > size:
            metadata["nextCursor"] = f"{page[-1]['server']['name']}:{page[-1]['server']['version']}"
        return httpx.Response(200, content=json.dumps({"servers": page, "metadata": metadata}),
                              headers={"content-type": "application/json"})


def run_sync(store, upstream: Upstream, *, at: datetime = T0, **kwargs: Any) -> dict[str, Any]:
    return sync(store, UPSTREAM, transport=upstream.transport(), pause_s=0, retry_delay=0, clock=lambda: at, **kwargs)


def rows(store, where: str = "1=1") -> list[dict[str, Any]]:
    with store.db.read() as tx:
        return tx.execute(f"SELECT * FROM registry_entries WHERE {where} ORDER BY name, version")  # noqa: S608 - test text


@pytest.fixture
def store(world):
    return world["services"].store


@pytest.fixture
def client(world):
    services = world["services"]
    services.settings = replace(services.settings, subregistry=True, subregistry_sync_s=0)
    with TestClient(create_app(services)) as c:
        yield c, world


# -- the parts -----------------------------------------------------------------------------------


def test_time_key_puts_times_written_differently_in_time_order():
    written = ["2026-11-01T10:00:20Z", "2026-11-01T10:00:20.8Z", "2026-11-01T10:00:20.852269123Z",
               "2026-11-01T12:00:21+02:00", "2026-11-01T10:00:19.999999Z"]
    keys = [time_key(w) for w in written]
    assert all(len(k) == 27 for k in keys)
    assert sorted(keys) == [keys[4], keys[0], keys[1], keys[2], keys[3]]
    # Compared as written, the second sorts before the first: "." is below "Z".
    assert sorted(written[:2]) == [written[1], written[0]]
    for bad in ("2026-11-01", "2026-11-01T10:00:20", "yesterday", "", None, 5, "9" * 80):
        assert time_key(bad) is None


def test_parse_path_reads_the_encoded_slash_and_the_plain_one():
    assert parse_path(b"/v0.1/servers/io.example%2Fweather/versions/latest") == ("io.example/weather", "latest")
    assert parse_path(b"/v0.1/servers/io.example/weather/versions/latest") == ("io.example/weather", "latest")
    assert parse_path(b"/v0.1/servers/io.example%2Fweather/versions") == ("io.example/weather", None)
    assert parse_path(b"/v0.1/servers/io.example%2Fweather/versions/1.0.0%2Bbuild.5?include_deleted=true") == \
        ("io.example/weather", "1.0.0+build.5")
    # A server whose name is "versions", and a version called "versions".
    assert parse_path(b"/v0.1/servers/io.example%2Fversions/versions") == ("io.example/versions", None)
    assert parse_path(b"/v0.1/servers/io.example%2Fweather/versions/versions") == ("io.example/weather", "versions")
    for other in (b"/v0.1/servers/io.example%2Fweather", b"/v0.1/servers/noslash/versions/latest",
                  b"/v0.1/servers/io.example%2Fa%2Fb/versions/latest", b"/v0.1/servers/io.example%2Fweather/versions/a%2Fb",
                  b"/v0.1/servers/io.example%2Fwe%00ather/versions/1", b"/api/v1/servers/x/versions"):
        assert parse_path(other) is None


def test_an_allow_list_is_names_and_namespaces_and_refuses_what_is_neither():
    allow = parse_allow("# our servers\nio.github.acme/billing\n\nio.github.acme-tools/*   # all of them\n")
    assert allow.matches("io.github.acme/billing") and allow.matches("io.github.acme-tools/anything")
    assert not allow.matches("io.github.acme/other") and not allow.matches("io.github.acme-tools-evil/x")
    assert allow.size == 2 and parse_allow("io.github.acme-tools/*\nio.github.acme/billing").digest == allow.digest
    for bad in ("*", "io.github.acme", "io.github.acme/*/x", "https://example.com/a"):
        with pytest.raises(RuntimeError, match="line 1"):
            parse_allow(bad)
    with pytest.raises(RuntimeError, match="names no server"):
        parse_allow("# nothing\n\n")


# -- sync ----------------------------------------------------------------------------------------


def test_the_first_sync_copies_every_latest_version_as_it_was_sent(store):
    records = [entry(f"io.example/s{i:03d}") for i in range(230)]
    records += [entry("io.example/old", "0.9.0", latest=False), entry("io.example/old", "1.0.0")]
    records.append(entry("io.example/odd", description="lone \ud800 surrogate and a \x00", icons=[{"src": "https://x.test/i.png"}]))
    records.append({"server": {"name": "not a name", "version": "1"}, "_meta": {}})
    records.append({"server": {"name": "io.example/nan", "version": "1", "weight": float("nan")}, "_meta": {OFFICIAL_KEY: {"isLatest": True}}})
    records.append(entry("io.example/huge", description="x" * 70_000))
    upstream = Upstream(records)
    result = run_sync(store, upstream)

    assert result["mode"] == "full" and result["pages"] == 3 and result["stored"] == 232 and result["skipped"] == 3
    assert [r["limit"] for r in upstream.requests] == ["100"] * 3 and all(r["version"] == "latest" for r in upstream.requests)
    assert "updated_since" not in upstream.requests[0] and upstream.requests[1]["cursor"] == "io.example/s095:1.0.0"
    assert len(rows(store)) == 232 and not rows(store, "version='0.9.0'")
    page = subregistry.list_page(store, "https://histor.test", search="io.example/odd")
    assert page["servers"][0]["server"] == records[232]["server"]   # byte for byte what the upstream sent
    assert store.get_meta(subregistry.CHECKPOINT_META) == "2026-11-01T12:00:00.000000Z"
    assert json.loads(store.get_meta(subregistry.LAST_META))["at"] == "2026-11-01T12:00:00Z"


def test_a_later_sync_asks_what_changed_and_supersedes_the_old_version(store):
    upstream = Upstream([entry("io.example/a"), entry("io.example/b")])
    run_sync(store, upstream)
    upstream.records[0]["_meta"][OFFICIAL_KEY]["isLatest"] = False
    upstream.records.append(entry("io.example/a", "1.1.0", updated="2026-11-01T12:30:00.5Z"))
    upstream.records.append(entry("io.example/b", "1.0.0", status="deleted", updated="2026-11-01T12:40:00Z"))
    del upstream.records[1]
    upstream.requests.clear()
    result = run_sync(store, upstream, at=T0 + timedelta(hours=1))

    assert result["mode"] == "incremental" and result["stored"] == 2
    # Ten minutes before the last sync began, so an entry written while it ran is not lost.
    assert upstream.requests[0]["updated_since"] == "2026-11-01T11:50:00Z"
    kept = {(r["name"], r["version"]): (r["is_latest"], r["status"]) for r in rows(store)}
    assert kept == {("io.example/a", "1.0.0"): (0, "active"), ("io.example/a", "1.1.0"): (1, "active"),
                    ("io.example/b", "1.0.0"): (1, "deleted")}
    # The stored block of 1.0.0 still says isLatest: true; what is served does not.
    old = subregistry.one(store, "https://histor.test", "io.example/a", "1.0.0")
    assert old["_meta"][OFFICIAL_KEY]["isLatest"] is False
    assert subregistry.one(store, "https://histor.test", "io.example/a", "latest")["server"]["version"] == "1.1.0"
    assert subregistry.one(store, "https://histor.test", "io.example/b", "latest") is None
    assert subregistry.one(store, "https://histor.test", "io.example/b", "latest", include_deleted=True)["_meta"][OFFICIAL_KEY]["status"] == "deleted"


def test_a_sync_that_fails_part_way_keeps_its_pages_and_not_its_checkpoint(store):
    upstream = Upstream([entry(f"io.example/s{i:03d}") for i in range(150)])
    upstream.fail_on = 2
    with pytest.raises(httpx.HTTPStatusError):
        run_sync(store, upstream)
    assert len(rows(store)) == 100 and store.get_meta(subregistry.CHECKPOINT_META) is None
    upstream.fail_on = None
    assert run_sync(store, upstream, at=T0 + timedelta(minutes=5))["mode"] == "full"   # no checkpoint: all of it again
    assert len(rows(store)) == 150


def test_a_weekly_walk_marks_what_the_upstream_stopped_listing_and_a_short_walk_marks_nothing(store):
    upstream = Upstream([entry(f"io.example/s{i}") for i in range(10)])
    run_sync(store, upstream)
    assert run_sync(store, upstream, at=T0 + timedelta(days=6))["mode"] == "incremental"
    removed = upstream.records.pop(3)["server"]["name"]
    result = run_sync(store, upstream, at=T0 + timedelta(days=7, minutes=1))
    assert result["mode"] == "full" and result["gone"] == 1
    assert [r["name"] for r in rows(store, "status='deleted'")] == [removed]
    names = [s["server"]["name"] for s in subregistry.list_page(store, "https://histor.test")["servers"]]
    assert removed not in names and len(names) == 9

    # An upstream that answers with a tenth of the registry is having a bad day; nobody was removed.
    del upstream.records[1:]
    result = run_sync(store, upstream, at=T0 + timedelta(days=15))
    assert result["mode"] == "full" and result["gone"] == 0 and len(rows(store, "status='deleted'")) == 1


def test_a_copy_built_for_an_allow_list_holds_those_servers_only(store):
    upstream = Upstream([entry("io.github.acme/billing"), entry("io.github.acme/billing-old"), entry("io.github.acme-tools/a"),
                         entry("io.github.acme-tools/b"), entry("io.other/x"), entry("io.github.acme/gone", status="deleted")])
    allow = parse_allow("io.github.acme/billing\nio.github.acme-tools/*\nio.github.acme/gone\n")
    result = run_sync(store, upstream, allow=allow)

    assert result["mode"] == "allow-list" and result["stored"] == 4 and result["filtered"] == 1
    assert [r["search"] for r in upstream.requests] == ["io.github.acme/billing", "io.github.acme/gone", "io.github.acme-tools/"]
    assert [r["name"] for r in rows(store)] == ["io.github.acme-tools/a", "io.github.acme-tools/b", "io.github.acme/billing", "io.github.acme/gone"]
    listed = [s["server"]["name"] for s in subregistry.list_page(store, "https://histor.test")["servers"]]
    assert listed == ["io.github.acme-tools/a", "io.github.acme-tools/b", "io.github.acme/billing"]   # the deleted one is held, not listed

    # The list shrinks: what left it leaves the copy at the next sync, whatever that sync would have been.
    result = run_sync(store, upstream, allow=parse_allow("io.github.acme/billing"), at=T0 + timedelta(hours=1))
    assert result["gone"] == 3 and [r["name"] for r in rows(store)] == ["io.github.acme/billing"]
    # A server taken down upstream is marked in the copy by the next sync.
    upstream.records[0]["_meta"][OFFICIAL_KEY]["status"] = "deleted"
    run_sync(store, upstream, allow=parse_allow("io.github.acme/billing"), at=T0 + timedelta(hours=2))
    assert subregistry.list_page(store, "https://histor.test")["servers"] == []
    # No list any more: the copy is rebuilt from a full walk, not from "what changed since".
    upstream.requests.clear()
    assert run_sync(store, upstream, at=T0 + timedelta(hours=3))["mode"] == "full" and "search" not in upstream.requests[0]


# -- the routes ----------------------------------------------------------------------------------


def seed(world, records: list[dict[str, Any]]) -> None:
    run_sync(world["services"].store, Upstream(records))


def test_the_list_pages_to_the_end_with_the_filters_the_registry_has(client):
    c, world = client
    records = [entry(f"io.example/s{i:02d}") for i in range(45)]
    records += [entry("io.example/s00", "0.9.0", latest=False), entry("com.Other/Thing", updated="2026-10-20T00:00:00Z"),
                entry("io.example/removed", status="deleted", updated="2026-10-25T00:00:00Z")]
    store = world["services"].store
    with store.db.transaction() as tx:   # as two syncs would leave it: an old version and a deleted server
        subregistry._store_page(tx, records, allow=None, seen_at="2026-11-01T12:00:00.000000Z",
                                stats={"read": 0, "stored": 0, "skipped": 0, "filtered": 0})
        tx.execute("UPDATE registry_entries SET is_latest=0 WHERE version='0.9.0'")

    first = c.get("/v0.1/servers")
    assert first.status_code == 200 and first.headers["content-type"] == "application/json"
    assert first.json()["metadata"] == {"count": 30, "nextCursor": "io.example/s27:1.0.0"}
    seen, cursor, pages = [], None, 0
    while True:
        page = c.get("/v0.1/servers", params={"limit": 20, **({"cursor": cursor} if cursor else {})}).json()
        seen += [(s["server"]["name"], s["server"]["version"]) for s in page["servers"]]
        cursor, pages = page["metadata"].get("nextCursor"), pages + 1
        if not cursor:
            break
    assert pages == 3 and len(seen) == 47 and seen == sorted(seen) and ("io.example/removed", "1.0.0") not in seen
    assert seen[:3] == [("com.Other/Thing", "1.0.0"), ("io.example/s00", "0.9.0"), ("io.example/s00", "1.0.0")]

    names = lambda **params: [s["server"]["name"] for s in c.get("/v0.1/servers", params={"limit": 100, **params}).json()["servers"]]  # noqa: E731
    assert len(names(version="latest")) == 46 and names(version="0.9.0") == ["io.example/s00"]
    assert names(search="OTHER/th") == ["com.Other/Thing"] and names(search="s0_") == [] and names(search="%") == []
    assert names(include_deleted="true").count("io.example/removed") == 1
    # A client that syncs hears about the removal without asking for deleted entries.
    assert names(updated_since="2026-10-15T00:00:00Z") == ["com.Other/Thing", "io.example/removed"]
    for params in ({"cursor": "nonsense"}, {"limit": "0"}, {"limit": "101"}, {"limit": "many"},
                   {"updated_since": "last week"}, {"version": "a/b"}):
        bad = c.get("/v0.1/servers", params=params)
        assert bad.status_code == 400 and bad.json()["error"] and bad.headers["access-control-allow-origin"] == "*", params


def test_one_server_by_encoded_name_by_plain_name_and_its_versions(client):
    c, world = client
    seed(world, [entry("io.example/weather", "1.0.0", latest=False, updated="2026-09-01T00:00:00Z"),
                 entry("io.example/weather", "1.2.0+build.7", updated="2026-10-01T00:00:00Z")])
    with world["services"].store.db.transaction() as tx:   # the superseded version, as an earlier sync left it
        subregistry._store_page(tx, [entry("io.example/weather", "1.0.0", latest=False, updated="2026-09-01T00:00:00Z")], allow=None,
                                seen_at="2026-09-01T00:00:00.000000Z", stats={"read": 0, "stored": 0, "skipped": 0, "filtered": 0})

    latest = c.get("/v0.1/servers/io.example%2Fweather/versions/latest")
    assert latest.status_code == 200 and latest.json()["server"]["version"] == "1.2.0+build.7"
    assert latest.json()["_meta"][OFFICIAL_KEY] == {"status": "active", "publishedAt": "2026-10-01T00:00:00Z",
                                                    "updatedAt": "2026-10-01T00:00:00Z", "isLatest": True}
    assert c.get("/v0.1/servers/io.example/weather/versions/latest").json() == latest.json()
    assert c.get("/v0.1/servers/io.example%2Fweather/versions/1.2.0%2Bbuild.7").json() == latest.json()
    assert c.get("/v0.1/servers/io.example%2Fweather/versions/1.0.0").json()["_meta"][OFFICIAL_KEY]["isLatest"] is False
    history = c.get("/v0.1/servers/io.example%2Fweather/versions").json()
    assert [s["server"]["version"] for s in history["servers"]] == ["1.2.0+build.7", "1.0.0"] and history["metadata"] == {"count": 2}
    assert c.head("/v0.1/servers/io.example%2Fweather/versions/latest").status_code == 200

    for path in ("/v0.1/servers/io.example%2Fnobody/versions/latest", "/v0.1/servers/io.example%2Fweather/versions/9.9.9",
                 "/v0.1/servers/io.example%2Fnobody/versions", "/v0.1/servers/io.example%2Fweather", "/v0.1/servers/x"):
        missing = c.get(path)
        assert missing.status_code == 404 and missing.headers["content-type"] == "application/problem+json", path
        assert missing.json() == {"title": "Not Found", "status": 404, "detail": "Server not found", "error": "Server not found"}


def test_every_route_answers_another_origin_as_a_host_application_needs(client):
    c, world = client
    seed(world, [entry("io.example/weather")])
    wanted = {"access-control-allow-origin": "*", "access-control-allow-methods": "GET, OPTIONS",
              "access-control-allow-headers": "Authorization, Content-Type"}
    for path in ("/v0.1/servers", "/v0.1/servers/io.example%2Fweather/versions/latest", "/v0.1/servers/io.example%2Fweather/versions",
                 "/v0.1/servers/io.example%2Fnobody/versions/latest"):
        got = c.get(path, headers={"Origin": "https://vscode.dev"})
        assert {k: got.headers.get(k) for k in wanted} == wanted, path
        pre = c.options(path, headers={"Origin": "https://vscode.dev", "Access-Control-Request-Method": "GET",
                                       "Access-Control-Request-Headers": "authorization"})
        assert pre.status_code == 204 and {k: pre.headers.get(k) for k in wanted} == wanted, path
    assert c.post("/v0.1/servers", json={}).status_code == 405   # read-only: nobody publishes here


def test_the_latest_version_carries_what_histor_observed_of_each_endpoint(client):
    c, world = client
    world["mcp"].tools["/a"] = [tool("get_weather", "Return the weather."), tool("list_cities", "List cities.")]
    world["targets"] += [make_target("io.example/weather", "/a"), make_target("io.example/weather", "/down", skip="auth")]
    world["services"].crawler.run()
    seed(world, [entry("io.example/weather", "1.0.0"), entry("io.example/unobserved")])
    with world["services"].store.db.transaction() as tx:
        subregistry._store_page(tx, [entry("io.example/weather", "0.5.0", latest=False)], allow=None,
                                seen_at="2026-09-01T00:00:00.000000Z", stats={"read": 0, "stored": 0, "skipped": 0, "filtered": 0})
    target = next(t for t in world["services"].store.all_targets() if t["endpoint"].endswith("/a"))

    body = c.get("/v0.1/servers/io.example%2Fweather/versions/latest").json()
    observed = body["_meta"][OBSERVED_KEY]
    assert observed["type"] == "histor.observed/v1" and observed["log"] == world["services"].settings.public_base
    seen = {t["endpoint"]: t for t in observed["targets"]}
    assert set(seen) == {"https://127.0.0.1/a", "https://127.0.0.1/down"}
    read = seen["https://127.0.0.1/a"]
    assert read["toolSetDigest"] == target["current_toolset"] and read["toolSetDigest"].startswith("sha256-")
    assert read["toolCount"] == 2 and read["lastStatus"] == "ok" and read["changes"] == 0 and read["notAttempted"] is None
    assert read["page"].endswith(f"/s/{target['id']}") and read["unchangedSince"] == target["unchanged_since"]
    assert seen["https://127.0.0.1/down"]["notAttempted"] == "auth" and seen["https://127.0.0.1/down"]["toolSetDigest"] is None
    # The observation is of what the registry lists now, so an older version does not carry it, and a
    # server HISTOR lists nothing for has no block at all.
    assert OBSERVED_KEY not in c.get("/v0.1/servers/io.example%2Fweather/versions/0.5.0").json()["_meta"]
    assert OBSERVED_KEY not in c.get("/v0.1/servers/io.example%2Funobserved/versions/latest").json()["_meta"]
    listed = {s["server"]["name"] + "@" + s["server"]["version"]: s for s in c.get("/v0.1/servers").json()["servers"]}
    assert listed["io.example/weather@1.0.0"]["_meta"][OBSERVED_KEY] == observed


def test_a_copy_that_observes_nothing_passes_on_its_upstreams_observation(client):
    c, world = client
    theirs = {"type": "histor.observed/v1", "log": "https://histor.modelmarket.dev",
              "targets": [{"id": "e1eb27d49e525f9f", "endpoint": "https://weather.test/mcp", "toolSetDigest": "sha256-abc="}]}
    seed(world, [entry("io.example/weather", meta={OBSERVED_KEY: theirs, "com.example.ratings/v1": {"stars": 4.5}})])
    meta = c.get("/v0.1/servers/io.example%2Fweather/versions/latest").json()["_meta"]
    assert meta[OBSERVED_KEY] == theirs and meta["com.example.ratings/v1"] == {"stars": 4.5}


def test_an_entry_with_a_lone_surrogate_is_served_and_not_a_500(client):
    c, world = client
    seed(world, [entry("io.example/odd", description="lone \ud800 surrogate, a \x00 and  ")])
    answer = c.get("/v0.1/servers/io.example%2Fodd/versions/latest")
    assert answer.status_code == 200 and answer.content.isascii()
    assert answer.json()["server"]["description"] == "lone \ud800 surrogate, a \x00 and  "
    assert c.get("/v0.1/servers").status_code == 200


def test_the_status_route_says_what_the_copy_holds(client):
    c, world = client
    assert c.get("/api/v1/subregistry").json()["lastSync"] is None
    seed(world, [entry("io.example/a"), entry("io.example/b", status="deprecated")])
    status = c.get("/api/v1/subregistry").json()
    assert status["servers"] == 2 and status["entries"] == 2 and status["api"] == "v0.1" and status["allowList"] is None
    assert status["observedKey"] == OBSERVED_KEY and status["upstream"] == world["services"].settings.registry_url
    assert status["lastSync"]["mode"] == "full" and status["lastSync"]["stored"] == 2


def test_an_instance_that_did_not_turn_it_on_has_none_of_the_routes(world):
    with TestClient(create_app(world["services"])) as c:
        for path in ("/v0.1/servers", "/v0.1/servers/io.example%2Fweather/versions/latest", "/api/v1/subregistry"):
            assert c.get(path).status_code == 404, path


def test_the_schedule_syncs_the_copy_on_an_instance_that_never_crawls(world, monkeypatch):
    services = world["services"]
    assert services.settings.crawl_interval_s == 0 and services.settings.remote_interval_s == 0
    calls: list[bool] = []

    def fake_sync(given, *, full: bool = False) -> dict[str, Any]:
        calls.append(given is services)
        services.stop.set()
        return {"stored": 0, "gone": 0, "mode": "incremental"}

    monkeypatch.setattr(subregistry, "sync_for", fake_sync)
    scheduler(services, first_wait=0, tick=0.01)          # off: returns at once, as before
    assert calls == []
    services.settings = replace(services.settings, subregistry=True, subregistry_sync_s=3600)
    worker = threading.Thread(target=scheduler, args=(services,), kwargs={"first_wait": 0, "tick": 0.01}, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive() and calls == [True]
