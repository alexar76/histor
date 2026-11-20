"""A publisher's signed statement about its tool set (histor.signed_declaration): found through the
pointer in its registry listing, checked as the format's specification says a listing is checked,
recorded as the publisher's word with source 'signed' and the detail of the check."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import socket
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from histor import addon_declaration as ad
from histor.app import create_app
from histor.declared import FIELD, PUBLISHER_PROVIDED, DeclarationError, DeclarationFetcher
from histor.registry import targets_from_servers
from histor.signed_declaration import POINTER, check, hosts_for, pointer, read
from histor.subject import tool_set_digest
from tests.conftest import make_target, tool

NOW = datetime(2026, 11, 19, 19, 0, tzinfo=UTC)

# -- the first published statements and their listing, as served on 2026-11-19 (public) ---------------

LIVE_NAME = "com.rtlpdf/arabic-pdf"
LIVE_KEY = b"v=MCPv1; k=ed25519; p=7Tn1YNmgwvAXD76Eccw8rAT9Pdv+VFE937rNTAxQK54=\n"
LIVE_KEY_REF = {"method": "http", "domain": "rtlpdf.com", "alg": "ed25519",
                "id": "sha256:57b47ffa643b43cc1dedebd1d2438bf5202551175eb289a39fb63e78e808e29c"}
LIVE_REMOTES = [{"type": "streamable-http", "url": "https://rtlpdf.com/mcp"}]
LIVE_FIRST_HASH = "sha256:9170020c77a92f039806c11f714d1aa6eb50780bdb90c27e52016958045baca6"
LIVE_LATEST_HASH = "sha256:919ed174f2c34c1ae788b5c05e56804a273ecc26fe80d6fc11375172ab3cd069"
LIVE_FIRST = {
    "spec": "addon-declaration/0.1", "name": LIVE_NAME, "version": "1.0.0",
    "toolsHash": "sha256:317fad35d11e84f3d5a5c36f308eac5f4ba9329eb803916c707c8bdd4770d833",
    "remotes": LIVE_REMOTES, "contact": "mailto:support@rtlpdf.com",
    "issuedAt": "2026-11-19T00:08:46Z", "expiresAt": "2027-05-18T00:08:46Z", "key": LIVE_KEY_REF,
    "signature": "yjqNudcwRo4PsBFypPzylY+3hQncGF8HhEBPfbMsAvyc+X/OAo2NVYiE68+HBBoM9Wvo/fjGBfsoaBx/M6v/Dw==",
}
LIVE_CONTRACT = "sha256-VPxDXBxVgGzH/0OtZBO1iGClgcDKQSgXmvDHsxe3YTU="
LIVE_EXTRAS = "sha256-+q4/BoqWmGGjGJw7VgeYkxe99dw6scmRsiGkGhwP8n8="
LIVE_LATEST = {
    "spec": "addon-declaration/0.2", "name": LIVE_NAME, "version": "1.0.0",
    "toolSetDigest": {"spec": "mcp-tool-set-digest/1", "contract": LIVE_CONTRACT, "extras": LIVE_EXTRAS},
    "remotes": LIVE_REMOTES, "contact": "mailto:support@rtlpdf.com",
    "issuedAt": "2026-11-19T03:14:55Z", "expiresAt": "2027-05-18T03:14:55Z", "previous": LIVE_FIRST_HASH,
    "key": LIVE_KEY_REF,
    "signature": "rEUFxobukSfFV22oRrbbYTe4piIK/ceG07mQOnPLUnDEedAg/Z7W+uIjJptZNH7An+6nzd/wF4BDG7rgcQ2cAw==",
}
LIVE_DIR = f"https://rtlpdf.com/.well-known/addon-declaration/{LIVE_NAME}/"
LIVE_URL = f"{LIVE_DIR}by-hash/{LIVE_FIRST_HASH[7:]}.json"
LIVE_ENTRY = {"name": LIVE_NAME, "version": "1.0.0", "remotes": LIVE_REMOTES,
              "_meta": {PUBLISHER_PROVIDED: {POINTER: {"hash": LIVE_FIRST_HASH, "url": LIVE_URL}}}}


class Site:
    """A publisher's web server: url → bytes or an error text. A missing url answers 404."""

    def __init__(self, files: dict[str, bytes | str]):
        self.files = dict(files)
        self.reads: list[str] = []

    def get(self, url: str, *, limit: int = 65536, accept: str = "application/json") -> bytes:
        self.reads.append(url)
        content = self.files.get(url, "http-404")
        if isinstance(content, str):
            raise DeclarationError(content)
        if len(content) > limit:
            raise DeclarationError(f"the file is larger than {limit // 1024} KiB")
        return content


def dump(doc: dict) -> bytes:
    return json.dumps(doc).encode()


def test_the_first_published_listing_checks_as_its_own_tool_reports_it():
    listing, ignored = pointer(LIVE_ENTRY)
    assert ignored == [] and listing == {"url": LIVE_URL, "hash": LIVE_FIRST_HASH, "name": LIVE_NAME,
                                         "version": "1.0.0", "remotes": LIVE_REMOTES}
    site = Site({LIVE_URL: dump(LIVE_FIRST), LIVE_DIR + "versions/1.0.0.json": dump(LIVE_LATEST),
                 LIVE_DIR + "latest.json": dump(LIVE_LATEST),
                 "https://rtlpdf.com/.well-known/mcp-registry-auth": LIVE_KEY})
    outcome = check(site.get, listing, NOW)
    # The reference command gives the same state for this listing, in force from versions/1.0.0.json.
    assert outcome.state == "verified_newer_declaration", outcome.reasons
    assert outcome.named == {"hash": LIVE_FIRST_HASH, "format": "addon-declaration/0.1", "state": "valid"}
    assert outcome.in_force["source"] == "versions/1.0.0.json" and outcome.in_force["hash"] == LIVE_LATEST_HASH
    assert outcome.in_force["toolSetDigest"] == {"contract": LIVE_CONTRACT, "extras": LIVE_EXTRAS}
    assert outcome.in_force["key"] == LIVE_KEY_REF and outcome.in_force["expiresAt"] == "2027-05-18T03:14:55Z"
    assert [(n["path"], n["result"]) for n in outcome.newer] == [("versions/1.0.0.json", "in_force"), ("latest.json", "in_force")]
    assert outcome.newer[1]["sameAs"] == "versions/1.0.0.json"
    assert site.reads == [LIVE_URL, "https://rtlpdf.com/.well-known/mcp-registry-auth",
                          LIVE_DIR + "versions/1.0.0.json", LIVE_DIR + "latest.json"]
    sets, _notes, detail = read(site.get, listing, NOW, "2026-11-19T19:00:00Z")
    assert sets == [{"digest": LIVE_CONTRACT, "source": "signed"}]
    assert detail["state"] == "verified_newer_declaration" and detail["checkedAt"] == "2026-11-19T19:00:00Z"


# -- statements made here, to try what a real publisher has not done --------------------------------

NAME = "ai.example/pdf"
DIR = f"https://example.ai/.well-known/addon-declaration/{NAME}/"
KEY_URL = "https://example.ai/.well-known/mcp-registry-auth"
REMOTES = [{"type": "streamable-http", "url": "https://mcp.example.ai/mcp"}]
TOOLS = [tool("render", "Render a PDF.")]
CONTRACT = tool_set_digest(TOOLS)[0]
OTHER = tool_set_digest([*TOOLS, tool("merge", "Merge PDFs.")])[0]


class Publisher:
    def __init__(self, domain: str = "example.ai", method: str = "http"):
        self.private = Ed25519PrivateKey.generate()
        self.raw = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key = {"method": method, "domain": domain, "alg": "ed25519",
                    "id": "sha256:" + hashlib.sha256(self.raw).hexdigest()}

    @property
    def key_file(self) -> bytes:
        return f"v=MCPv1; k=ed25519; p={base64.b64encode(self.raw).decode()}\n".encode()

    def statement(self, *, contract: str | None = CONTRACT, previous: str | None = None, name: str = NAME,
                  version: str = "1.0.0", issued: str = "2026-11-19T00:00:00Z", expires: str | None = "2027-05-18T00:00:00Z",
                  remotes: list | None = REMOTES, **extra) -> tuple[dict, str]:
        doc = {"name": name, "version": version, "issuedAt": issued, "key": self.key, **extra}
        if contract:
            doc |= {"spec": ad.FORMAT_02, "toolSetDigest": {"spec": ad.DIGEST_SPEC, "contract": contract, "extras": None}}
        else:
            doc |= {"spec": ad.FORMAT_01, "toolsHash": "sha256:" + "ab" * 32, "contact": "mailto:dev@example.ai"}
        if expires:
            doc["expiresAt"] = expires
        if remotes:
            doc["remotes"] = remotes
        if previous:
            doc["previous"] = previous
        doc["signature"] = base64.b64encode(self.private.sign(ad.signing_input(doc))).decode()
        return doc, ad.declaration_hash(doc)


def by_hash(digest: str) -> str:
    return f"{DIR}by-hash/{digest[7:]}.json"


def listing_for(digest: str, **changes) -> dict:
    return {"url": by_hash(digest), "hash": digest, "name": NAME, "version": "1.0.0", "remotes": REMOTES, **changes}


def site_for(pub: Publisher, doc: dict, digest: str, **more) -> Site:
    return Site({by_hash(digest): dump(doc), KEY_URL: pub.key_file, **more})


@pytest.fixture
def pub():
    return Publisher()


def test_the_pointer_is_read_under_the_fetch_rules():
    digest = "sha256:" + "a" * 64
    good = {"url": by_hash(digest), "hash": digest}

    def entry(block, name=NAME, **provided):
        return {"name": name, "version": "1.0.0", "remotes": REMOTES,
                "_meta": {PUBLISHER_PROVIDED: {POINTER: block, **provided}}}

    assert pointer(entry(good)) == (listing_for(digest), [])
    assert pointer({"name": NAME}) == (None, []) and pointer({"name": NAME, "_meta": {PUBLISHER_PROVIDED: {}}}) == (None, [])
    for block, state in (
        ("text", "pointer_malformed"),
        ({**good, "note": "x"}, "pointer_malformed"),
        ({**good, "hash": "sha256:" + "A" * 64}, "pointer_malformed"),
        ({**good, "url": f"https://elsewhere.com/.well-known/addon-declaration/{NAME}/by-hash/{'a' * 64}.json"}, "pointer_refused"),
        ({**good, "url": f"https://cdn.example.ai/.well-known/addon-declaration/{NAME}/by-hash/{'a' * 64}.json"}, "pointer_refused"),
        ({**good, "url": by_hash("sha256:" + "b" * 64)}, "pointer_refused"),
        ({**good, "url": good["url"] + "?v=2"}, "pointer_refused"),
        ({**good, "url": DIR + "latest.json"}, "pointer_refused"),
        ({**good, "url": good["url"].replace("https://", "http://")}, "pointer_refused"),
    ):
        listing, ignored = pointer(entry(block))
        assert listing is None and ignored[0].startswith(state + ":"), (block, ignored)
    # A GitHub namespace is out of scope; a sub namespace may keep its file at a parent domain.
    assert pointer(entry(good, name="io.github.someone/pdf"))[1][0].startswith("namespace_out_of_scope:")
    assert hosts_for("com.example.sub/x") == ["sub.example.com", "example.com"] and hosts_for("com.rtlpdf/x") == ["rtlpdf.com"]

    # Through the registry reader: the pointer travels with every target of the server; a declaration
    # file the entry names itself is the one read.
    [target] = targets_from_servers([{"server": entry(good), "_meta": {}}])
    assert target.declared_url == good["url"] and target.declared_pointer == listing_for(digest)
    filed = entry(good, **{FIELD: {"mtl": "1", "url": "https://example.ai/sets.json"}})
    [target] = targets_from_servers([{"server": filed, "_meta": {}}])
    assert target.declared_url == "https://example.ai/sets.json" and target.declared_pointer is None
    assert "addonDeclaration not read" in target.declared_ignored[0]


def test_each_way_a_listing_check_ends(pub):
    doc, digest = pub.statement()
    listing = listing_for(digest)

    def state(site: Site, ctx: dict = listing) -> tuple[str, str]:
        outcome = check(site.get, ctx, NOW)
        return outcome.state, "; ".join(outcome.reasons)

    assert state(site_for(pub, doc, digest))[0] == "verified"
    assert state(Site({KEY_URL: pub.key_file}))[0] == "declaration_unreachable"
    assert state(Site({by_hash(digest): "unreachable: ConnectTimeout"}))[0] == "declaration_unreachable"
    assert state(Site({by_hash(digest): b" " * 20000}))[0] == "declaration_not_the_named_one"
    assert state(Site({by_hash(digest): b"<html>"}))[0] == "declaration_not_the_named_one"
    other, _ = pub.statement(contract=OTHER)
    assert state(site_for(pub, other, digest))[0] == "declaration_not_the_named_one", "another signed statement at that path"
    # Against the listing: version and remotes, both ways.
    assert state(site_for(pub, doc, digest), listing_for(digest, version="1.0.1"))[0] == "listing_mismatch"
    assert state(site_for(pub, doc, digest), listing_for(digest, remotes=[]))[0] == "listing_mismatch"
    more = [*REMOTES, {"type": "sse", "url": "https://mcp.example.ai/sse"}]
    assert state(site_for(pub, doc, digest), listing_for(digest, remotes=more))[0] == "listing_mismatch"
    # A listing may write a bare origin and an uppercase host: compared after serialization.
    bare, bare_hash = pub.statement(remotes=[{"type": "streamable-http", "url": "https://mcp.example.ai/"}])
    assert state(site_for(pub, bare, bare_hash),
                 listing_for(bare_hash, remotes=[{"type": "streamable-http", "url": "https://MCP.example.ai"}]))[0] == "verified"
    # The key.
    assert state(Site({by_hash(digest): dump(doc)}))[0] == "key_not_found", "a 404 is an answer without the key"
    assert state(site_for(pub, doc, digest, **{KEY_URL: Publisher().key_file}))[0] == "key_not_found"
    assert state(site_for(pub, doc, digest, **{KEY_URL: "http-503"}))[0] == "key_lookup_failed"
    elsewhere = Publisher(domain="elsewhere.com")
    foreign, foreign_hash = elsewhere.statement()
    assert state(Site({by_hash(foreign_hash): dump(foreign)}), listing_for(foreign_hash))[0] == "name_domain_mismatch"
    by_dns = Publisher(method="dns")
    dns_doc, dns_hash = by_dns.statement()
    result = state(Site({by_hash(dns_hash): dump(dns_doc)}), listing_for(dns_hash))
    assert result[0] == "key_lookup_failed" and "dns method" in result[1]
    # Signed by another holder of the same key id is impossible; a changed body is.
    forged = {**doc, "contact": "mailto:someone@example.ai"}
    forged_hash = ad.declaration_hash(forged)
    assert state(site_for(pub, forged, forged_hash), listing_for(forged_hash))[0] == "invalid_signature"
    # Format rules of the declaration itself.
    extra = {**doc, "comment": "x"}
    extra_hash = ad.declaration_hash(extra)
    assert state(site_for(pub, extra, extra_hash), listing_for(extra_hash))[0] == "malformed"
    # The clock.
    old, old_hash = pub.statement(issued="2026-05-01T00:00:00Z", expires="2026-11-01T00:00:00Z")
    assert state(site_for(pub, old, old_hash), listing_for(old_hash))[0] == "expired"
    early, early_hash = pub.statement(issued="2026-12-01T00:00:00Z")
    assert state(site_for(pub, early, early_hash), listing_for(early_hash))[0] == "not_yet_valid"


def test_a_newer_declaration_is_put_in_force_only_through_its_history(pub):
    first, h1 = pub.statement(contract=None)
    second, h2 = pub.statement(contract=OTHER, previous=h1, issued="2026-11-19T01:00:00Z")
    third, h3 = pub.statement(contract=CONTRACT, previous=h2, issued="2026-11-19T02:00:00Z")
    listing = listing_for(h1)

    def run(**files):
        return check(site_for(pub, first, h1, **files).get, listing, NOW)

    # Nothing newer: the named 0.1 statement verifies; it states nothing an observer can compare.
    alone = run()
    assert alone.state == "verified" and alone.in_force["toolsHash"] and "toolSetDigest" not in alone.in_force
    assert [n["result"] for n in alone.newer] == ["absent", "absent"]
    sets, notes, _ = read(site_for(pub, first, h1).get, listing, NOW, "t")
    assert sets == [] and "toolsHash" in notes[-1]

    one = run(**{DIR + "latest.json": dump(second)})
    assert one.state == "verified_newer_declaration" and one.in_force["toolSetDigest"]["contract"] == OTHER
    assert one.in_force["source"] == "latest.json" and one.newer[1]["links"] == 1
    # Two links back, the middle one fetched from its by-hash path.
    two = run(**{DIR + "latest.json": dump(third), by_hash(h2): dump(second)})
    assert two.state == "verified_newer_declaration" and two.in_force["hash"] == h3 and two.newer[1]["links"] == 2
    # versions/ holds the older reissue and latest.json the one that follows it: the later one is used.
    both = run(**{DIR + "versions/1.0.0.json": dump(second), DIR + "latest.json": dump(third), by_hash(h2): dump(second)})
    assert both.in_force["hash"] == h3

    def set_aside(doc: dict, **files) -> dict:
        outcome = run(**{DIR + "latest.json": dump(doc), **files})
        assert outcome.state == "verified" and outcome.in_force["hash"] == h1, "the named declaration stays in force"
        assert any("not put in force" in note for note in outcome.notes)
        return outcome.newer[1]

    stray, _ = pub.statement(contract=OTHER, previous="sha256:" + "0" * 64)
    assert set_aside(stray)["result"] == "not_chained"
    assert set_aside(third)["result"] == "not_chained", "the middle statement is not published"
    assert set_aside(third, **{by_hash(h2): dump(first)})["result"] == "not_chained", "a substituted earlier file"
    assert set_aside(pub.statement(contract=OTHER, previous=h1, version="2.0.0")[0])["result"] == "other_version"
    assert set_aside(pub.statement(contract=OTHER, previous=h1, issued="2026-05-01T00:00:00Z", expires="2026-06-01T00:00:00Z")[0]
                     )["result"] in ("expired", "not_chained")
    intruder = Publisher()
    assert set_aside(intruder.statement(contract=OTHER, previous=h1)[0])["result"] == "invalid", "a key the domain does not publish"
    # Both keys published (a rotation): the link verifies, and a change of key is still not accepted.
    rotated = set_aside(intruder.statement(contract=OTHER, previous=h1, issued="2026-11-19T01:00:00Z")[0],
                        **{KEY_URL: pub.key_file + intruder.key_file})
    assert rotated["result"] == "key_changed"
    # A newer statement that declares other remotes than the listing ends the check.
    moved, _ = pub.statement(contract=OTHER, previous=h1, issued="2026-11-19T01:00:00Z",
                             remotes=[{"type": "streamable-http", "url": "https://other.example.ai/mcp"}])
    outcome = run(**{DIR + "latest.json": dump(moved)})
    assert outcome.state == "listing_mismatch" and outcome.newer[1]["result"] == "remotes_differ"


def test_an_expired_named_statement_with_a_valid_newer_one(pub):
    old, h_old = pub.statement(issued="2026-05-01T00:00:00Z", expires="2026-11-01T00:00:00Z")
    renewed, _ = pub.statement(contract=OTHER, previous=h_old)
    site = site_for(pub, old, h_old, **{DIR + "versions/1.0.0.json": dump(renewed)})
    outcome = check(site.get, listing_for(h_old), NOW)
    assert outcome.state == "verified_newer_declaration" and outcome.named["state"] == "expired"
    assert "the declaration the listing names is expired" in outcome.notes


def test_the_file_reader_checks_a_listing_with_pinned_addresses(pub):
    doc, digest = pub.statement(remotes=[{"type": "streamable-http", "url": "https://example.ai/mcp"}])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path.rsplit("/", 1)[-1], request.headers["host"], request.url.host, request.headers["accept"]))
        if request.url.path == "/.well-known/mcp-registry-auth":
            return httpx.Response(200, content=pub.key_file)
        if request.url.path.endswith(f"/by-hash/{digest[7:]}.json"):
            return httpx.Response(200, json=doc)
        return httpx.Response(404)

    def dns(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    fetcher = DeclarationFetcher(transport=httpx.MockTransport(handler), resolver=dns, clock=lambda: NOW)
    listing = listing_for(digest, remotes=[{"type": "streamable-http", "url": "https://example.ai/mcp"}])
    sets, notes, detail = fetcher.fetch_signed(listing)
    assert sets == [{"digest": CONTRACT, "source": "signed"}] and notes == []
    assert detail["state"] == "verified" and detail["inForce"]["source"] == "pointer" and detail["checkedAt"] == "2026-11-19T19:00:00Z"
    assert [(name, accept) for name, _, _, accept in seen] == [
        (f"{digest[7:]}.json", "application/json"), ("mcp-registry-auth", "text/plain"),
        ("1.0.0.json", "application/json"), ("latest.json", "application/json")]
    assert {(host, address) for _, host, address, _ in seen} == {("example.ai", "93.184.216.34")}
    with pytest.raises(DeclarationError, match="listing_mismatch") as failed:
        fetcher.fetch_signed({**listing, "version": "9.9.9"})
    assert failed.value.detail["state"] == "listing_mismatch"


# -- through a crawl, the store and the API ----------------------------------------------------------


class SignedFiles:
    """Stands in for DeclarationFetcher: listing url → (sets, detail) or an error with its detail."""

    def __init__(self):
        self.files: dict[str, tuple[list[dict], dict] | Exception] = {}
        self.asked: list[dict] = []

    def fetch_signed(self, listing):
        self.asked.append(listing)
        content = self.files[listing["url"]]
        if isinstance(content, Exception):
            raise content
        return content[0], [], content[1]

    def fetch(self, url):
        raise AssertionError("a signed statement is not read as a declaration file")


def test_a_signed_set_is_recorded_with_its_check_and_compared_with_what_was_observed(world):
    digest = "sha256:" + "b" * 64
    listing = {"url": by_hash(digest), "hash": digest, "name": NAME, "version": "1.0.0", "remotes": REMOTES}
    statement = {"state": "verified", "checkedAt": "2026-11-19T19:00:00Z",
                 "named": {"hash": digest, "format": ad.FORMAT_02, "state": "valid"},
                 "inForce": {"source": "pointer", "hash": digest, "format": ad.FORMAT_02, "issuedAt": "2026-11-19T00:00:00Z",
                             "key": {"method": "http", "domain": "example.ai", "alg": "ed25519", "id": "sha256:" + "c" * 64},
                             "toolSetDigest": {"contract": CONTRACT, "extras": None}}}
    files = SignedFiles()
    files.files[listing["url"]] = ([{"digest": CONTRACT, "source": "signed"}], statement)
    world["services"].crawler.declarations = files
    world["mcp"].tools["/pdf"] = TOOLS
    t = dataclasses.replace(make_target(NAME, "/pdf"), declared_url=listing["url"], declared_pointer=listing)
    world["targets"].append(t)
    stats = world["services"].crawler.run()
    assert files.asked == [listing], "read once, with what the listing says"
    assert stats["declared_tool_sets"] == {"servers": 1, "sets": 1, "withdrawn": 0, "files": 1}
    with TestClient(create_app(world["services"])) as client:
        page = client.get(f"/api/v1/servers/{t.id}").json()
        assert [(d["toolSetDigest"], d["source"], d["url"]) for d in page["declaredToolSets"]] == [(CONTRACT, "signed", listing["url"])]
        filed = page["declarationFile"]
        assert filed["kind"] == "signed" and filed["pointerHash"] == digest and filed["lastOk"]
        assert filed["statement"]["inForce"]["key"]["domain"] == "example.ai"
        assert filed["agreesWithObserved"]["contract"] is True and filed["agreesWithObserved"]["extras"] is True

        # The hourly read fails: the signed set stays, and the page keeps the failed check.
        failure = DeclarationError("invalid_signature: the signature does not verify under the named key")
        failure.detail = {"state": "invalid_signature", "checkedAt": "2026-11-19T20:00:00Z"}
        files.files[listing["url"]] = failure
        world["clock"].advance(hours=1)
        assert world["services"].crawler.refresh_declarations() == {
            "files": 1, "unread": 1, "targetsWithNewSets": 0, "withdrawn": 0}
        assert files.asked[-1] == listing, "the hourly read uses the listing as the last crawl stored it"
        page = client.get(f"/api/v1/servers/{t.id}").json()
        assert page["declaredToolSets"][0]["source"] == "signed" and "withdrawn" not in page["declaredToolSets"][0]
        assert page["declarationFile"]["lastError"].startswith("invalid_signature")
        assert page["declarationFile"]["statement"]["state"] == "invalid_signature"

        # A newer statement in force names another set: the old one is withdrawn, and it now differs
        # from what HISTOR observes.
        newer = json.loads(json.dumps(statement))
        newer["state"] = "verified_newer_declaration"
        newer["inForce"]["toolSetDigest"]["contract"] = OTHER
        files.files[listing["url"]] = ([{"digest": OTHER, "source": "signed"}], newer)
        world["clock"].advance(hours=1)
        assert world["services"].crawler.refresh_declarations()["withdrawn"] == 1
        page = client.get(f"/api/v1/servers/{t.id}").json()
        assert page["declarationFile"]["agreesWithObserved"]["contract"] is False
        answer = client.post("/api/v1/check", json={"endpoint": t.endpoint, "toolSetDigest": OTHER}).json()
        assert answer["declared"]["source"] == "signed"
        assert f"in a statement signed with its registry key ({listing['url']})" in answer["note"]
