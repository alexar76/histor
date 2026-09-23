"""Audit a HISTOR log from the outside: ``python -m histor audit <base-url> [--state FILE] [--label ID]``.

What it checks, trusting nothing but the log's published key:

1. the latest signed tree head verifies against the log's ``did:key``;
2. if a head was saved by a previous run (``--state``), the new head is consistent with it —
   the log was only appended to, nothing removed or rewritten — and the log key did not change;
3. with ``--label``, the label is a valid AWR/2 document and its leaf is in the tree under the
   new head.

Then it saves the new head for next time. Run it from cron on a machine you control and you are
a witness: a log that forks its history, or shows you a different tree than it shows others,
fails here. Exit status 0 means every check passed, 1 that a check FAILED (the log misbehaved),
2 that the audit could not be completed (unreachable, no head yet, a label not yet under one) —
which says nothing either way about the log's honesty.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from awr import canonicalize, verify_document

from histor import merkle
from histor.logbook import STH_TYPE
from histor.logbook import verify_document as verify_signed


class AuditFailure(RuntimeError):
    """The log failed a check: a forged head, a fork, a rewrite, a missing label."""


class AuditIncomplete(RuntimeError):
    """The audit could not run to the end. Not evidence against the log."""


def _get(http: httpx.Client, path: str, **params: Any) -> dict[str, Any]:
    try:
        response = http.get(path, params=params or None)
    except httpx.HTTPError as exc:
        raise AuditIncomplete(f"{path}: {type(exc).__name__}") from exc
    if response.status_code == 404 and path == "/api/v1/log/sth":
        raise AuditIncomplete("the log has no signed tree head yet; nothing to audit")
    if response.status_code == 409:
        raise AuditIncomplete(f"{path}: not yet under a signed tree head; audit again after the next crawl")
    if response.status_code >= 400:
        raise AuditIncomplete(f"{path}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise AuditIncomplete(f"{path}: the answer is not JSON") from exc


def audit(base: str, *, state: Path | None = None, label_id: str | None = None,
          client: httpx.Client | None = None) -> dict[str, Any]:
    http = client or httpx.Client(base_url=base.rstrip("/"), timeout=30)
    report: dict[str, Any] = {"log": base}
    issuer = _get(http, "/api/v1/issuer")
    did = issuer["did"]
    sth = _get(http, "/api/v1/log/sth")
    if not verify_signed(sth, did, STH_TYPE) or sth.get("log") != did:
        raise AuditFailure("the signed tree head does not verify against the log key")
    report.update(did=did, treeSize=sth["treeSize"], rootHash=sth["rootHash"], signature="ok")

    if state and state.is_file():
        kept = json.loads(state.read_text())
        if kept.get("log") != did:
            raise AuditFailure(f"the log key changed: kept {kept.get('log')}, now {did}")
        if sth["treeSize"] < kept["treeSize"]:
            raise AuditFailure(f"the log shrank: kept {kept['treeSize']}, now {sth['treeSize']}")
        if sth["treeSize"] == kept["treeSize"]:
            if sth["rootHash"] != kept["rootHash"]:
                raise AuditFailure("same size, different root: the log rewrote its history")
            report["consistency"] = "unchanged"
        else:
            proof = _get(http, "/api/v1/log/proof/consistency", first=kept["treeSize"], second=sth["treeSize"])["proof"]
            if not merkle.verify_consistency(kept["treeSize"], sth["treeSize"], [bytes.fromhex(h) for h in proof],
                                             bytes.fromhex(kept["rootHash"]), bytes.fromhex(sth["rootHash"])):
                raise AuditFailure(f"the new head is NOT consistent with the head kept at size {kept['treeSize']}")
            report["consistency"] = f"ok ({kept['treeSize']} → {sth['treeSize']})"

    if label_id:
        doc = _get(http, f"/api/v1/labels/{label_id}")
        result = verify_document(doc)
        if not result["valid"]:
            raise AuditFailure(f"label {label_id} is not a valid AWR/2 document: {result['reasons']}")
        if doc["issuer"]["id"] != did:
            raise AuditFailure(f"label {label_id} was issued by {doc['issuer']['id']}, not by this log")
        proof = _get(http, f"/api/v1/labels/{label_id}/proof", tree_size=sth["treeSize"])
        ok = merkle.verify_inclusion(merkle.leaf_hash(canonicalize(doc)), proof["leafIndex"], sth["treeSize"],
                                     [bytes.fromhex(h) for h in proof["inclusionProof"]], bytes.fromhex(sth["rootHash"]))
        if not ok:
            raise AuditFailure(f"label {label_id} is not in the tree under the head of size {sth['treeSize']}")
        report["label"] = {"id": label_id, "leafIndex": proof["leafIndex"], "inclusion": "ok"}

    if state:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(sth))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="histor audit", description=__doc__.split("\n\n")[0])
    parser.add_argument("base", help="log base URL, e.g. https://histor.modelmarket.dev")
    parser.add_argument("--state", type=Path, help="file holding the head from the previous run")
    parser.add_argument("--label", help="also check one label's validity and inclusion")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(audit(args.base, state=args.state, label_id=args.label), indent=2))
        return 0
    except AuditFailure as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    except AuditIncomplete as exc:
        print(f"AUDIT INCOMPLETE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
