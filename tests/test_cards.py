"""MCP server cards as declared tool sets (histor.cards), on SQLite and (when set) Postgres."""

from __future__ import annotations

import json

import httpx

from histor import cards
from histor.robots import OK, Fetched, Policy
from tests.conftest import make_target, tool

FULL = [tool("get_weather", "Return the weather.", city="string"), tool("list_cities", "List cities.")]


def card(tools=FULL, **extra):
    return {"serverInfo": {"name": "x", "version": "1"}, "tools": tools, **extra}


def ok(doc):
    return cards.Read("ok", doc=doc)


# --- which card belongs to which endpoint -----------------------------------------------------------------

def test_a_card_at_the_endpoints_own_path_declares_its_tools():
    v = cards.judge("https://h.example/mcp", {"https://h.example/mcp/server-card": ok(card())}, hosts_endpoints=3)
    assert v.status == cards.DECLARED and v.url == "https://h.example/mcp/server-card" and len(v.tools) == 2


def test_a_host_wide_card_naming_no_endpoint_counts_only_on_a_host_with_one():
    reads = {"https://h.example/.well-known/mcp/server-card.json": ok(card())}
    assert cards.judge("https://h.example/mcp", reads, hosts_endpoints=1).status == cards.DECLARED
    assert cards.judge("https://h.example/mcp", reads, hosts_endpoints=2).status == cards.AMBIGUOUS


def test_a_card_naming_endpoints_describes_those_and_no_other():
    named = {"https://h.example/.well-known/mcp/server-card.json": ok(card(transport={"type": "streamable-http", "endpoint": "/mcp"}))}
    assert cards.judge("https://h.example/mcp/", named, hosts_endpoints=5).status == cards.DECLARED   # relative, trailing slash
    assert cards.judge("https://h.example/other", named, hosts_endpoints=5).status == cards.OTHER_ENDPOINT
    remotes = {"https://h.example/.well-known/mcp/server-card.json": ok(card(remotes=[{"type": "sse", "url": "https://h.example/sse"}]))}
    assert cards.judge("https://h.example/sse", remotes, hosts_endpoints=2).status == cards.DECLARED


def test_mcp_json_is_the_third_place_a_card_is_looked_for():
    assert cards.candidates("https://h.example/api/mcp") == [
        "https://h.example/api/mcp/server-card", "https://h.example/.well-known/mcp/server-card.json",
        "https://h.example/.well-known/mcp.json"]
    mcp_json = "https://h.example/.well-known/mcp.json"
    assert cards.judge("https://h.example/mcp", {mcp_json: ok(card())}, hosts_endpoints=1).url == mcp_json
    assert cards.judge("https://h.example/mcp", {mcp_json: ok(card())}, hosts_endpoints=2).status == cards.AMBIGUOUS
    # a full list in mcp.json counts although the standard card has none
    reads = {"https://h.example/.well-known/mcp/server-card.json": ok(card(tools=None)), mcp_json: ok(card())}
    v = cards.judge("https://h.example/mcp", reads, hosts_endpoints=1)
    assert v.status == cards.DECLARED and v.url == mcp_json


def test_the_ways_mcp_json_names_its_endpoint():
    url = "https://h.example/.well-known/mcp.json"
    shapes = [{"mcpUrl": "https://h.example/mcp"}, {"remote": {"url": "https://h.example/mcp"}},
              {"endpoints": {"streamable_http": "https://h.example/mcp"}},
              {"servers": [{"name": "x", "endpoint": "https://h.example/mcp"}]},
              {"aliases": ["https://h.example/mcp"], "transport": {"url": "https://h.example/v2/mcp"}},
              {"transport": {"endpoint": "/mcp"}}]
    for shape in shapes:
        assert "https://h.example/mcp" in cards.named_endpoints(shape, url), shape
        assert cards.judge("https://h.example/mcp", {url: ok(card(**shape))}, hosts_endpoints=4).status == cards.DECLARED, shape
    assert cards.judge("https://h.example/other", {url: ok(card(mcpUrl="/mcp"))}, 4).status == cards.OTHER_ENDPOINT


def test_only_a_complete_tool_list_is_a_declaration():
    no_schema = [{"name": "a", "description": "no schema"}]
    for doc in (card(tools=no_schema), card(tools=[]), card(tools="dynamic"), {"serverInfo": {"name": "x"}}):
        v = cards.judge("https://h.example/mcp", {"https://h.example/mcp/server-card": ok(doc)}, 1)
        assert v.status == cards.NO_TOOLS, doc


def test_no_card_robots_and_errors_are_told_apart():
    ep = "https://h.example/mcp"
    urls = cards.candidates(ep)
    assert cards.judge(ep, {u: cards.Read("absent", detail="http-404") for u in urls}, 1).status == cards.ABSENT
    assert cards.judge(ep, {u: cards.Read("robots") for u in urls}, 1).status == cards.ROBOTS
    mixed = {urls[0]: cards.Read("absent", detail="http-404"), urls[1]: cards.Read("error", detail="unreachable: ConnectError")}
    assert cards.judge(ep, mixed, 1).status == cards.ERROR
    one_allowed = {u: cards.Read("robots") for u in urls[:-1]} | {urls[-1]: cards.Read("absent", detail="http-404")}
    assert cards.judge(ep, one_allowed, 1).status == cards.ABSENT


# --- reading ------------------------------------------------------------------------------------------------

PUBLIC = "https://93.184.216.34"


def reader(handler, robots=None):
    return cards.CardReader(transport=httpx.MockTransport(handler), robots=robots)


def test_a_path_robots_txt_disallows_is_not_fetched():
    asked = []
    policy = Policy(fetch=lambda origin: Fetched(OK, 200, "User-agent: *\nDisallow: /mcp\nAllow: /mcp/server-card\n"))
    r = reader(lambda req: asked.append(req.url.path) or httpx.Response(200, json=card()), robots=policy)
    assert r.read(f"{PUBLIC}/mcp/server-card").status == "ok"
    assert r.read(f"{PUBLIC}/mcp/other").status == "robots" and asked == ["/mcp/server-card"]


def test_read_results():
    assert reader(lambda r: httpx.Response(404)).read(f"{PUBLIC}/x").status == "absent"
    assert reader(lambda r: httpx.Response(200, text="<html>spa</html>")).read(f"{PUBLIC}/x").status == "absent"
    moved = reader(lambda r: httpx.Response(302, headers={"location": "/y"})).read(f"{PUBLIC}/x")
    assert moved.status == "error" and "redirects are not followed" in moved.detail
    assert reader(lambda r: httpx.Response(503)).read(f"{PUBLIC}/x").status == "error"
    big = reader(lambda r: httpx.Response(200, content=b"[" + b"1," * (600 * 1024) + b"1]")).read(f"{PUBLIC}/x")
    assert big.status == "error" and "KiB" in big.detail
    assert reader(lambda r: httpx.Response(200, json=card())).read(f"{PUBLIC}/x").doc["tools"][0]["name"] == "get_weather"
    assert cards.CardReader().read("https://10.0.0.5/x").status == "error"           # private address: refused


# --- the pass, recorded --------------------------------------------------------------------------------

class Cards:
    def __init__(self):
        self.answers: dict[str, httpx.Response] = {}
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        return self.answers.get(request.url.path) or httpx.Response(404)


def wire(world) -> Cards:
    site = Cards()
    world["services"].crawler.cards = cards.CardReader(transport=httpx.MockTransport(lambda r: site(r)), allow_private=True)
    return site


def test_a_card_becomes_a_declared_set_shown_beside_what_is_served(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    site = wire(world)
    mcp.tools["/a"] = FULL
    mcp.status["/b"] = 401                                   # read only with a key
    world["targets"] += [make_target("io.example/a", "/a"), make_target("io.example/b", "/b")]
    s.crawler.run()
    site.answers["/a/server-card"] = httpx.Response(200, json=card())
    site.answers["/b/server-card"] = httpx.Response(200, json=card(tools=[tool("secret_tool", "Only with a key.")]))

    result = s.crawler.refresh_cards()
    assert result["checked"] == 2 and result["declared"] == 2

    by_name = {t["name"]: t for t in s.store.all_targets()}
    a, b = by_name["io.example/a"], by_name["io.example/b"]
    [da] = s.store.declared_toolsets(a["id"])
    assert da["source"] == "card" and da["toolset"] == a["current_toolset"]           # the card equals what is served
    assert da["url"] == "https://127.0.0.1/a/server-card"
    page = s.store.server_card(b["id"])
    assert page["status"] == "declared" and page["toolNames"] == ["secret_tool"] and b["current_toolset"] is None

    answer = s.checker.check({"endpoint": "https://127.0.0.1/b", "tools": [tool("secret_tool", "Only with a key.")]},
                             allow_scan=False)
    assert answer["declared"]["source"] == "card" and "in its server card (https://127.0.0.1/b/server-card)" in answer["note"]
    assert answer["target"]["serverCard"]["toolCount"] == 1

    clock.advance(hours=2)
    assert s.crawler.refresh_cards()["checked"] == 0                  # a day apart
    site.answers["/b/server-card"] = httpx.Response(503)
    clock.advance(hours=23)
    s.crawler.refresh_cards()
    assert [d["withdrawn_at"] for d in s.store.declared_toolsets(b["id"])] == [None]     # a failed read withdraws nothing
    site.answers.pop("/b/server-card")
    clock.advance(hours=25)
    s.crawler.refresh_cards()
    assert s.store.declared_toolsets(b["id"])[0]["withdrawn_at"] is not None          # the card is gone


def test_a_registry_sync_leaves_card_sets_alone(world):
    s, mcp = world["services"], world["mcp"]
    site = wire(world)
    mcp.tools["/a"] = FULL
    world["targets"].append(make_target("io.example/a", "/a"))
    s.crawler.run()
    site.answers["/a/server-card"] = httpx.Response(200, json=card(tools=[tool("other", "x")]))
    s.crawler.refresh_cards()
    world["clock"].advance(hours=1)
    s.crawler.run()                                          # the harvest syncs registry declarations
    t = s.store.all_targets()[0]
    [d] = s.store.declared_toolsets(t["id"])
    assert d["source"] == "card" and d["withdrawn_at"] is None


def test_a_host_without_a_card_is_asked_again_after_a_week(world):
    s, clock = world["services"], world["clock"]
    site = wire(world)
    world["mcp"].tools["/a"] = FULL
    world["targets"].append(make_target("io.example/a", "/a"))
    s.crawler.run()
    assert s.crawler.refresh_cards()["statuses"] == {"absent": 1}
    asked = len(site.asked)
    clock.advance(days=6)
    assert s.crawler.refresh_cards()["checked"] == 0
    clock.advance(days=1, hours=1)
    assert s.crawler.refresh_cards()["checked"] == 1 and len(site.asked) > asked


def test_a_card_found_only_in_mcp_json_is_read_and_declared(world):
    s = world["services"]
    site = wire(world)
    world["mcp"].status["/a"] = 401
    world["targets"].append(make_target("io.example/a", "/a"))
    s.crawler.run()
    site.answers["/.well-known/mcp.json"] = httpx.Response(200, json=card(mcpUrl="https://127.0.0.1/a"))
    assert s.crawler.refresh_cards()["statuses"] == {"declared": 1}
    assert "/.well-known/mcp.json" in site.asked
    t = s.store.all_targets()[0]
    assert s.store.server_card(t["id"])["url"] == "https://127.0.0.1/.well-known/mcp.json"


def test_migration_021_makes_earlier_absent_and_robots_reads_due(world):
    from histor.migrations import MIGRATION_021
    s, clock = world["services"], world["clock"]
    wire(world)
    world["mcp"].tools["/a"] = FULL
    world["targets"].append(make_target("io.example/a", "/a"))
    s.crawler.run()
    assert s.crawler.refresh_cards()["statuses"] == {"absent": 1}
    clock.advance(hours=1)
    assert s.crawler.refresh_cards()["checked"] == 0             # absent: a week before the next read
    with s.store.db.transaction() as tx:
        for statement in MIGRATION_021:
            tx.execute(statement)
    assert s.crawler.refresh_cards()["checked"] == 1


def test_the_server_page_carries_the_card(world):
    s, mcp = world["services"], world["mcp"]
    site = wire(world)
    mcp.tools["/a"] = FULL
    world["targets"].append(make_target("io.example/a", "/a"))
    s.crawler.run()
    site.answers["/a/server-card"] = httpx.Response(200, json=card(tools=[FULL[0]]))
    s.crawler.refresh_cards()
    from fastapi.testclient import TestClient

    from histor.app import create_app
    t = s.store.all_targets()[0]
    with TestClient(create_app(s)) as client:
        page = json.loads(client.get(f"/api/v1/servers/{t['id']}").text)
    assert page["serverCard"]["toolNames"] == ["get_weather"] and page["serverCard"]["status"] == "declared"
    assert [d["source"] for d in page["declaredToolSets"]] == ["card"]
    assert {x["name"] for x in page["tools"]} == {"get_weather", "list_cities"}
