"""A publisher's signed statement about its tool set, pointed at from its registry listing.

Another way a publisher states the digests of its tool set (histor.declared is the first). The
listing carries a pointer under the field the registry keeps for publishers::

    "_meta": {
      "io.modelcontextprotocol.registry/publisher-provided": {
        "addonDeclaration": {
          "url": "https://rtlpdf.com/.well-known/addon-declaration/com.rtlpdf/arabic-pdf/by-hash/9170….json",
          "hash": "sha256:9170…"
        }
      }
    }

The statement is an ``addon-declaration`` (histor.addon_declaration: the format, checked against the
format's own conformance vectors). This module is the check of a listing, section 8a of that
specification, in its order and with its states:

1. the pointer: an object with exactly ``url`` and ``hash``; an ``io.github`` namespace is out of scope;
2. the fetch rules: the url is the by-hash path for this name and hash, on the domain the namespace
   names (or a parent of it); nothing else is fetched;
3. the file is the one named: its declaration hash equals the pointer hash, compared before anything
   in it is used;
4. the rules of its format; then the listing: same name, same version, the same remotes both ways;
5. verification: the namespace rule, the key published at ``key.domain``, the signature, the clock;
6. newer declarations beside it (``versions/<version>.json``, ``latest.json``): put in force only when
   they are for this name and version, their ``previous`` history reaches the pointer hash within 8
   links, every declaration on the way verifies, and no link crosses a change of key.

A 0.2 statement in force carries the two digests of mcp-tool-set-digest/1. The contract digest is the
MTL/1 tool-set digest HISTOR records for an observation, so it is recorded as a set the publisher
declares, with ``source = 'signed'``; the whole result of the check is kept beside it.

What is not done. The tool check (section 11 step 6): HISTOR does not fetch a tool list for this, it
shows the stated digests next to the ones it recorded on its own schedule. Keys published by the
``dns`` method: HISTOR has no TXT lookup, so such a statement is reported as ``key_lookup_failed``.

Like every declaration it stays the publisher's word: never an observation, never a label.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from histor import addon_declaration as ad
from histor.declared import DeclarationError

POINTER = "addonDeclaration"
PUBLISHER_PROVIDED = "io.modelcontextprotocol.registry/publisher-provided"
SOURCE = "signed"
KEY_PATH = "/.well-known/mcp-registry-auth"
MAX_KEY_BYTES = 4096
MAX_LINKS = 8
PASSED = ("verified", "verified_newer_declaration")

Get = Callable[..., bytes]  # get(url, limit=..., accept=...) -> body; raises DeclarationError


def pointer(server: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """(what to check, why not) for a registry listing. The first is None when the listing names no
    declaration or names one HISTOR must not fetch; the second says which state that is and why.

    The context holds everything a later check needs from the listing: ``url``, ``hash``, ``name``,
    ``version`` and ``remotes`` as the listing writes them."""
    meta = server.get("_meta")
    if meta is None:
        return None, []
    if not isinstance(meta, dict):
        return None, ["pointer_malformed: _meta is not an object"]
    provided = meta.get(PUBLISHER_PROVIDED)
    if provided is None:
        return None, []
    if not isinstance(provided, dict):
        return None, ["pointer_malformed: the publisher metadata is not an object"]
    if POINTER not in provided:
        return None, []
    name = str(server.get("name") or "")
    namespace = name.split("/", 1)[0].lower()
    if namespace == "io.github" or namespace.startswith("io.github."):
        return None, [f"namespace_out_of_scope: {POINTER} is not read for an io.github namespace"]
    block = provided[POINTER]
    if not isinstance(block, dict) or set(block) != {"url", "hash"} or not isinstance(block["url"], str) \
            or not isinstance(block["hash"], str) or not ad.HASH.fullmatch(block["hash"]):
        return None, [f"pointer_malformed: {POINTER} must be an object with exactly url and hash (sha256:<64 hex digits>)"]
    refusal = url_refusal(block["url"], name, block["hash"])
    if refusal:
        return None, [f"pointer_refused: {refusal}"]
    remotes = [{"type": r.get("type"), "url": r.get("url")} for r in (server.get("remotes") or []) if isinstance(r, dict)]
    return {"url": block["url"], "hash": block["hash"], "name": name, "version": server.get("version"),
            "remotes": remotes}, []


def hosts_for(name: str) -> list[str]:
    """The hosts whose key could prove *name*'s namespace: ``reverse(namespace)`` and its parents with
    two or more labels. ``com.rtlpdf`` → rtlpdf.com; ``com.example.sub`` → sub.example.com, example.com."""
    labels = [x for x in name.split("/", 1)[0].lower().split(".") if x]
    hosts = [".".join(reversed(labels[:n])) for n in range(len(labels), 1, -1)]
    return [h for h in hosts if ad.domain_ok(h)]


def url_refusal(url: str, name: str, digest: str) -> str | None:
    """Why the pointer url must not be fetched (section 8a, fetch rules), or None."""
    if not ad.https_url(url):
        return "the url is not an https URL in canonical form"
    for host in hosts_for(name):
        if url == f"https://{host}/.well-known/addon-declaration/{name}/by-hash/{digest[7:]}.json":
            return None
    allowed = ", ".join(hosts_for(name)) or "no host"
    return f"the url is not the by-hash path for this name and hash on {allowed}"


def _serialized(url: Any) -> str | None:
    """The WHATWG serialization of a listing's remote url when it is an https URL without template,
    user name or password; None for anything else (which then matches no declared remote)."""
    if not isinstance(url, str):
        return None
    m = re.fullmatch(r"(?i)(https)://([^/?#]*)(.*)", url)
    if not m:
        return None
    authority, tail = m.group(2).lower(), m.group(3)
    if authority.endswith(":443"):
        authority = authority[:-4]
    if not tail.startswith("/"):
        tail = "/" + tail
    candidate = f"https://{authority}{tail}"
    return candidate if ad.https_url(candidate) else None


def _remote_set(remotes: Any, declared: bool) -> set[tuple[str, str]] | None:
    """{(type, url)}; None when a listing remote cannot equal any declared one."""
    out: set[tuple[str, str]] = set()
    for remote in remotes or []:
        url = remote.get("url") if declared else _serialized(remote.get("url"))
        if url is None or not isinstance(remote.get("type"), str):
            return None
        out.add((remote["type"], url))
    return out


def remotes_match(listing: Any, doc: dict[str, Any]) -> bool:
    mine = _remote_set(listing, declared=False)
    return mine is not None and mine == _remote_set(doc.get("remotes"), declared=True)


def _encoded(version: str) -> str:
    """``version`` percent encoded byte by byte, everything outside the unreserved set (section 8)."""
    return "".join(chr(b) if chr(b).isascii() and (chr(b).isalnum() or chr(b) in "-._~") else f"%{b:02X}"
                   for b in version.encode("utf-8"))


def _brief(doc: dict[str, Any], digest: str, source: str) -> dict[str, Any]:
    """What HISTOR keeps of a statement."""
    out: dict[str, Any] = {"source": source, "hash": digest, "format": doc["spec"], "version": doc["version"],
                           "issuedAt": doc["issuedAt"], "key": dict(doc["key"])}
    if "expiresAt" in doc:
        out["expiresAt"] = doc["expiresAt"]
    if "toolSetDigest" in doc:
        out["toolSetDigest"] = {"contract": doc["toolSetDigest"]["contract"], "extras": doc["toolSetDigest"]["extras"]}
    if "toolsHash" in doc:
        out["toolsHash"] = doc["toolsHash"]
    if "previous" in doc:
        out["previous"] = doc["previous"]
    return out


@dataclass
class Outcome:
    """The result of checking one listing (the states are section 11's closed list)."""

    state: str
    reasons: list[str] = field(default_factory=list)
    named: dict[str, Any] | None = None      # the declaration the listing names
    in_force: dict[str, Any] | None = None   # the declaration in force, when the check passed
    newer: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.state in PASSED

    def detail(self, checked_at: str) -> dict[str, Any]:
        return {k: v for k, v in (("state", self.state), ("reasons", self.reasons), ("checkedAt", checked_at),
                                  ("named", self.named), ("inForce", self.in_force), ("newer", self.newer),
                                  ("notes", self.notes)) if v not in (None, [])}


def _records(get: Get, key: dict[str, Any]) -> tuple[list[ad.Record] | None, str]:
    """(published records, "") or (None, why the lookup could not be completed). Section 9: a 3xx, 404
    or 410 is an answer without the key; every other failure says nothing about the key."""
    if key["method"] != "http":
        return None, "the key is published by the dns method, which HISTOR does not look up"
    try:
        body = get(f"https://{key['domain']}{KEY_PATH}", limit=MAX_KEY_BYTES, accept="text/plain")
    except DeclarationError as exc:
        text = str(exc)
        if re.match(r"http-(3\d\d|404|410)\b", text):
            return [], ""
        return None, f"the key file could not be read ({text[:120]})"
    try:
        return ad.parse_records(body.decode("utf-8").strip()), ""
    except UnicodeDecodeError:
        return [], ""


def check(get: Get, listing: dict[str, Any], now: datetime) -> Outcome:
    """Check the declaration a listing points at. *listing* is the context from :func:`pointer`."""
    name, version, digest, url = listing["name"], listing["version"], listing["hash"], listing["url"]
    refusal = url_refusal(url, name, digest)
    if refusal:
        return Outcome("pointer_refused", [refusal])
    try:
        body = get(url, limit=ad.MAX_BYTES, accept="application/json")
    except DeclarationError as exc:
        if "larger than" in str(exc):
            return Outcome("declaration_not_the_named_one", [f"the file is larger than {ad.MAX_BYTES} bytes"])
        return Outcome("declaration_unreachable", [str(exc)[:200]])
    try:
        doc = ad.parse(body)
        if doc.get("spec") not in ad.FORMATS:
            raise ad.Malformed("spec names no known format")
        got = ad.declaration_hash(doc)
    except Exception as exc:  # noqa: BLE001 - whatever cannot be hashed is not the named declaration
        return Outcome("declaration_not_the_named_one", [f"the file is not a declaration ({str(exc)[:120]})"])
    if got != digest:
        return Outcome("declaration_not_the_named_one", ["the file's declaration hash is not the pointer hash"])
    try:
        ad.check_members(doc)
    except ad.Malformed as exc:
        return Outcome("malformed", [str(exc)], named={"hash": digest, "format": doc.get("spec")})
    named = {"hash": digest, "format": doc["spec"]}
    if doc["name"] != name or doc["version"] != version:
        return Outcome("listing_mismatch", ["the declaration is for another name or version than the listing"], named=named)
    if not remotes_match(listing.get("remotes"), doc):
        return Outcome("listing_mismatch", ["the listing's remotes and the declared remotes differ"], named=named)
    if not ad.namespace_ok(name, doc["key"]):
        return Outcome("name_domain_mismatch",
                       [f"a {doc['key']['method']} key at {doc['key']['domain']} does not prove this namespace"], named=named)
    records, why = _records(get, doc["key"])
    if records is None:
        return Outcome("key_lookup_failed", [why], named=named)
    result = ad.verify(doc, records, now=now)
    named["state"] = result.state
    if result.state not in ("valid", "expired", "not_yet_valid"):
        return Outcome(result.state, result.reasons, named=named)

    outcome = Outcome("verified" if result.state == "valid" else result.state, list(result.reasons), named=named)
    folder = url[: url.index("/by-hash/") + 1]
    candidates: list[tuple[str, dict[str, Any], str, list[str]]] = []
    seen: dict[str, str] = {}
    for path in (f"versions/{_encoded(version)}.json", "latest.json"):
        report = _newer(get, folder, path, doc, digest, listing, records, now)
        if report.get("hash") in seen and report["result"] == "in_force":
            report["sameAs"] = seen[report["hash"]]
        outcome.newer.append({k: v for k, v in report.items() if not k.startswith("_")})
        if report["result"] == "in_force" and report["hash"] not in seen:
            seen[report["hash"]] = path
            candidates.append((path, report["_doc"], report["hash"], report["_chain"]))
    if candidates:
        path, newest, newest_hash, _ = candidates[0]
        if len(candidates) == 2:
            first, second = candidates
            if first[2] in second[3]:      # the second one follows the first: it is the later one
                path, newest, newest_hash, _ = second
            elif second[2] not in first[3]:
                outcome.notes.append("two newer declarations do not follow one another; the one at versions/ is used")
        if not remotes_match(listing.get("remotes"), newest):
            for entry in outcome.newer:
                if entry.get("hash") == newest_hash:
                    entry["result"] = "remotes_differ"
            return Outcome("listing_mismatch", ["the newer declaration in force declares other remotes than the listing"],
                           named=named, newer=outcome.newer, notes=outcome.notes)
        outcome.state, outcome.reasons = "verified_newer_declaration", []
        outcome.in_force = _brief(newest, newest_hash, path)
        if result.state != "valid":
            outcome.notes.append(f"the declaration the listing names is {result.state}")
    elif result.state == "valid":
        outcome.in_force = _brief(doc, digest, "pointer")
    if any(e["result"] not in ("in_force", "absent", "same_as_pointer") for e in outcome.newer):
        outcome.notes.append("a newer file for this version was read and not put in force")
    return outcome


def _newer(get: Get, folder: str, path: str, pointed: dict[str, Any], pointed_hash: str, listing: dict[str, Any],
           records: list[ad.Record], now: datetime) -> dict[str, Any]:
    """One file beside the named declaration: ``result`` says whether it is put in force and, if not, why."""
    report: dict[str, Any] = {"path": path}
    try:
        body = get(folder + path, limit=ad.MAX_BYTES, accept="application/json")
    except DeclarationError as exc:
        report["result"] = "absent" if re.match(r"http-(404|410)\b", str(exc)) else "unreachable"
        if report["result"] == "unreachable":
            report["reason"] = str(exc)[:160]
        return report
    try:
        doc = ad.parse(body)
        ad.check_members(doc)
    except ad.Malformed as exc:
        return {**report, "result": "invalid", "reason": str(exc)[:160]}
    digest = ad.declaration_hash(doc)
    report.update(hash=digest, format=doc["spec"])
    if digest == pointed_hash:
        return {**report, "result": "same_as_pointer"}
    if doc["name"] != listing["name"]:
        return {**report, "result": "invalid", "reason": "it is about another server"}
    if doc["version"] != listing["version"]:
        return {**report, "result": "other_version", "reason": f"it is for version {doc['version'][:40]}"}
    # Walk previous back to the pointer hash; every read stays at a by-hash path of this folder.
    chain: list[tuple[dict[str, Any], str]] = [(doc, digest)]
    walked = {digest}
    unreached = {**report, "result": "not_chained",
                 "reason": "its previous history does not reach the declaration the listing names"}
    while True:
        earlier_hash = chain[-1][0].get("previous")
        if earlier_hash == pointed_hash:
            chain.append((pointed, pointed_hash))
            break
        if earlier_hash is None or len(chain) >= MAX_LINKS or earlier_hash in walked:
            return unreached
        try:
            earlier = ad.parse(get(f"{folder}by-hash/{earlier_hash[7:]}.json", limit=ad.MAX_BYTES, accept="application/json"))
            ad.check_members(earlier)
        except (DeclarationError, ad.Malformed):
            return unreached
        if ad.declaration_hash(earlier) != earlier_hash or earlier["name"] != listing["name"]:
            return {**report, "result": "not_chained", "reason": "an earlier file is not the declaration asked for"}
        walked.add(earlier_hash)
        chain.append((earlier, earlier_hash))
    for i in range(len(chain) - 1):
        current, earlier = chain[i][0], chain[i + 1][0]
        result = ad.verify(current, records, now=now, previous=earlier, check_time=(i == 0))
        if result.state in ("expired", "not_yet_valid"):
            return {**report, "result": result.state, "reason": "; ".join(result.reasons)}
        if result.state == "chain_mismatch":
            return {**report, "result": "not_chained", "state": "chain_mismatch", "reason": "; ".join(result.reasons)}
        if result.state != "valid":
            return {**report, "result": "invalid", "state": result.state, "reason": "; ".join(result.reasons)}
        if result.chain and (result.chain["keyChanged"] or result.chain["keyLocationChanged"]):
            return {**report, "result": "key_changed", "reason": "a link in its history crosses a change of key"}
    return {**report, "result": "in_force", "links": len(chain) - 1, "_doc": doc, "_chain": [h for _, h in chain[1:]]}


def read(get: Get, listing: dict[str, Any], now: datetime, checked_at: str) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """(sets, notes, detail) for histor.declared.DeclarationFetcher. A check that did not pass raises
    DeclarationError carrying the detail, so what the publisher declared before is kept, not withdrawn."""
    outcome = check(get, listing, now)
    if not outcome.passed:
        error = DeclarationError(f"{outcome.state}: {'; '.join(outcome.reasons)[:240]}")
        error.detail = outcome.detail(checked_at)  # type: ignore[attr-defined]
        raise error
    stated = (outcome.in_force or {}).get("toolSetDigest")
    if not stated:
        outcome.notes.append("the statement in force states a toolsHash (format 0.1), which no observer's digest can be compared with")
    sets = [{"digest": stated["contract"], "source": SOURCE}] if stated else []
    return sets, outcome.notes, outcome.detail(checked_at)
