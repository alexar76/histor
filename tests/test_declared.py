"""Tool sets a publisher declares in its registry entry: read, recorded apart from observations,
answered by /check, withdrawn when the entry stops naming them."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from histor.app import create_app
from histor.check import CHECK_TYPE
from histor.declared import FIELD, MAX_SETS, PUBLISHER_PROVIDED, declared_tool_sets
from histor.logbook import verify_document
from histor.registry import targets_from_servers
from histor.subject import tool_set_digest
from tests.conftest import make_target, tool

LAUNCHER = [tool("install_auten", "The runner is not installed on this machine yet.")]
RUNNER = [tool("screenshot", "Take a screenshot."), tool("click", "Click at a point.")]
RUNNER_DIGEST = tool_set_digest(RUNNER)[0]


def server(block=None, **extra):
    srv = {"name": "ai.example/launcher", "version": "0.1.5", **extra}
    if block is not None:
        srv["_meta"] = {PUBLISHER_PROVIDED: {FIELD: block}}
    return srv


def test_a_declaration_is_read_and_checked_entry_by_entry():
    sets, ignored = declared_tool_sets(server({"mtl": "1", "sets": [
        {"digest": RUNNER_DIGEST, "count": 2, "when": "the runner​ is\n installed‮"},
        {"digest": "sha256-short="},
        {"digest": RUNNER_DIGEST},
        {"digest": "sha256-" + "B" * 43 + "=", "count": True},
        "not an object",
    ]}))
    assert sets == [{"digest": RUNNER_DIGEST, "count": 2, "when": "the runner is installed"}]
    assert [r.split(":")[0] for r in ignored] == ["sets[1]", "sets[2]", "sets[3]", "sets[4]"]

    assert declared_tool_sets(server()) == ([], [])
    assert declared_tool_sets({"name": "x", "_meta": {PUBLISHER_PROVIDED: {"other": 1}}}) == ([], [])
    assert declared_tool_sets(server({"mtl": "2", "sets": [{"digest": RUNNER_DIGEST}]}))[1][0].startswith('"mtl"')
    assert declared_tool_sets(server({"mtl": "1", "sets": []}))[1] == ['"sets" must be a non-empty list']
    assert declared_tool_sets(server(["not", "an", "object"]))[1] == [f"{FIELD} is not an object"]

    many = [{"digest": "sha256-" + chr(65 + i) * 43 + "="} for i in range(MAX_SETS + 2)]
    sets, ignored = declared_tool_sets(server({"mtl": "1", "sets": many}))
    assert len(sets) == MAX_SETS and ignored == [f"sets[{MAX_SETS}:]: more than {MAX_SETS} sets"]


def test_every_target_of_the_server_carries_the_declaration():
    targets = targets_from_servers([{"server": server(
        {"mtl": "1", "sets": [{"digest": RUNNER_DIGEST, "when": "the runner is installed"}]},
        remotes=[{"type": "streamable-http", "url": "https://launcher.example.com/mcp"}],
        packages=[{"registryType": "npm", "identifier": "@example/launcher", "transport": {"type": "stdio"}}],
    )}], observe_packages=True)
    assert {t.endpoint for t in targets} == {"https://launcher.example.com/mcp", "npm:@example/launcher"}
    assert all(t.declared == ({"digest": RUNNER_DIGEST, "when": "the runner is installed"},) for t in targets)


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
    assert answer["declared"]["toolSetDigest"] == RUNNER_DIGEST
    assert answer["declared"]["when"] == "the runner is installed" and answer["declared"]["toolCount"] == 2
    assert answer["declared"]["registryVersion"] == "1.0.0" and "withdrawn" not in answer["declared"]
    assert 'declares this set in its registry entry ("the runner is installed")' in answer["note"]
    assert verify_document(answer, world["services"].key.did, CHECK_TYPE), "the declared block is signed with the rest"

    observed = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": LAUNCHER}).json()
    assert observed["match"] == "same" and "declared" not in observed

    other = client.post("/api/v1/check", json={"endpoint": t.endpoint, "tools": [tool("x", "y")]}).json()
    assert other["match"] == "different" and "declared" not in other and "publisher" not in other["note"]

    page = client.get(f"/api/v1/servers/{t.id}").json()
    assert [d["toolSetDigest"] for d in page["declaredToolSets"]] == [RUNNER_DIGEST]
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
