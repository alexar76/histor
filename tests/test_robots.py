"""robots.txt (RFC 9309): what HISTOR reads and what it leaves alone, on SQLite and (when set) Postgres."""

from __future__ import annotations

import httpx
import pytest

from histor.labels import CONTINUITY
from histor.mcpclient import McpReader
from histor.robots import OK, UNAVAILABLE, UNREACHABLE, Fetched, Fetcher, Policy, decide_path
from tests.conftest import make_target, tool

PUBLIC = "https://93.184.216.34"  # an IP literal: no DNS in tests, and public to the address checks


@pytest.mark.parametrize("text, url, allowed", [
    ("User-agent: *\nAllow: /\nDisallow: /api/\n", "https://h/api/mcp", False),   # longest match, not first
    ("User-agent: *\nDisallow: /mcp\n", "https://h/mcp", False),
    ("User-agent: *\nDisallow: /\nAllow: /v1/mcp\n", "https://h/v1/mcp", True),
    ("User-agent: histor\nAllow: /\nUser-agent: *\nDisallow: /\n", "https://h/mcp", True),
    ("User-agent: HISTOR/0.5\nDisallow: /mcp\nUser-agent: *\nAllow: /\n", "https://h/mcp", False),
    ("User-agent: *\nDisallow: /api/mcp$\n", "https://h/api/mcp/x", True),
    ("User-agent: *\nDisallow: /api/mcp$\n", "https://h/api/mcp", False),
    ("User-agent: *\nDisallow: /*/mcp$\n", "https://h/v2/mcp", False),
    ("User-agent: *\nDisallow: /foo\nAllow: /foo\n", "https://h/foo", True),        # Allow wins a tie
    ("Disallow: /\nUser-agent: other\nDisallow: /\n", "https://h/mcp", True),       # no group for us
    ("User-agent: *\nDisallow:\n", "https://h/mcp", True),                          # empty value: no rule
    ("User-agent: *\nDisallow: /\n", "https://h/robots.txt", True),                 # always allowed
    ("User-agent: *\nDisallow: /caf%c3%a9\n", "https://h/café/mcp", False),    # percent-encoding
    ("User-agent: a\nUser-agent: *\nDisallow: /x\n\nUser-agent: *\nDisallow: /mcp\n", "https://h/mcp", False),
    ("<html><body>a page, not a robots file</body></html>", "https://h/mcp", True),
    ("User-agent: *\nDisallow: /mcp?key\n", "https://h/mcp?key=1", False),          # the query counts
])
def test_rfc_9309_matching(text, url, allowed):
    assert decide_path(text, url)[0] is allowed


class Site:
    """A fake robots.txt per origin, counting fetches."""

    def __init__(self) -> None:
        self.answers: dict[str, Fetched] = {}
        self.fetches: list[str] = []

    def __call__(self, origin: str) -> Fetched:
        self.fetches.append(origin)
        return self.answers.get(origin, Fetched(UNAVAILABLE, 404))


def robots_world(world) -> tuple[Site, Policy]:
    site = Site()
    policy = Policy(fetch=site, clock=world["clock"])
    crawler = world["services"].crawler
    crawler.reader.robots = policy
    crawler.robots = policy
    return site, policy


ORIGIN = "https://127.0.0.1"  # make_target's endpoints


def test_a_disallowed_path_is_not_read_and_the_page_says_how_to_allow_it(world):
    s, mcp = world["services"], world["mcp"]
    site, _ = robots_world(world)
    site.answers[ORIGIN] = Fetched(OK, 200, "User-agent: *\nAllow: /\nDisallow: /private\n")
    mcp.tools["/private/mcp"] = [tool("t", "x")]
    mcp.tools["/open/mcp"] = [tool("t", "x")]
    world["targets"] += [make_target("io.example/private", "/private/mcp"), make_target("io.example/open", "/open/mcp")]

    stats = s.crawler.run()

    assert stats["statuses"] == {"robots-disallowed": 1, "ok": 1}
    assert not any(path.startswith("/private") for path, _ in mcp.calls)   # not one MCP request
    by_name = {t["name"]: t for t in s.store.all_targets()}
    blocked = by_name["io.example/private"]
    assert blocked["last_status"] == "robots-disallowed" and blocked["first_pinned"] is None
    assert "Disallow: /private" in blocked["last_detail"] and "User-agent: histor" in blocked["last_detail"]
    assert site.fetches == [ORIGIN]                                         # once per origin
    [row] = s.store.robots_rows()
    assert row["origin"] == ORIGIN and row["kind"] == OK and "Disallow: /private" in row["body"]


def test_a_group_naming_histor_overrides_the_star_group(world):
    s, mcp = world["services"], world["mcp"]
    site, _ = robots_world(world)
    site.answers[ORIGIN] = Fetched(OK, 200, "User-agent: *\nDisallow: /\n\nUser-agent: histor\nAllow: /mcp\n")
    mcp.tools["/mcp"] = [tool("t", "x")]
    world["targets"].append(make_target("io.example/allowed", "/mcp"))
    assert s.crawler.run()["statuses"] == {"ok": 1}


def test_history_stays_one_label_says_why_and_reading_resumes_when_allowed(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    site, _ = robots_world(world)
    mcp.tools["/mcp"] = [tool("t", "x")]
    world["targets"].append(make_target("io.example/s", "/mcp"))
    assert s.crawler.run()["statuses"] == {"ok": 1}          # no robots.txt (404): read
    pinned = s.store.all_targets()[0]["first_pinned"]

    site.answers[ORIGIN] = Fetched(OK, 200, "User-agent: *\nDisallow: /mcp\n")
    clock.advance(hours=23)
    assert s.crawler.run()["statuses"] == {"ok": 1}          # the 404 is reused for 24 hours
    clock.advance(hours=2)
    assert s.crawler.run()["statuses"] == {"robots-disallowed": 1}
    s.crawler.run()
    t = s.store.all_targets()[0]
    failures = [r for r in s.store.labels_for(t["id"]) if r["method"] == CONTINUITY and r["verdict"] != "pass"]
    assert t["first_pinned"] == pinned and len(failures) == 1      # history kept; one label, not one per read

    site.answers[ORIGIN] = Fetched(OK, 200, "User-agent: histor\nAllow: /mcp\n")
    clock.advance(hours=25)
    assert s.crawler.run()["statuses"] == {"ok": 1}


def test_an_unreachable_robots_txt_disallows_until_30_days_then_counts_as_absent(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    site, _ = robots_world(world)
    site.answers[ORIGIN] = Fetched(UNREACHABLE, 503)
    mcp.tools["/mcp"] = [tool("t", "x")]
    world["targets"].append(make_target("io.example/s", "/mcp"))

    assert s.crawler.run()["statuses"] == {"robots-unreachable": 1}
    t = s.store.all_targets()[0]
    assert "answered HTTP 503" in t["last_detail"] and "assume everything is disallowed" in t["last_detail"]
    clock.advance(days=29, hours=23)
    assert s.crawler.run()["statuses"] == {"robots-unreachable": 1}
    assert mcp.calls == []                                     # nothing sent for 30 days
    clock.advance(hours=2)
    assert s.crawler.run()["statuses"] == {"ok": 1}


def test_while_unreachable_the_last_file_read_still_applies(world):
    s, mcp, clock = world["services"], world["mcp"], world["clock"]
    site, _ = robots_world(world)
    site.answers[ORIGIN] = Fetched(OK, 200, "User-agent: *\nDisallow: /closed\n")
    mcp.tools["/open"] = [tool("t", "x")]
    mcp.tools["/closed"] = [tool("t", "x")]
    world["targets"] += [make_target("io.example/open", "/open"), make_target("io.example/closed", "/closed")]
    s.crawler.run()
    site.answers[ORIGIN] = Fetched(UNREACHABLE, None, note="timeout")
    clock.advance(hours=25)
    assert s.crawler.run()["statuses"] == {"ok": 1, "robots-disallowed": 1}
    closed = {t["name"]: t for t in s.store.all_targets()}["io.example/closed"]
    assert "has not answered since" in closed["last_detail"]


def test_robots_state_survives_a_restart(world):
    s, clock = world["services"], world["clock"]
    site, policy = robots_world(world)
    site.answers[ORIGIN] = Fetched(UNREACHABLE, 503)
    world["mcp"].tools["/mcp"] = [tool("t", "x")]
    world["targets"].append(make_target("io.example/s", "/mcp"))
    s.crawler.run()
    since = s.store.robots_rows()[0]["unreachable_since"]

    again = Policy(fetch=site, clock=clock)
    again.load(s.store.robots_rows())
    clock.advance(minutes=30)
    assert not again.decide(f"{ORIGIN}/mcp").allowed and site.fetches == [ORIGIN]  # no refetch within the hour
    clock.advance(hours=2)
    again.decide(f"{ORIGIN}/mcp")
    s.store.save_robots(again.take_dirty())
    assert s.store.robots_rows()[0]["unreachable_since"] == since    # the 30 days count from the first failure


# --- the MCP client never touches a disallowed endpoint ----------------------------------------------------

def test_the_reader_sends_nothing_to_a_disallowed_endpoint():
    sent = []

    def mcp(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(500)

    policy = Policy(fetch=lambda origin: Fetched(OK, 200, "User-agent: *\nDisallow: /\n"))
    obs = McpReader(timeout=2, transport=httpx.MockTransport(mcp), robots=policy).list_tools(f"{PUBLIC}/mcp")
    assert obs.status == "robots-disallowed" and sent == []


def test_a_private_address_stays_blocked_and_robots_txt_is_not_asked():
    asked = []
    policy = Policy(fetch=lambda origin: asked.append(origin) or Fetched(UNAVAILABLE, 404))
    obs = McpReader(timeout=2, robots=policy).list_tools("https://10.0.0.5/mcp")
    assert obs.status == "blocked-address" and asked == []


# --- fetching robots.txt ---------------------------------------------------------------------------------

def fetch_with(handler) -> Fetched:
    return Fetcher(user_agent="histor/test", transport=httpx.MockTransport(handler))(PUBLIC)


def test_fetch_results_follow_rfc_9309_section_2_3_1():
    assert fetch_with(lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /\n")).kind == OK
    for code in (401, 403, 404, 410, 429):
        assert fetch_with(lambda r, c=code: httpx.Response(c)).kind == UNAVAILABLE     # 4xx: no restrictions
    for code in (500, 503):
        assert fetch_with(lambda r, c=code: httpx.Response(c)).kind == UNREACHABLE     # 5xx: disallow all

    def down(request):
        raise httpx.ConnectError("refused")
    assert fetch_with(down).kind == UNREACHABLE


def test_fetch_follows_five_redirects_and_no_more():
    def chain(hops):
        def handler(request: httpx.Request) -> httpx.Response:
            n = int(request.url.params.get("n", "0"))
            if n < hops:
                return httpx.Response(301, headers={"location": f"/robots.txt?n={n + 1}"})
            return httpx.Response(200, text="User-agent: *\nDisallow: /x\n")
        return handler

    assert fetch_with(chain(5)).kind == OK
    assert fetch_with(chain(6)).kind == UNAVAILABLE       # more than five: MAY assume unavailable


def test_fetch_refuses_a_redirect_to_a_private_address_and_caps_the_size():
    redirected = fetch_with(lambda r: httpx.Response(302, headers={"location": "https://10.0.0.5/robots.txt"}))
    assert redirected.kind == UNREACHABLE and "not fetched" in redirected.note
    big = fetch_with(lambda r: httpx.Response(200, content=b"User-agent: *\n" + b"#" * (600 * 1024)))
    assert big.kind == OK and len(big.body.encode()) == 512 * 1024


def test_fetch_identifies_itself_and_keeps_the_host():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(ua=request.headers["user-agent"], host=request.headers["host"], path=request.url.path)
        return httpx.Response(404)

    fetch_with(handler)
    assert seen == {"ua": "histor/test", "host": "93.184.216.34", "path": "/robots.txt"}


def test_check_answers_say_a_robots_disallowed_server_is_not_read(world):
    s, mcp = world["services"], world["mcp"]
    site, _ = robots_world(world)
    mcp.tools["/mcp"] = [tool("t", "x")]
    world["targets"].append(make_target("io.example/s", "/mcp"))
    s.crawler.run()
    site.answers[ORIGIN] = Fetched(OK, 200, "User-agent: *\nDisallow: /mcp\n")
    world["clock"].advance(hours=25)
    s.crawler.run()
    answer = s.checker.check({"endpoint": f"{ORIGIN}/mcp", "tools": [tool("t", "x")]}, allow_scan=False)
    assert answer["target"]["lastStatus"] == "robots-disallowed"
    assert "robots.txt does not allow HISTOR" in answer["note"] and "after" in answer["note"]
