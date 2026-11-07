"""A 402 says why HISTOR did not read: a site charging crawlers per read, or a paid endpoint (histor.mcpclient)."""

from __future__ import annotations

from dataclasses import replace

import httpx

from histor.mcpclient import PAY_PER_CRAWL, PAYMENT_REQUIRED, USER_AGENT, McpReader
from histor.remote_schedule import RemoteSchedule, after
from tests.conftest import FakeMcp, make_target, tool

URL = "https://127.0.0.1/mcp"
CRAWL_BODY = {"error": "Pay per crawl. $0.01 a page.", "crawler": "crawler", "price": "USD 0.01",
              "how_to_pay": {"x402": "https://example.com/.well-known/x402"}}


def read(response: httpx.Response):
    mcp = FakeMcp()
    mcp.status["/mcp"] = response
    return McpReader(timeout=5, allow_private=True, transport=mcp.transport()).list_tools(URL), mcp


def test_a_402_says_what_it_asks_for():
    obs, _ = read(httpx.Response(402, json=CRAWL_BODY))
    assert obs.status == PAY_PER_CRAWL and "(USD 0.01)" in obs.detail and "does not pay" in obs.detail
    obs, _ = read(httpx.Response(402, headers={"crawler-price": "USD 0.002"}))      # Cloudflare pay per crawl
    assert obs.status == PAY_PER_CRAWL and "(USD 0.002)" in obs.detail
    obs, _ = read(httpx.Response(402, json={"x402Version": 1, "error": "X-PAYMENT header is required",
                                            "accepts": [{"scheme": "exact", "network": "base"}]}))
    assert obs.status == PAYMENT_REQUIRED
    obs, _ = read(httpx.Response(402, headers={"payment-required": "eyJ4NDAyVmVyc2lvbiI6Mn0="}))   # x402 v2
    assert obs.status == PAYMENT_REQUIRED
    obs, _ = read(httpx.Response(402, text="<html>nope</html>"))
    assert obs.status == "http-402" and obs.detail == ""
    obs, _ = read(httpx.Response(402, json={"error": {"code": "402", "message": "Payment required"}}))  # a host's billing
    assert obs.status == "http-402" and obs.detail == "the server says: Payment required"


def test_histor_names_itself_and_sends_no_payment():
    seen = []

    def site(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(402, json=CRAWL_BODY)

    obs = McpReader(timeout=5, allow_private=True, transport=httpx.MockTransport(site)).list_tools(URL)
    assert obs.status == PAY_PER_CRAWL and len(seen) == 1                 # one request, no retry under another name
    assert seen[0]["user-agent"] == USER_AGENT and "crawler" in USER_AGENT
    assert not any(h.lower() in seen[0] for h in ("x-payment", "payment-signature", "crawler-exact-price", "crawler-max-price"))


def test_a_site_charging_crawlers_keeps_its_history_and_says_why(world):
    s, mcp = world["services"], world["mcp"]
    mcp.tools["/mcp"] = [tool("t", "x")]
    world["targets"].append(make_target("io.example/s", "/mcp"))
    assert s.crawler.run()["statuses"] == {"ok": 1}
    mcp.status["/mcp"] = httpx.Response(402, json=CRAWL_BODY)
    world["clock"].advance(hours=7)
    assert s.crawler.run()["statuses"] == {PAY_PER_CRAWL: 1}
    t = s.store.all_targets()[0]
    assert t["first_pinned"] is not None and t["current_toolset"] is not None
    assert t["last_status"] == PAY_PER_CRAWL and "charges crawlers" in t["last_detail"]
    answer = s.checker.check({"endpoint": "https://127.0.0.1/mcp", "tools": [tool("t", "x")]}, allow_scan=False)
    assert "charges crawlers for each read" in answer["note"] and "after" in answer["note"]


def test_a_site_charging_crawlers_is_asked_again_a_day_later(world):
    s = world["services"]
    s.settings = s.crawler.settings = replace(s.settings, remote_interval_s=21600, crawl_min_gap_s=0)
    s.crawler.remote_schedule = queue = RemoteSchedule(s.store, s.settings)
    a = make_target("io.example/a", "/a")
    world["targets"].append(a)
    s.crawler.run(include_remote=False)
    now = world["clock"]()
    for status in (PAY_PER_CRAWL, PAYMENT_REQUIRED):
        queue.record(a.id, now, status)
        with s.store.db.read() as tx:
            row = tx.one("SELECT next_due FROM remote_schedule WHERE target_id=?", (a.id,))
        assert row["next_due"] >= after(now, 86400), status
