"""The cross-check against a peer's tool-change log (histor/peer_xcheck.py); no real network."""

from __future__ import annotations

import gzip
import hashlib
import json

import httpx
import pytest
from histor.db import open_backend
from histor.migrations import apply_migrations
from histor.store import Store

from histor import peer_xcheck as px

EP = "https://seller.example/mcp"


# --- robots.txt as RFC 9309 reads it ------------------------------------------------------------------------

@pytest.mark.parametrize("text, url, allowed", [
    (None, EP, True),
    ("User-agent: *\nDisallow: /\n", EP, False),
    # The longest rule wins, not the first one (Python's robotparser takes the first).
    ("User-Agent: *\nAllow: /\nDisallow: /api/\n", "https://openbait.example/api/mcp", False),
    # "$" anchors the end: the rule that let www.apier.no be read.
    ("User-agent: *\nAllow: /api/mcp$\nDisallow: /api/\n", "https://apier.example/api/mcp", True),
    ("User-agent: *\nAllow: /api/mcp$\nDisallow: /api/\n", "https://apier.example/api/mcp/x", False),
    ("User-agent: *\nDisallow: /m*p\n", EP, False),
    ("User-agent: *\nDisallow: /mcp\nAllow: /mcp\n", EP, True),  # a tie goes to Allow
    # The agent's own group replaces "*".
    ("User-agent: *\nDisallow: /\n\nUser-agent: ScraperWatch-MCP-Observatory\nAllow: /\n", EP, True),
    ("User-agent: GPTBot\nDisallow: /\n", EP, True),
])
def test_robots_follow_rfc_9309(text, url, allowed):
    assert px.robots_allows(text, url, px.PEER["agent"]) is allowed


def test_peer_read_times_start_from_its_first_run():
    (p0, p1), (c0, c1) = px.reads("2026-10-31")
    assert px.iso(p0) == "2026-10-30T10:49:43Z" and px.iso(p1) == "2026-10-30T10:59:39Z"
    assert px.iso(c0) == "2026-10-31T02:50:00Z" and px.iso(c1) == "2026-10-31T03:00:00Z"
    (p0, _), (c0, _) = px.reads("2026-11-07")
    assert px.iso(p0) == "2026-11-06T02:50:00Z" and px.iso(c0) == "2026-11-07T02:50:00Z"


# --- both directions on hand-made rows ----------------------------------------------------------------------

def tool(name, description="d", **extra):
    return {"name": name, "description": description, "inputSchema": {"type": "object", **extra}}


class Rows:
    """A tiny HISTOR: targets, ok reads and blobs, as the Data loader sees them."""

    def __init__(self):
        self.targets, self.obs, self.blobs, self.changes = [], [], {}, []

    def target(self, tid, endpoint=EP, name="x/y", delisted=0):
        self.targets.append({"id": tid, "name": name, "endpoint": endpoint, "transport": "streamable-http",
                             "delisted": delisted})

    def read(self, tid, at, tools, extras=None, protocol="2025-06-18"):
        d = "sha256-" + hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()
        self.blobs[d] = tools
        x = None
        if extras is not None:
            x = "sha256-x" + hashlib.sha256(json.dumps(extras, sort_keys=True).encode()).hexdigest()
            self.blobs[x] = {"tools": extras}
        self.obs.append({"target_id": tid, "observed_at": at, "status": "ok", "toolset": d, "extras": x,
                         "protocol": protocol})

    def change(self, tid, at, kind="tools"):
        self.changes.append({"target_id": tid, "observed_at": at, "kind": kind})

    def data(self):
        return px.Data(self.targets,
                       lambda ids, s, e: [o for o in self.obs if o["target_id"] in ids and s <= o["observed_at"] <= e],
                       lambda ids, s, e: [c for c in self.changes if c["target_id"] in ids and s < c["observed_at"] <= e],
                       lambda ds: {d: self.blobs[d] for d in ds if d in self.blobs})


def doc(*days):
    return {"days": [{"day": d, "changes": c} for d, c in days]}


def test_their_change_seen_by_histor_path_by_path():
    r = Rows()
    r.target("t1")
    r.read("t1", "2026-11-06T02:00:00Z", [tool("a", "old text", properties={"k": {"enum": ["b", "a"]}})])
    r.read("t1", "2026-11-06T09:00:00Z", [tool("a", "new text", properties={"k": {"enum": ["c", "b", "a"]}})])
    rec = {"endpoint": EP, "servers": ["x/y"], "op": "tools_changed", "added": [], "removed": [], "changed": [
        {"name": "a", "fields": {
            "description": [{"path": "$.description", "op": "changed", "old": "old text", "new": "new text"}],
            # The peer publishes lists sorted; HISTOR keeps the order served.
            "inputSchema": [{"path": "$.inputSchema.properties.k.enum", "op": "changed", "old": ["a", "b"],
                             "new": ["a", "b", "c"]}]}}]}
    res = px.compare(r.data(), doc(("2026-11-07", [rec])), [EP], ["2026-11-07"], lambda u: True)
    row = res["forward"]["rows"][0]
    assert row["verdict"] == "same change in HISTOR" and row["itemsSeenByHistor"] == 2


def test_absent_member_is_their_null_and_instructions_only_is_named():
    r = Rows()
    r.target("t1")
    r.read("t1", "2026-11-06T02:00:00Z", [tool("a")], extras={"a": {"title": "A"}})
    r.read("t1", "2026-11-06T09:00:00Z", [tool("a")], extras={})
    gone = {"endpoint": EP, "op": "tools_changed", "changed": [
        {"name": "a", "fields": {"title": [{"path": "$.title", "op": "changed", "old": "A", "new": None}]}}]}
    only_instructions = {"endpoint": EP, "op": "tools_changed", "changed": [], "instructions_changed": {"old": "x", "new": "y"}}
    res = px.compare(r.data(), doc(("2026-11-07", [gone, only_instructions])), [EP], ["2026-11-07"], lambda u: True)
    verdicts = [x["verdict"] for x in res["forward"]["rows"]]
    assert verdicts == ["same change in HISTOR", "server instructions only (HISTOR does not record them)"]


def reverse_case(*, before, after, extras_before=None, extras_after=None, robots=True, listed=None):
    r = Rows()
    r.target("t1")
    r.read("t1", "2026-11-06T02:40:00Z", before, extras=extras_before)
    r.read("t1", "2026-11-06T03:30:00Z", before, extras=extras_before)  # after their read: the order is known
    r.read("t1", "2026-11-06T12:00:00Z", after, extras=extras_after)
    r.read("t1", "2026-11-07T02:40:00Z", after, extras=extras_after)
    r.change("t1", "2026-11-06T12:00:00Z")
    res = px.compare(r.data(), doc(("2026-11-07", listed or [])), [EP], ["2026-11-07"], lambda u: robots)
    return res["reverse"]


def test_histor_change_they_should_have_listed_is_a_likely_miss():
    rev = reverse_case(before=[tool("a", "old")], after=[tool("a", "new")])
    assert rev["why"] == {"a change in the fields the peer hashes": 1} and len(rev["likelyMisses"]) == 1


def test_reads_hours_apart_around_theirs_leave_the_order_open():
    r = Rows()
    r.target("t1")
    r.read("t1", "2026-11-06T02:40:00Z", [tool("a", "old")])
    r.read("t1", "2026-11-06T08:40:00Z", [tool("a", "new")])
    r.read("t1", "2026-11-07T02:40:00Z", [tool("a", "new")])
    r.change("t1", "2026-11-06T08:40:00Z")
    rev = px.compare(r.data(), doc(("2026-11-06", []), ("2026-11-07", [])), [EP], ["2026-11-07"], lambda u: True)["reverse"]
    assert rev["why"] == {"a change in the fields the peer hashes, this day or the day before "
                          "(HISTOR's reads were hours apart)": 1} and len(rev["likelyMisses"]) == 1


def test_histor_change_they_cannot_see():
    assert reverse_case(before=[tool("a", "old")], after=[tool("a", "new")], robots=False)["why"] == \
        {"robots.txt disallows the endpoint": 1}
    assert reverse_case(before=[tool("a", "build 2026-11-05")], after=[tool("a", "build 2026-11-06")])["why"] == \
        {"only dates, version strings, numbers or whitespace changed": 1}
    rev = reverse_case(before=[tool("a")], after=[tool("a")], extras_before={"a": {"version": "7"}},
                       extras_after={"a": {"version": "8"}})
    assert rev["why"] == {"only members the peer does not hash changed": 1} and rev["membersNotHashed"] == {"version": 1}
    listed = [{"endpoint": EP, "op": "tools_changed", "changed": []}]
    assert reverse_case(before=[tool("a", "old")], after=[tool("a", "new")], listed=listed)["notInPeerLog"] == 0


def test_an_endpoint_with_a_delisted_twin_keeps_the_listed_entry_and_both_changes():
    r = Rows()
    r.target("old", delisted=1)
    r.target("new")
    data = r.data()
    assert data.target(EP)["id"] == "new" and data.ids_by_endpoint[EP] == ["new", "old"]


# --- the whole run on a real store, with the peer's files and robots.txt faked ----------------------------

class Response:
    def __init__(self, body: bytes, status: int = 200):
        self.content, self.status_code, self.text = body, status, body.decode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Client:
    def __init__(self, files):
        self.files, self.asked = files, []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return Response(*self.files[url]) if url in self.files else Response(b"", 404)


def test_run_reads_the_store_and_writes_the_report(settings, tmp_path, monkeypatch):
    backend = open_backend(settings.db_path, settings.database_url)
    apply_migrations(backend)
    store = Store(backend)
    old, new = [tool("a", "old")], [tool("a", "new")]
    with store.db.transaction() as tx:
        tx.execute("INSERT INTO targets(id, name, endpoint, transport, registry, first_listed, last_listed) "
                   "VALUES(?, ?, ?, ?, ?, ?, ?)", ("t1", "x/y", EP, "streamable-http", "r", "2026-11-01T00:00:00Z",
                                                   "2026-11-07T00:00:00Z"))
        for d, body in (("sha256-old", old), ("sha256-new", new)):
            tx.execute("INSERT INTO blobs(digest, kind, body) VALUES(?, ?, ?)", (d, "toolset", json.dumps(body)))
        for at, d in (("2026-11-06T02:40:00Z", "sha256-old"), ("2026-11-06T03:30:00Z", "sha256-old"),
                      ("2026-11-06T12:00:00Z", "sha256-new"),
                      ("2026-11-07T02:40:00Z", "sha256-new")):
            tx.execute("INSERT INTO observations(target_id, observed_at, status, toolset, protocol) VALUES(?, ?, ?, ?, ?)",
                       ("t1", at, "ok", d, "2025-06-18"))
        tx.execute("INSERT INTO changes(target_id, observed_at, from_toolset, to_toolset, summary) VALUES(?, ?, ?, ?, ?)",
                   ("t1", "2026-11-06T12:00:00Z", "sha256-old", "sha256-new", "{}"))
    from histor.netguard import PinnedTarget

    monkeypatch.setattr("histor.netguard.resolve_public", lambda url, **kw: PinnedTarget(
        "https://93.184.216.34/robots.txt", "seller.example", "seller.example", "93.184.216.34"))
    changes = json.dumps({"days": [{"day": "2026-11-07", "changes": []}]}).encode()
    client = Client({px.PEER["changes"]: (changes,), px.PEER["panel"]: (f"# panel\n{EP}\n".encode(),),
                     "https://93.184.216.34/robots.txt": (b"User-agent: *\nAllow: /\n",)})
    out = tmp_path / "report"
    try:
        result = px.run(store, out, days=7, http=client)
    finally:
        backend.close()
    assert result["reverse"]["why"] == {"a change in the fields the peer hashes": 1}
    assert result["panelInHistor"] == 1 and result["days"] == ["2026-11-07"]
    assert "seller.example/mcp" in (out / "README.md").read_text()
    assert json.loads(gzip.decompress((out / "inputs" / "changes.json.gz").read_bytes())) == json.loads(changes)
    sums = dict(line.split("  ")[::-1] for line in (out / "SHA256SUMS").read_text().splitlines())
    assert sums["results.json"] == hashlib.sha256((out / "results.json").read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        px.run(Store(open_backend(settings.db_path, settings.database_url)), out, http=client)


@pytest.mark.parametrize("redirect", [False, True])
def test_peer_robots_uses_checked_ip_and_never_follows_a_private_redirect(tmp_path, monkeypatch, redirect):
    from histor.netguard import PinnedTarget

    rows = Rows()
    rows.target("t1")
    rows.read("t1", "2026-11-06T02:40:00Z", [tool("a", "old")])
    rows.read("t1", "2026-11-06T03:30:00Z", [tool("a", "old")])
    rows.read("t1", "2026-11-06T12:00:00Z", [tool("a", "new")])
    rows.read("t1", "2026-11-07T02:40:00Z", [tool("a", "new")])
    rows.change("t1", "2026-11-06T12:00:00Z")
    monkeypatch.setattr(px, "from_store", lambda *args: rows.data())
    monkeypatch.setattr("histor.netguard.resolve_public", lambda *args, **kw: PinnedTarget(
        "https://93.184.216.34/robots.txt", "seller.example", "seller.example", "93.184.216.34"))
    asked = []

    def handle(request):
        asked.append(request)
        if str(request.url) == px.PEER["changes"]:
            return httpx.Response(200, json=doc(("2026-11-07", [])))
        if str(request.url) == px.PEER["panel"]:
            return httpx.Response(200, text=EP)
        if request.url.host == "127.0.0.1":
            return httpx.Response(200, text="private service content")
        if redirect:
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/admin"})
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    # The caller's client deliberately follows redirects: the untrusted robots request
    # must override that policy, rather than inherit access to a private second hop.
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        result = px.run(None, tmp_path / "report", http=client)
    outbound = [r for r in asked if str(r.url) not in (px.PEER["changes"], px.PEER["panel"])]
    assert [str(r.url) for r in outbound] == ["https://93.184.216.34/robots.txt"]
    assert outbound[0].headers["host"] == "seller.example"
    assert outbound[0].extensions["sni_hostname"] == "seller.example"
    if redirect:
        assert result["robotsRead"] == {"seller.example": None}


def test_a_server_histor_first_read_after_their_read_is_not_counted_as_missed():
    r = Rows()
    r.target("t1")
    r.read("t1", "2026-11-07T08:00:00Z", [tool("a", "new text")])
    rec = {"endpoint": EP, "op": "tools_changed", "changed": [
        {"name": "a", "fields": {"description": [{"path": "$.description", "op": "changed", "old": "old", "new": "new text"}]}}]}
    res = px.compare(r.data(), doc(("2026-11-07", [rec])), [EP], ["2026-11-07"], lambda u: True)
    assert res["forward"]["rows"][0]["verdict"] == "HISTOR has no definitions before their read"
