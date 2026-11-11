"""``histor revision``: publisher-signed tool-manifest revisions, as OWASP MCPSTG-SUPP-06 describes them.

    histor revision build --server io.github.alexar76/histor --stdio "aimarket-histor" \\
        --index index.json --identity "$SIGNER" --server-version 0.7.0 -o statement.json
    sigstore sign --bundle statement.sigstore.json statement.json        # in the publisher's CI
    histor revision envelope statement.json statement.sigstore.json -o envelope.json
    histor revision record envelope.json --dir .                          # revisions/<n>.json + index.json
    histor revision verify envelope.json --stdio "aimarket-histor"

**Canonical form** (SUPP-06, Test Data, steps 1-4):
- every tool `tools/list` returns, following `nextCursor`;
- the fields `name`, `title`, `description`, `inputSchema`, `outputSchema`, `annotations` and `execution` (the
  execution hint the MCP spec defines), as present;
- one object keyed by tool name, RFC 8785 (JCS), SHA-256.

A per-tool digest is the same computation on one tool's entry.

**Statement.** `build` writes the statement as RFC 8785 bytes, so the file `sigstore sign` signs is exactly what
a verifier rebuilds from the envelope after dropping `signer.signature` and `transparencyLog`.

**Signing** is done in the publisher's CI with a short-lived identity: Sigstore keyless with GitHub Actions OIDC,
which is SUPP-06's remediation. Sigstore also records the signature in Rekor. The envelope carries:
- the whole Sigstore bundle as `signer.signature`;
- the Rekor entry and its inclusion proof as `transparencyLog`.

**`verify`** answers with SUPP-06's reasons: `unsigned`, `unexpected_signer`, `not_logged`, `digest_mismatch`.
- For a server named `io.github.<owner>/...` the expected signer is a GitHub Actions workflow in a repository
  owned by `<owner>`: the registry proved that namespace through the same GitHub account.
- Any other namespace needs `--expected-identity`.
- Checking the signature and the inclusion proof needs `pip install 'aimarket-histor[sigstore]'`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from histor.fingerprints import canonical

FORMAT = "histor.manifest-revision/v1"
INDEX_FORMAT = "histor.manifest-revisions/v1"
CANONICALIZATION = "RFC8785-JCS"
TOOL_FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations", "execution")
GITHUB_ISSUER = "https://token.actions.githubusercontent.com"
UNSIGNED_MEMBERS = ("transparencyLog",)  # plus signer.signature


class RevisionError(ValueError):
    pass


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8", "surrogatepass")).hexdigest()


def canonical_manifest(tools: list[Any]) -> dict[str, dict[str, Any]]:
    """SUPP-06's canonical form: the snapshot fields of every tool, keyed by tool name."""
    manifest: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not tool["name"]:
            raise RevisionError("a tool without a string name")
        if tool["name"] in manifest:
            raise RevisionError(f"two tools named {tool['name']!r}: a manifest keyed by name cannot hold both")
        manifest[tool["name"]] = {k: tool[k] for k in TOOL_FIELDS if k in tool}
    return manifest


def digests(tools: list[Any]) -> tuple[str, dict[str, str]]:
    manifest = canonical_manifest(tools)
    return _digest(manifest), {name: _digest(entry) for name, entry in manifest.items()}


def signed_bytes(doc: dict[str, Any]) -> bytes:
    """The bytes the signature covers: every member except ``signer.signature`` and ``transparencyLog``."""
    body = {k: v for k, v in doc.items() if k not in UNSIGNED_MEMBERS}
    signer = body.get("signer")
    if isinstance(signer, dict):
        body["signer"] = {k: v for k, v in signer.items() if k != "signature"}
    return canonical(body).encode("utf-8", "surrogatepass")


def _owner(server: str) -> str | None:
    """``alexar76`` for ``io.github.alexar76/histor``; None outside the GitHub namespace."""
    head, sep, _ = server.partition("/")
    if sep and head.startswith("io.github.") and len(head) > len("io.github."):
        return head[len("io.github."):]
    return None


def load_index(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    index = json.loads(path.read_text(encoding="utf-8"))
    if index.get("format") != INDEX_FORMAT:
        raise RevisionError(f"{path}: not a {INDEX_FORMAT} index")
    return index


def build_statement(*, server: str, tools: list[Any], identity: str, previous: dict[str, Any] | None = None,
                    server_version: str | None = None, source: str | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    manifest_digest, tool_digests = digests(tools)
    statement: dict[str, Any] = {
        "format": FORMAT,
        "server": server,
        "revision": int(previous["revision"]) + 1 if previous else 1,
        "canonicalization": CANONICALIZATION,
        "manifestDigest": manifest_digest,
        "toolDigests": tool_digests,
        "previousManifestDigest": previous["manifestDigest"] if previous else None,
        "signer": {"identity": identity},
        "createdAt": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if server_version:
        statement["serverVersion"] = server_version
    if source:
        statement["source"] = source
    return statement


def _tlog(bundle: dict[str, Any]) -> dict[str, Any]:
    entries = (bundle.get("verificationMaterial") or {}).get("tlogEntries") or []
    if not entries or not isinstance(entries[0], dict):
        raise RevisionError("the Sigstore bundle carries no transparency-log entry")
    entry = entries[0]
    proof = entry.get("inclusionProof") or {}
    return {
        "log": "sigstore-rekor",
        "logId": (entry.get("logId") or {}).get("keyId"),
        "logIndex": entry.get("logIndex"),
        "integratedTime": entry.get("integratedTime"),
        "inclusionProof": {
            "treeSize": proof.get("treeSize"),
            "rootHash": proof.get("rootHash"),
            "hashes": proof.get("hashes") or [],
            "checkpoint": (proof.get("checkpoint") or {}).get("envelope"),
        },
    }


def assemble_envelope(statement: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    envelope = json.loads(json.dumps(statement))
    envelope["signer"]["signature"] = {"sigstoreBundle": bundle}
    envelope["transparencyLog"] = _tlog(bundle)
    return envelope


def record(envelope: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Write ``revisions/<n>.json`` and append to ``index.json`` (refuses a gap or a rewrite)."""
    index_path = directory / "index.json"
    index = load_index(index_path) or {"format": INDEX_FORMAT, "server": envelope["server"], "revisions": []}
    if index["server"] != envelope["server"]:
        raise RevisionError(f"index is for {index['server']}, envelope for {envelope['server']}")
    last = index["revisions"][-1] if index["revisions"] else None
    want = last["revision"] + 1 if last else 1
    if envelope["revision"] != want:
        raise RevisionError(f"revision {envelope['revision']} after {want - 1}: the index takes the next one only")
    if (last["manifestDigest"] if last else None) != envelope["previousManifestDigest"]:
        raise RevisionError("previousManifestDigest does not match the index's last revision")
    rel = f"revisions/{envelope['revision']}.json"
    (directory / "revisions").mkdir(parents=True, exist_ok=True)
    (directory / rel).write_text(json.dumps(envelope, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    index["revisions"].append({
        "revision": envelope["revision"],
        "manifestDigest": envelope["manifestDigest"],
        "serverVersion": envelope.get("serverVersion"),
        "createdAt": envelope.get("createdAt"),
        "signer": envelope["signer"]["identity"],
        "logIndex": envelope["transparencyLog"]["logIndex"],
        "envelope": rel,
    })
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return index


_LOG_WORDS = ("log", "inclusion", "checkpoint", "rekor", "timestamp", "set ")


def check_bundle(raw: Any, artifact: bytes, *, identity: str, issuer: str = GITHUB_ISSUER,
                 owner: str | None = None, instance: str = "production",
                 offline: bool = False) -> tuple[str, str] | None:
    """Sigstore's verdict on ``artifact`` as SUPP-06 reasons; None when every check holds.

    The signer (certificate identity, issuer and, for ``io.github.<owner>``, the repository owner) is
    checked first, then the signature with the certificate chain and the Rekor entry. A failure in
    the second step that names the log, the inclusion proof, a checkpoint or a timestamp is
    ``not_logged``; any other is ``unexpected_signer``, as the pseudocode's "not signature_valid".
    """
    try:
        from sigstore.errors import Error as SigstoreError
        from sigstore.models import Bundle
        from sigstore.verify import Verifier, policy
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise RevisionError("checking a signature needs: pip install 'aimarket-histor[sigstore]'") from exc
    try:
        bundle = Bundle.from_json(json.dumps(raw))
    except Exception as exc:  # noqa: BLE001 - any malformed bundle is "unsigned"
        return "unsigned", f"no readable Sigstore bundle ({type(exc).__name__})"
    rules: list[Any] = [policy.Identity(identity=identity, issuer=issuer)]
    if owner:
        rules.append(policy.OIDCSourceRepositoryOwnerURI(f"https://github.com/{owner}"))
    try:
        policy.AllOf(rules).verify(bundle.signing_certificate)
    except SigstoreError as exc:
        return "unexpected_signer", str(exc)
    verifier = Verifier.staging(offline=offline) if instance == "staging" else Verifier.production(offline=offline)
    try:
        verifier.verify_artifact(artifact, bundle, policy.UnsafeNoOp())
    except SigstoreError as exc:
        text = str(exc)
        return ("not_logged" if any(w in text.lower() for w in _LOG_WORDS) else "unexpected_signer"), text
    return None


def _sigstore_check(envelope: dict[str, Any], expected_identity: str | None, owner: str | None,
                    offline: bool) -> tuple[str, str] | None:
    """(reason, detail) when the bundle, the signer or the log entry fails; None when all hold."""
    raw = (envelope.get("signer") or {}).get("signature")
    raw = raw.get("sigstoreBundle") if isinstance(raw, dict) else None
    identity = expected_identity or (envelope.get("signer") or {}).get("identity") or ""
    failed = check_bundle(raw, signed_bytes(envelope), identity=identity, owner=owner, offline=offline)
    if failed:
        return failed
    # transparencyLog is outside the signature: it must say what the verified bundle says.
    claimed = envelope.get("transparencyLog") or {}
    try:
        actual = _tlog(raw)
    except RevisionError as exc:
        return "not_logged", str(exc)
    for key in ("logIndex", "logId"):
        if str(claimed.get(key)) != str(actual.get(key)):
            return "not_logged", f"transparencyLog.{key} {claimed.get(key)!r} is not the bundle's {actual.get(key)!r}"
    return None


def verify(envelope: dict[str, Any] | None, *, served_tools: list[Any] | None = None,
           expected_identity: str | None = None, check_signature: bool = True,
           offline: bool = False) -> dict[str, Any]:
    """SUPP-06's admission check, in its pseudocode's order. ``ok`` is True only when every step holds."""
    result: dict[str, Any] = {"ok": False, "reason": None, "detail": ""}
    if not isinstance(envelope, dict) or not (envelope.get("signer") or {}).get("signature"):
        result.update(reason="unsigned", detail="no envelope or no signature")
        return result
    server = envelope.get("server") or ""
    owner = _owner(server)
    identity = (envelope.get("signer") or {}).get("identity") or ""
    if not expected_identity and not owner:
        result.update(reason="unexpected_signer", detail=f"{server}: no expected identity (pass --expected-identity)")
        return result
    if owner and not identity.startswith(f"https://github.com/{owner}/"):
        result.update(reason="unexpected_signer", detail=f"{identity!r} is not a workflow in a repository of {owner}")
        return result
    if expected_identity and identity != expected_identity:
        result.update(reason="unexpected_signer", detail=f"signed by {identity!r}, expected {expected_identity!r}")
        return result
    if check_signature:
        failed = _sigstore_check(envelope, expected_identity, owner, offline)
        if failed:
            result.update(reason=failed[0], detail=failed[1])
            return result
    if served_tools is not None:
        served, served_tools_digests = digests(served_tools)
        result["servedDigest"] = served
        if served != envelope.get("manifestDigest"):
            changed = sorted(n for n in set(served_tools_digests) | set(envelope.get("toolDigests") or {})
                             if served_tools_digests.get(n) != (envelope.get("toolDigests") or {}).get(n))
            result.update(reason="digest_mismatch", detail="served tools that differ: " + ", ".join(changed))
            return result
    result.update(ok=True, reason=None, revision=envelope.get("revision"),
                  logIndex=(envelope.get("transparencyLog") or {}).get("logIndex"))
    return result


def _tools(args: argparse.Namespace) -> list[Any]:
    from histor.toolcost_cli import SourceError, tools_from_json, tools_from_stdio, tools_from_url

    try:
        if args.tools:
            return tools_from_json(json.loads(Path(args.tools).read_text(encoding="utf-8")))
        if args.stdio:
            return tools_from_stdio(args.stdio, args.timeout)
        if args.url:
            return tools_from_url(args.url, args.timeout)
    except SourceError as exc:
        raise RevisionError(str(exc)) from exc
    raise RevisionError("say where the tools come from: --tools FILE, --stdio COMMAND or --url URL")


def _source(p: argparse.ArgumentParser, required: bool) -> None:
    src = p.add_mutually_exclusive_group(required=required)
    src.add_argument("--tools", metavar="FILE", help="a saved tools/list result")
    src.add_argument("--stdio", metavar="COMMAND", help='start the server, e.g. "aimarket-histor"')
    src.add_argument("--url", metavar="URL", help="a streamable HTTP MCP endpoint")
    p.add_argument("--timeout", type=float, default=60.0, help="seconds for the whole listing (default 60)")


def _github_output(**values: str) -> None:
    import os

    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="histor revision", description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="read the server's tools and write the statement to sign")
    b.add_argument("--server", required=True, help="registry name, e.g. io.github.alexar76/histor")
    b.add_argument("--identity", required=True, help="the signer, e.g. https://github.com/$GITHUB_WORKFLOW_REF")
    b.add_argument("--index", type=Path, help="index.json of earlier revisions (absent: this is revision 1)")
    b.add_argument("--server-version")
    b.add_argument("--source", help='what was read, e.g. "pypi:aimarket-histor==0.7.0"')
    b.add_argument("--if-changed", action="store_true", help="write nothing when the last revision has this digest")
    b.add_argument("-o", "--output", required=True)
    _source(b, required=True)
    e = sub.add_parser("envelope", help="statement + Sigstore bundle -> envelope")
    e.add_argument("statement")
    e.add_argument("bundle")
    e.add_argument("-o", "--output", required=True)
    r = sub.add_parser("record", help="add the envelope to revisions/ and index.json")
    r.add_argument("envelope")
    r.add_argument("--dir", type=Path, default=Path("."))
    v = sub.add_parser("verify", help="SUPP-06 admission check of an envelope against what the server serves")
    v.add_argument("envelope")
    v.add_argument("--expected-identity")
    v.add_argument("--offline", action="store_true", help="use the cached Sigstore trust root")
    _source(v, required=False)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            index = load_index(args.index)
            previous = index["revisions"][-1] if index and index["revisions"] else None
            if index and index["server"] != args.server:
                raise RevisionError(f"index is for {index['server']}, not {args.server}")
            statement = build_statement(server=args.server, tools=_tools(args), identity=args.identity,
                                        previous=previous, server_version=args.server_version, source=args.source)
            if args.if_changed and previous and previous["manifestDigest"] == statement["manifestDigest"]:
                print(f"unchanged: {statement['manifestDigest']} is revision {previous['revision']}")
                _github_output(changed="false")
                return 0
            Path(args.output).write_bytes(canonical(statement).encode("utf-8", "surrogatepass"))
            print(f"revision {statement['revision']} of {args.server}: {statement['manifestDigest']}"
                  f" ({len(statement['toolDigests'])} tools)")
            _github_output(changed="true", revision=str(statement["revision"]))
            return 0
        if args.command == "envelope":
            raw = Path(args.statement).read_bytes()
            statement = json.loads(raw)
            if canonical(statement).encode("utf-8", "surrogatepass") != raw:
                raise RevisionError(f"{args.statement} is not RFC 8785 text: the signature would not cover it")
            bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
            envelope = assemble_envelope(statement, bundle)
            Path(args.output).write_text(json.dumps(envelope, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"envelope: revision {envelope['revision']}, Rekor log index {envelope['transparencyLog']['logIndex']}")
            return 0
        if args.command == "record":
            envelope = json.loads(Path(args.envelope).read_text(encoding="utf-8"))
            index = record(envelope, args.dir)
            print(f"recorded revision {envelope['revision']}; index has {len(index['revisions'])}")
            return 0
        envelope = json.loads(Path(args.envelope).read_text(encoding="utf-8"))
        served = _tools(args) if (args.tools or args.stdio or args.url) else None
        result = verify(envelope, served_tools=served, expected_identity=args.expected_identity,
                        offline=args.offline)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except RevisionError as exc:
        print(f"histor revision: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
