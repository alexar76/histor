"""/check — "is what I just received what everyone else is seeing?"

A client about to trust an MCP server already holds the thing MTL/1 section 4.6 says is
expensive to get: the tool set, retrieved by itself, from the server. So the check needs no
execution on our side. The client sends the endpoint and either the tools or their MTL/1
digest; HISTOR answers, signed, with what it has observed at that endpoint.

Clients that opt in (``contribute: true``) add their digest to a per-day count. That count is
what makes a server that shows different clients different definitions visible at all: a
crawler sees one view, a local scanner sees one view, only a comparison across many sees the
split. Nothing but ``(target, digest, day)`` is stored — no address, no account, no tools.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from awr import SigningKey

from histor.logbook import sign_document, utcnow
from histor.mcpclient import MAX_TOOLS
from histor.scanner import Scanner, ScannerError
from histor.store import Store, dumps
from histor.subject import MtlError, tool_set_digest

CHECK_TYPE = "histor.check/v1"
SRI = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")
MAX_MATCHES = 200          # matches returned per answer; the per-tier counts cover all of them
CHECK_SCAN_TIMEOUT_S = 30  # a /check scan is one set, not a crawl batch
# One fresh scan at a time for the whole site: a scan is a Node subprocess, and callers queueing
# behind each other is exactly the stall this prevents. A caller who finds it busy is told so.
_SCAN_SLOT = threading.Semaphore(1)

NOTES = {
    "not-listed": "HISTOR has no record of this endpoint or name in the registries it reads.",
    "not-observed": "HISTOR lists this endpoint but has not retrieved its tool definitions (see lastStatus).",
    "same": "The tool definitions you sent are the set HISTOR currently observes at this endpoint.",
    "previously-observed": (
        "The tool definitions you sent match a set HISTOR observed at this endpoint earlier, not the current one."
    ),
    "different": (
        "The tool definitions you sent differ from every set HISTOR has observed at this endpoint. Possible causes: "
        "the server changed after HISTOR's last observation, its definitions vary per account, or it shows different "
        "clients different definitions. Read the definitions again before approving them."
    ),
    "no-digest": "No tool set was sent, so only HISTOR's own observations are reported.",
    "undigestible": (
        "The tools you sent are not digestible under MTL/1 (see query.digestError), so they cannot be compared "
        "with HISTOR's record. HISTOR's own observations are reported."
    ),
}
NOTE_DELISTED = " This endpoint is no longer listed in the registry; HISTOR's record stops at {day}."
NOTE_LAST_FAILED = (
    " HISTOR's latest attempt at this endpoint produced no tool set ({status}); the comparison is with its "
    "last successful observation."
)


class CheckError(ValueError):
    pass


class Checker:
    def __init__(self, store: Store, key: SigningKey, scanner: Scanner, public_base: str) -> None:
        self.store = store
        self.key = key
        self.scanner = scanner
        self.public_base = public_base

    def check(self, body: dict[str, Any], *, allow_scan: bool | Callable[[], bool],
              may_contribute: Callable[[str], bool] | None = None) -> dict[str, Any]:
        """*allow_scan* may be a callable: it is asked only when a fresh scan is about to run, so a
        check that needs none does not spend the caller's scan budget."""
        endpoint = body.get("endpoint")
        name = body.get("name")
        for field, value in (("endpoint", endpoint), ("name", name)):
            if value is not None and (not isinstance(value, str) or "\x00" in value or len(value) > 2000):
                raise CheckError(f"{field} must be a string of at most 2000 characters")
        if not endpoint and not name:
            raise CheckError("send endpoint or name")

        client_digest: str | None = None
        client_entries: list[dict[str, Any]] | None = None
        digest_error: dict[str, str] | None = None
        if body.get("tools") is not None:
            if not isinstance(body["tools"], list):
                raise CheckError("tools must be the tools array of a tools/list result")
            if len(body["tools"]) > MAX_TOOLS:
                raise CheckError(f"more than {MAX_TOOLS} tools")
            try:
                client_digest, client_entries = tool_set_digest(body["tools"])
            except MtlError as exc:
                digest_error = {"code": exc.code, "detail": exc.detail}
        elif body.get("toolSetDigest") is not None:
            if not isinstance(body["toolSetDigest"], str) or not SRI.match(body["toolSetDigest"]):
                raise CheckError("toolSetDigest must be an MTL/1 tool-set digest (sha256-<base64>)")
            client_digest = body["toolSetDigest"]

        candidates = self.store.targets_by(endpoint=endpoint) if endpoint else self.store.targets_by(name=name)
        target = next((t for t in candidates if not t["delisted"]), candidates[0] if candidates else None)

        result: dict[str, Any] = {
            "type": CHECK_TYPE,
            "checkedAt": utcnow(),
            "issuer": self.key.did,
            "query": {k: v for k, v in (("endpoint", endpoint), ("name", name), ("toolSetDigest", client_digest)) if v},
        }
        if digest_error:
            result["query"]["digestError"] = digest_error

        if target is None:
            state = "not-listed"
        else:
            result["target"] = {
                "id": target["id"],
                "name": target["name"],
                "endpoint": target["endpoint"],
                "page": f"{self.public_base}/s/{target['id']}",
                "badge": f"{self.public_base}/badge/{target['id']}.svg",
                "lastStatus": target["last_status"],
                "listed": not target["delisted"],
            }
            if target["current_toolset"] or target["current_subject"]:
                result["observed"] = {
                    k: v for k, v in (
                        ("toolSetDigest", target["current_toolset"]),
                        ("subjectDigest", target["current_subject"]),
                        ("toolCount", target["current_count"]),
                        ("firstPinned", target["first_pinned"]),
                        ("unchangedSince", target["unchanged_since"]),
                        ("lastObserved", target["last_ok"]),
                        ("observations", target["ok_observations"]),
                        ("changes", target["changes"]),
                    ) if v is not None
                }
            if not target["current_subject"]:
                state = "not-observed"
            elif digest_error:
                state = "undigestible"
            elif client_digest is None:
                state = "no-digest"
            elif target["current_toolset"] and client_digest == target["current_toolset"]:
                state = "same"
            else:
                seen = self.store.toolset_seen(target["id"], client_digest)
                if seen:
                    state = "previously-observed"
                    result["seenBefore"] = seen
                else:
                    state = "different"
            if (body.get("contribute") is True and client_digest
                    and (may_contribute is None or may_contribute(target["id"]))):
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                self.store.add_client_report(target["id"], client_digest, day)
                result["contributed"] = True

        result["match"] = state
        note = NOTES[state]
        if target is not None and state not in ("not-listed", "not-observed"):
            if target["delisted"]:
                note += NOTE_DELISTED.format(day=str(target["last_listed"] or "")[:10])
            if target["last_status"] and target["last_status"] != "ok":
                note += NOTE_LAST_FAILED.format(status=target["last_status"])
        result["note"] = note
        scan = self._scan(client_digest, client_entries, target, digest_error is not None, allow_scan)
        if scan is not None:
            result["patternScan"] = scan
        sth = self.store.latest_sth()
        if sth:
            result["log"] = {"treeSize": sth["treeSize"], "rootHash": sth["rootHash"], "timestamp": sth["timestamp"]}
        return sign_document(self.key, result)

    def _pattern_set(self) -> str | None:
        """The pattern set a fresh scan would use now: the running sidecar's, not the last crawl's."""
        try:
            return self.scanner.rulesets().pattern_set_digest
        except ScannerError:
            return self.store.get_meta("pattern_set")

    def _scan(self, client_digest: str | None, entries: list[dict[str, Any]] | None, target: dict[str, Any] | None,
              sent_undigestible: bool, allow_scan: bool | Callable[[], bool]) -> dict[str, Any] | None:
        """WARDEN pattern matches for the set the client holds — cached, or computed if allowed.

        Never a scan of some OTHER set: when the client sent tools HISTOR cannot digest, there is
        nothing to report rather than the observed set's matches presented as theirs.
        """
        if sent_undigestible:
            return None
        pattern_set = self._pattern_set()
        digest = client_digest or (target or {}).get("current_toolset")
        if not digest or not pattern_set:
            return None
        with self.store.db.read() as tx:
            row = tx.one("SELECT matches FROM scans WHERE toolset=? AND pattern_set=?", (digest, pattern_set))
        if row is not None:
            matches = json.loads(row["matches"])
        elif entries is not None and (allow_scan() if callable(allow_scan) else allow_scan):
            if not _SCAN_SLOT.acquire(blocking=False):
                return {"status": "busy", "note": "Another scan is running; try again in a moment."}
            try:
                out = self.scanner.scan([{"id": "check", "server": {"id": (target or {}).get("name", "")},
                                          "tools": entries}], timeout=CHECK_SCAN_TIMEOUT_S)
            except ScannerError:
                return {"status": "unavailable"}
            finally:
                _SCAN_SLOT.release()
            matches = out["check"]["patternMatches"]
            if self.store.has_blob(digest, "toolset"):
                # Cached only for sets HISTOR itself observed: a stranger's set is scanned for them
                # and forgotten, so anonymous callers cannot grow the table (audit F13).
                with self.store.db.transaction() as tx:
                    tx.execute(
                        "INSERT INTO scans(toolset, pattern_set, matches) VALUES(?, ?, ?) "
                        "ON CONFLICT(toolset, pattern_set) DO NOTHING",
                        (digest, pattern_set, dumps(matches)),
                    )
        else:
            return {"status": "not-scanned"}
        return {
            "status": "scanned",
            "toolSetDigest": digest,
            "patternSet": pattern_set,
            "block": sum(1 for m in matches if m["tier"] == "block"),
            "advise": sum(1 for m in matches if m["tier"] == "advise"),
            "matches": matches[:MAX_MATCHES],
            **({"truncated": len(matches)} if len(matches) > MAX_MATCHES else {}),
            "note": "A match is a reason to read the definition, not a finding of fact. Advise-tier matches never block in WARDEN.",
        }
