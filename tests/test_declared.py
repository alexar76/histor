"""Tool sets a publisher declares, in its registry entry or in a file on its own domain: read,
recorded apart from observations, answered by /check, refreshed between crawls, withdrawn when
the publisher stops naming them."""

from __future__ import annotations

import dataclasses
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from histor.app import create_app
from histor.check import CHECK_TYPE
from histor.declared import (
    FIELD,
    MAX_FILE_BYTES,
    MAX_FILE_SETS,
    MAX_SETS,
    PUBLISHER_PROVIDED,
    DeclarationError,
    DeclarationFetcher,
    declared_tool_sets,
    parse_file,
    url_refusal,
)
from histor.logbook import verify_document
from histor.registry import targets_from_servers
from histor.subject import tool_set_digest
from tests.conftest import make_target, tool

LAUNCHER = [tool("install_auten", "The runner is not installed on this machine yet.")]
RUNNER = [tool("screenshot", "Take a screenshot."), tool("click", "Click at a point.")]
RUNNER_DIGEST = tool_set_digest(RUNNER)[0]
RUNNER_2 = [*RUNNER, tool("type_text", "Type text.")]
RUNNER_2_DIGEST = tool_set_digest(RUNNER_2)[0]
FILE_URL = "https://auten.ai/.well-known/mcp-tool-sets.json"


def server(block=None, name="ai.example/launcher", **extra):
    srv = {"name": name, "version": "0.1.5", **extra}
    if block is not None:
        srv["_meta"] = {PUBLISHER_PROVIDED: {FIELD: block}}
    return srv


def digest(c: str) -> str:
    return "sha256-" + c * 43 + "="


# -- reading a declaration ------------------------------------------------------------------


def test_a_declaration_is_read_and_checked_entry_by_entry():
    sets, ignored, url = declared_tool_sets(server({"mtl": "1", "sets": [
        {"digest": RUNNER_DIGEST, "count": 2, "when": "the runner​ is\n installed‮"},
        {"digest": "sha256-short="},
        {"digest": RUNNER_DIGEST},
        {"digest": digest("B"), "count": True},
        "not an object",
    ]}))
    assert sets == [{"digest": RUNNER_DIGEST, "count": 2, "when": "the runner is installed"}] and url is None
    assert [r.split(":")[0] for r in ignored] == ["sets[1]", "sets[2]", "sets[3]", "sets[4]"]

    assert declared_tool_sets(server()) == ([], [], None)
    assert declared_tool_sets({"name": "x", "_meta": {PUBLISHER_PROVIDED: {"other": 1}}}) == ([], [], None)
    assert declared_tool_sets(server({"mtl": "2", "sets": [{"digest": RUNNER_DIGEST}]}))[1][0].startswith('"mtl"')
    assert declared_tool_sets(server({"mtl": "1", "sets": []}))[1] == ['"sets" must be a non-empty list']
    assert declared_tool_sets(server({"mtl": "1"}))[1] == ['"sets" or "url" is needed']
    assert declared_tool_sets(server(["not", "an", "object"]))[1] == [f"{FIELD} is not an object"]

    many = [{"digest": digest(chr(65 + i))} for i in range(MAX_SETS + 2)]
    sets, ignored, _ = declared_tool_sets(server({"mtl": "1", "sets": many}))
    assert len(sets) == MAX_SETS and ignored == [f"sets[{MAX_SETS}:]: more than {MAX_SETS} sets"]


def test_a_file_is_taken_only_from_the_domain_the_registry_verified():
    assert url_refusal(FILE_URL, "ai.auten/mcp") is None
    assert url_refusal("https://docs.auten.ai/sets.json", "ai.auten/mcp") is None
    assert "auten.ai" in url_refusal("https://evil.example/sets.json", "ai.auten/mcp")
    assert url_refusal("https://notauten.ai/sets.json", "ai.auten/mcp")  # a suffix match is not a subdomain
    assert url_refusal("http://auten.ai/sets.json", "ai.auten/mcp") == '"url" must be https'
    assert url_refusal("https://auten.ai:8443/sets.json", "ai.auten/mcp")
    assert url_refusal("https://user:pw@auten.ai/sets.json", "ai.auten/mcp")
    assert url_refusal(42, "ai.auten/mcp")
    # A GitHub namespace: the account's Pages site or its raw files, nobody else's.
    assert url_refusal("https://alexar76.github.io/sets.json", "io.github.alexar76/warden") is None
    assert url_refusal("https://raw.githubusercontent.com/alexar76/warden/main/sets.json", "io.github.alexar76/warden") is None
    assert url_refusal("https://raw.githubusercontent.com/mallory/warden/main/sets.json", "io.github.alexar76/warden")
    assert url_refusal("https://github.io/sets.json", "io.github.alexar76/warden")
    assert url_refusal("https://localhost/sets.json", "localhost/x")

    sets, ignored, url = declared_tool_sets(server({"mtl": "1", "url": FILE_URL}, name="ai.auten/mcp"))
    assert (sets, ignored, url) == ([], [], FILE_URL)
    sets, ignored, url = declared_tool_sets(server({"mtl": "1", "url": FILE_URL, "sets": [{"digest": RUNNER_DIGEST}]},
                                                   name="ai.other/mcp"))
    assert url is None and sets == [{"digest": RUNNER_DIGEST}] and "other.ai" in ignored[0]


def test_a_declaration_file_is_parsed_strictly():
    body = json.dumps({"mtl": "1", "sets": [{"digest": RUNNER_DIGEST, "when": "runner 1.4, macOS"}, {"digest": "bad"}]})
    sets, ignored = parse_file(body.encode())
    assert sets == [{"digest": RUNNER_DIGEST, "when": "runner 1.4, macOS"}] and ignored[0].startswith("sets[1]")
    assert parse_file(b'{"mtl": "1", "sets": []}') == ([], []), "an explicit empty list withdraws every set"
    many = [{"digest": f"sha256-{i:04d}" + "A" * 39 + "="} for i in range(MAX_FILE_SETS + 3)]
    sets, ignored = parse_file(json.dumps({"mtl": "1", "sets": many}).encode())
    assert len(sets) == MAX_FILE_SETS and ignored == [f"sets[{MAX_FILE_SETS}:]: more than {MAX_FILE_SETS} sets"]
    for bad in (b"not json", b"[]", b'{"mtl": "2", "sets": []}', b'{"mtl": "1", "sets": [{"digest": "x"}]}', b'{"mtl": "1"}'):
        with pytest.raises(DeclarationError):
            parse_file(bad)


def public_dns(host, port, type=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_the_file_reader_pins_the_address_and_follows_nothing():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["host"]))
        path = request.url.path
        if path == "/ok.json":
            return httpx.Response(200, json={"mtl": "1", "sets": [{"digest": RUNNER_DIGEST}]})
        if path == "/moved.json":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        if path == "/big.json":
            return httpx.Response(200, content=b" " * (MAX_FILE_BYTES + 1))
        return httpx.Response(404)

    fetcher = DeclarationFetcher(transport=httpx.MockTransport(handler), resolver=public_dns)
    assert fetcher.fetch("https://auten.ai/ok.json") == ([{"digest": RUNNER_DIGEST}], [])
    assert seen[0] == ("https://93.184.216.34/ok.json", "auten.ai"), "the checked address, the name in Host"
    for path, why in (("/moved.json", "redirects are not followed"), ("/big.json", "larger than"), ("/gone.json", "http-404")):
        with pytest.raises(DeclarationError, match=why):
            fetcher.fetch(f"https://auten.ai{path}")

    private = DeclarationFetcher(transport=httpx.MockTransport(handler),
                                 resolver=lambda h, p, type=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", p))])
    with pytest.raises(DeclarationError, match="refused"):
        private.fetch("https://auten.ai/ok.json")
    assert len(seen) == 4, "a private address is never dialled"


def test_every_target_of_the_server_carries_the_declaration():
    targets = targets_from_servers([{"server": server(
        {"mtl": "1", "sets": [{"digest": RUNNER_DIGEST, "when": "the runner is installed"}], "url": FILE_URL},
        name="ai.auten/mcp",
        remotes=[{"type": "streamable-http", "url": "https://launcher.example.com/mcp"}],
        packages=[{"registryType": "npm", "identifier": "@example/launcher", "transport": {"type": "stdio"}}],
    )}], observe_packages=True)
    assert {t.endpoint for t in targets} == {"https://launcher.example.com/mcp", "npm:@example/launcher"}
    assert all(t.declared == ({"digest": RUNNER_DIGEST, "when": "the runner is installed"},) for t in targets)
    assert all(t.declared_url == FILE_URL for t in targets)


# -- recorded, answered, withdrawn ------------------------------------------------------------


class FakeFiles:
    """Stands in for the publisher's web server."""

    def __init__(self):
        self.files: dict[str, list[dict] | Exception] = {}
        self.reads: list[str] = []

    def fetch(self, url):
        self.reads.append(url)
        content = self.files.get(url, DeclarationError("http-404"))
        if isinstance(content, Exception):
            raise content
        return content, []


@pytest.fixture
def launcher(world):
    world["mcp"].tools["/l"] = LAUNCHER
    t = dataclasses.replace(make_target("ai.example/launcher", "/l"),
                            declared=({"digest": RUNNER_DIGEST, "count": 2, "when": "the runner is installed"},))
    world["targets"].append(t)
    stats = world["services"].crawler.run()
    with TestClient(create_app(world["services"])) as client:
        yield client, world, t, stats


def test_check_answers_a_declared_set_as_the_publishers_word(launcher):
    client, world, t, stats = launcher
    assert stats["declared_tool_sets"] == {"servers": 1, "sets": 1, "withdrawn": 0}

    answer = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()
    assert answer["match"] == "different", "a declaration is never an observation"
    assert answer["declared"]["toolSetDigest"] == RUNNER_DIGEST and answer["declared"]["source"] == "registry"
    assert answer["declared"]["when"] == "the runner is installed" and answer["declared"]["toolCount"] == 2
    assert answer["declared"]["registryVersion"] == "1.0.0" and "withdrawn" not in answer["declared"]
    assert 'declares this set in its registry entry ("the runner is installed")' in answer["note"]
    assert verify_document(answer, world["services"].key.did, CHECK_TYPE), "the declared block is signed with the rest"

    observed = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": LAUNCHER}).json()
    assert observed["match"] == "same" and "declared" not in observed

    other = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": [tool("x", "y")]}).json()
    assert other["match"] == "different" and "declared" not in other and "publisher" not in other["note"]

    page = client.get(f"/api/v1/servers/{t.id}").json()
    assert [d["toolSetDigest"] for d in page["declaredToolSets"]] == [RUNNER_DIGEST] and page["declarationFile"] is None
    assert client.get("/api/v1/stats").json()["lastRun"]["declaredToolSets"] == stats["declared_tool_sets"]


def test_the_crawl_says_why_an_entry_was_skipped(world):
    world["mcp"].tools["/l"] = LAUNCHER
    t = dataclasses.replace(make_target("ai.example/launcher", "/l"), declared_ignored=("sets[0]: not an object",))
    world["targets"].append(t)
    stats = world["services"].crawler.run()
    assert stats["declared_tool_sets"]["ignored"] == [{"server": "ai.example/launcher", "reason": "sets[0]: not an object"}]


def test_a_set_the_entry_stops_naming_is_withdrawn_and_kept(launcher):
    client, world, t, _ = launcher
    world["targets"][:] = [dataclasses.replace(t, declared=())]
    world["clock"].advance(days=1)
    stats = world["services"].crawler.run()
    assert stats["declared_tool_sets"] == {"servers": 0, "sets": 0, "withdrawn": 1}

    answer = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()
    assert answer["match"] == "different" and "declared" not in answer
    history = client.get(f"/api/v1/servers/{t.id}").json()["declaredToolSets"]
    assert len(history) == 1 and history[0]["withdrawn"] > history[0]["firstDeclared"]


def test_a_registry_outage_or_a_delisting_withdraws_nothing(launcher):
    client, world, t, _ = launcher
    world["targets"].clear()  # the server left the registry: its record stops, its declaration stands as read
    world["clock"].advance(days=1)
    world["services"].crawler.run()
    answer = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()
    assert answer["declared"]["toolSetDigest"] == RUNNER_DIGEST


def test_a_launcher_that_never_answered_still_has_its_declaration_answered(world):
    world["mcp"].status["/gone"] = 500
    t = dataclasses.replace(make_target("ai.example/exits", "/gone"), declared=({"digest": RUNNER_DIGEST},))
    world["targets"].append(t)
    world["services"].crawler.run()
    with TestClient(create_app(world["services"])) as client:
        answer = client.post("/api/v1/check", json={"endpoint": t.endpoint, "toolSetDigest": RUNNER_DIGEST}).json()
    assert answer["match"] == "not-observed" and answer["declared"]["toolSetDigest"] == RUNNER_DIGEST
    assert "declares this set" in answer["note"]


# -- a declaration file on the publisher's domain -------------------------------------------------


@pytest.fixture
def filed(world):
    """A launcher whose entry points at a file listing one runner version; the registry names none."""
    files = FakeFiles()
    files.files[FILE_URL] = [{"digest": RUNNER_DIGEST, "count": 2, "when": "runner 1.4, macOS"}]
    world["services"].crawler.declarations = files
    world["mcp"].tools["/l"] = LAUNCHER
    t = dataclasses.replace(make_target("ai.auten/mcp", "/l"), declared_url=FILE_URL)
    world["targets"].append(t)
    stats = world["services"].crawler.run()
    with TestClient(create_app(world["services"])) as client:
        yield client, world, t, files, stats


def test_a_set_from_the_file_is_answered_with_where_it_came_from(filed):
    client, world, t, files, stats = filed
    assert files.reads == [FILE_URL], "read once per crawl, before the targets are written"
    assert stats["declared_tool_sets"] == {"servers": 1, "sets": 1, "withdrawn": 0, "files": 1}
    answer = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()
    assert answer["match"] == "different"
    assert answer["declared"]["source"] == "url" and answer["declared"]["url"] == FILE_URL
    assert f"in the declaration file its registry entry points at ({FILE_URL})" in answer["note"]
    assert '("runner 1.4, macOS")' in answer["note"]
    page = client.get(f"/api/v1/servers/{t.id}").json()
    assert page["declarationFile"]["url"] == FILE_URL and page["declarationFile"]["lastOk"]
    assert "lastError" not in page["declarationFile"]


def test_the_file_is_read_again_between_crawls(filed):
    client, world, t, files, _ = filed
    crawler = world["services"].crawler
    # A runner release: the publisher adds a set and drops the old one, no new registry entry.
    files.files[FILE_URL] = [{"digest": RUNNER_2_DIGEST, "when": "runner 1.5, macOS"}]
    world["clock"].advance(hours=1)
    result = crawler.refresh_declarations()
    assert result == {"files": 1, "unread": 0, "targetsWithNewSets": 1, "withdrawn": 1}
    new = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER_2}).json()
    assert new["declared"]["toolSetDigest"] == RUNNER_2_DIGEST
    old = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()
    assert "declared" not in old

    # The site is down: nothing the file declared is withdrawn, and the page says the read failed.
    files.files[FILE_URL] = DeclarationError("unreachable: ConnectTimeout")
    world["clock"].advance(hours=1)
    assert crawler.refresh_declarations()["unread"] == 1
    still = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER_2}).json()
    assert still["declared"]["toolSetDigest"] == RUNNER_2_DIGEST
    page = client.get(f"/api/v1/servers/{t.id}").json()
    assert page["declarationFile"]["lastError"] == "unreachable: ConnectTimeout" and page["declarationFile"]["lastOk"]

    # The crawl reads it too, and keeps the file's sets through a failed read as well.
    world["clock"].advance(days=1)
    stats = crawler.run()
    assert stats["declared_tool_sets"]["unread"] == [{"server": "ai.auten/mcp", "url": FILE_URL,
                                                       "error": "unreachable: ConnectTimeout"}]
    assert client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER_2}).json()["declared"]


def test_the_registry_and_the_file_together(filed):
    client, world, t, files, _ = filed
    crawler = world["services"].crawler
    # The entry names the old runner itself and still points at the file.
    world["targets"][:] = [dataclasses.replace(t, declared=({"digest": RUNNER_DIGEST, "when": "in the entry"},))]
    files.files[FILE_URL] = [{"digest": RUNNER_DIGEST, "when": "in the file"}, {"digest": RUNNER_2_DIGEST}]
    world["clock"].advance(days=1)
    crawler.run()
    both = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()["declared"]
    assert both["source"] == "registry" and both["when"] == "in the entry", "a digest both name is the registry's"
    # An hourly read never touches what the entry itself declares.
    files.files[FILE_URL] = [{"digest": RUNNER_2_DIGEST}]
    crawler.refresh_declarations()
    assert client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()["declared"]["source"] == "registry"

    # The entry stops pointing at a file: what only the file declared goes with it.
    world["targets"][:] = [dataclasses.replace(t, declared=({"digest": RUNNER_DIGEST},), declared_url=None)]
    world["clock"].advance(days=1)
    stats = crawler.run()
    assert stats["declared_tool_sets"]["withdrawn"] == 1
    assert "declared" not in client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER_2}).json()
    assert client.get(f"/api/v1/servers/{t.id}").json()["declarationFile"] is None
    assert crawler.refresh_declarations() == {"files": 0, "unread": 0, "targetsWithNewSets": 0, "withdrawn": 0}


def test_an_explicit_empty_file_withdraws_its_sets(filed):
    client, world, t, files, _ = filed
    files.files[FILE_URL] = []
    world["clock"].advance(hours=1)
    assert world["services"].crawler.refresh_declarations()["withdrawn"] == 1
    assert "declared" not in client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": RUNNER}).json()
