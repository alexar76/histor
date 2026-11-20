"""The ``addon-declaration`` format, 0.1 and 0.2: parsing, member rules, signing input, verification.

A publisher's signed statement about one version of an MCP server (spec:
https://github.com/bcrypto0/addon-declaration, SPEC.md as read on 2026-11-19; the section numbers
below are that document's). This module is the offline part: everything that needs no network. It is
checked against that repository's conformance vectors (tests/test_addon_declaration_vectors.py).
histor.signed_declaration does the reading of a registry listing around it.

What is implemented: sections 3 and 17.1 (members), 5 (strict parsing), 6 and 17.2 (signing input),
7 and 17.5 (declaration hash, link check), 9 (which published record a declaration's key matches),
11 steps 1 to 5 and 7. Not implemented: step 6, the tool check. HISTOR does not compare a tool list
with a declaration here; it shows the stated digests next to the ones it recorded itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from histor.subject import canonical_tool_bytes

FORMAT_01 = "addon-declaration/0.1"
FORMAT_02 = "addon-declaration/0.2"
FORMATS = (FORMAT_01, FORMAT_02)
DIGEST_SPEC = "mcp-tool-set-digest/1"
MAX_BYTES = 16384
MAX_DEPTH = 64
MAX_SAFE_INTEGER = 2**53 - 1
FUTURE_SKEW = timedelta(seconds=300)

HASH = re.compile(r"sha256:[0-9a-f]{64}")
SRI = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")
_NAME = re.compile(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+")
_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z")
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_MAILTO = re.compile(r"mailto:[A-Za-z0-9._+-]+@([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")
_URL_CHARS = re.compile(r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})*")
_NETWORK = re.compile(r"[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}")
_AMOUNT = re.compile(r"0|[1-9][0-9]{0,77}")
_PRINTABLE = re.compile(r"[\x21-\x7e]{1,128}")
_RECORD = re.compile(r"v=(MCPv1|ADKv1);[\t\n\f\r ]*k=([^;]+);[\t\n\f\r ]*p=([A-Za-z0-9+/=]+)")

_MEMBERS = {
    FORMAT_01: ({"spec", "name", "version", "toolsHash", "contact", "issuedAt", "key", "signature"},
                {"remotes", "payment", "expiresAt", "previous"}),
    FORMAT_02: ({"spec", "name", "version", "toolSetDigest", "issuedAt", "key", "signature"},
                {"remotes", "payment", "contact", "expiresAt", "previous"}),
}
# Default_Ignorable_Code_Point outside the general categories below (section 3, "unsafe characters").
_IGNORABLE = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x3164, 0x3164),
              (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0xE0000, 0xE0FFF))


class Malformed(ValueError):
    """Not a declaration of its format (section 11, step 1)."""


# -- section 5: strict parsing ----------------------------------------------------------------------


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Malformed(f"duplicate member {key!r}")
        out[key] = value
    return out


def _int(text: str) -> int:
    value = int(text)
    if abs(value) > MAX_SAFE_INTEGER:
        raise Malformed("an integer beyond 2^53 - 1")
    return value


def _float(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")):
        raise Malformed("a number that overflows a double")
    if value == 0 and re.search(r"[1-9]", text.lower().split("e")[0]):
        raise Malformed("a nonzero number that rounds to zero")
    return value


def _constant(text: str) -> Any:
    raise Malformed(f"{text} is not JSON")


def _walk(value: Any, depth: int) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise Malformed("a lone surrogate")
    elif isinstance(value, (dict, list)):
        if depth >= MAX_DEPTH:
            raise Malformed(f"nested deeper than {MAX_DEPTH} levels")
        if isinstance(value, dict):
            for key, item in value.items():
                _walk(key, depth + 1)
                _walk(item, depth + 1)
        else:
            for item in value:
                _walk(item, depth + 1)


def parse(body: bytes) -> dict[str, Any]:
    """The declaration object in *body*, read under the strict rules of section 5."""
    if len(body) > MAX_BYTES:
        raise Malformed(f"larger than {MAX_BYTES} bytes")
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Malformed("not valid UTF-8") from exc
    try:
        doc = json.loads(text, object_pairs_hook=_pairs, parse_int=_int, parse_float=_float, parse_constant=_constant)
    except RecursionError as exc:
        raise Malformed(f"nested deeper than {MAX_DEPTH} levels") from exc
    except ValueError as exc:
        if isinstance(exc, Malformed):
            raise
        raise Malformed("not JSON") from exc
    if not isinstance(doc, dict):
        raise Malformed("not a JSON object")
    _walk(doc, 0)
    return doc


# -- section 3: member rules ------------------------------------------------------------------------


def unsafe(text: str) -> bool:
    """True when *text* holds a character that can change what is displayed without changing what
    was signed (section 3). Cn follows this runtime's Unicode version, as the spec allows."""
    for ch in text:
        cp = ord(ch)
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Co", "Cn", "Cs"):
            return True
        if 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE:
            return True
        if any(lo <= cp <= hi for lo, hi in _IGNORABLE):
            return True
    return False


def _ipv4_host(host: str) -> bool | None:
    """None: not a number-looking host. True: canonical dotted decimal. False: a number-looking host
    that a WHATWG parser would rewrite or refuse."""
    labels = host[:-1].split(".") if host.endswith(".") else host.split(".")
    last = labels[-1]
    if not (last.isdigit() or re.fullmatch(r"0[xX][0-9a-fA-F]*", last)):
        return None
    return len(labels) == 4 and all(p.isdigit() and str(int(p)) == p and int(p) <= 255 for p in labels) \
        and not host.endswith(".")


def https_url(value: Any, limit: int = 2048) -> bool:
    """An https URL in the WHATWG serialization (section 3): only RFC 3986 characters, lowercase host,
    no user name or password, no default port, a path of at least ``/``, no dot segments."""
    if not isinstance(value, str) or len(value) > limit or not _URL_CHARS.fullmatch(value):
        return False
    if not value.startswith("https://"):
        return False
    rest = value[8:]
    cut = min((rest.index(c) for c in "/?#" if c in rest), default=len(rest))
    authority, tail = rest[:cut], rest[cut:]
    if not authority or "@" in authority or not tail.startswith("/"):
        return False
    host, port = authority, ""
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return False
        host, after = authority[:end + 1], authority[end + 1:]
        if after and not after.startswith(":"):
            return False
        port = after[1:] if after else ""
        if after == ":":
            return False
        try:
            if "[" + str(ipaddress.IPv6Address(host[1:-1])) + "]" != host:
                return False
        except ValueError:
            return False
    else:
        if ":" in authority:
            host, _, port = authority.partition(":")
            if not port:
                return False
        if not host or host != host.lower() or not re.fullmatch(r"[a-z0-9._~-]+", host):
            return False
        if host.startswith("xn--") or ".xn--" in host:
            try:
                host.encode("ascii").decode("idna")
            except UnicodeError:
                return False
        numeric = _ipv4_host(host)
        if numeric is False:
            return False
    if port and (not port.isdigit() or str(int(port)) != port or int(port) > 65535 or port == "443"):
        return False
    path = tail.split("#", 1)[0].split("?", 1)[0]
    for segment in path.split("/"):
        if re.fullmatch(r"(?:\.|%2[eE]){1,2}", segment):
            return False
    if "?" in tail.split("#", 1)[0] and "'" in tail.split("#", 1)[0].split("?", 1)[1]:
        return False
    return True


def domain_ok(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 253:
        return False
    labels = value.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(x) for x in labels) or labels[-1].isdigit():
        return False
    return not (value == "github.io" or value.endswith(".github.io"))


def canonical_base64(value: Any) -> bytes | None:
    """The bytes of standard padded base64 that decodes and encodes back to the same text, else None."""
    if not isinstance(value, str):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if base64.b64encode(raw).decode("ascii") == value else None


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(SRI.fullmatch(value)) and canonical_base64(value[7:]) is not None


def instant(value: Any) -> datetime | None:
    """A UTC time written exactly ``YYYY-MM-DDTHH:MM:SSZ`` that is a real date and time, else None."""
    m = _TIME.fullmatch(value) if isinstance(value, str) else None
    if not m:
        return None
    try:
        return datetime(*(int(x) for x in m.groups()), tzinfo=UTC)
    except ValueError:
        return None


def _exact(value: Any, members: set[str], what: str) -> None:
    if not isinstance(value, dict) or set(value) != members:
        raise Malformed(f"{what} must have exactly {', '.join(sorted(members))}")


def _contact(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 256 and (bool(_MAILTO.fullmatch(value)) or https_url(value, 256))


def _payment(option: Any, i: int) -> None:
    if not isinstance(option, dict) or not {"network", "asset", "payTo", "amount"} <= set(option) \
            or set(option) - {"network", "asset", "payTo", "amount", "tool"}:
        raise Malformed(f"payment[{i}] has other members than network, asset, payTo, amount, tool")
    if not isinstance(option["network"], str) or not _NETWORK.fullmatch(option["network"]):
        raise Malformed(f"payment[{i}].network is not a CAIP-2 chain id")
    for member in ("asset", "payTo"):
        if not isinstance(option[member], str) or not _PRINTABLE.fullmatch(option[member]):
            raise Malformed(f"payment[{i}].{member} must be 1 to 128 printable ASCII characters")
    if re.fullmatch(r"[A-Z]{3}", option["asset"]):
        raise Malformed(f"payment[{i}].asset is a fiat currency code")
    if not isinstance(option["amount"], str) or not _AMOUNT.fullmatch(option["amount"]):
        raise Malformed(f"payment[{i}].amount must be a base 10 integer string")
    if "tool" in option:
        tool = option["tool"]
        if not isinstance(tool, str) or not 1 <= len(tool.encode("utf-16-le")) // 2 <= 128 or unsafe(tool):
            raise Malformed(f"payment[{i}].tool is not a tool name")


def check_members(doc: dict[str, Any]) -> str:
    """Check every rule of section 3 for the format ``spec`` names; returns that format."""
    spec = doc.get("spec")
    if spec not in FORMATS:
        raise Malformed(f"spec {str(spec)[:40]!r} names no known format")
    required, optional = _MEMBERS[spec]
    missing, extra = required - set(doc), set(doc) - required - optional
    if missing:
        raise Malformed(f"missing {', '.join(sorted(missing))}")
    if extra:
        raise Malformed(f"member {sorted(extra)[0]!r} is not part of {spec}")
    name = doc["name"]
    if not isinstance(name, str) or not 3 <= len(name) <= 200 or not _NAME.fullmatch(name) \
            or any(part in (".", "..") for part in name.split("/")):
        raise Malformed("name is not a registry server name")
    version = doc["version"]
    if not isinstance(version, str) or not 1 <= len(version) <= 255 or unsafe(version):
        raise Malformed("version must be 1 to 255 characters with no unsafe character")
    if spec == FORMAT_01:
        if not isinstance(doc["toolsHash"], str) or not HASH.fullmatch(doc["toolsHash"]):
            raise Malformed("toolsHash must be sha256: and 64 lowercase hex digits")
    else:
        stated = doc["toolSetDigest"]
        _exact(stated, {"spec", "contract", "extras"}, "toolSetDigest")
        if stated["spec"] != DIGEST_SPEC:
            raise Malformed(f"toolSetDigest.spec must be {DIGEST_SPEC}")
        if not _digest(stated["contract"]):
            raise Malformed("toolSetDigest.contract is not a digest in the sha256-<base64> form")
        if stated["extras"] is not None and not _digest(stated["extras"]):
            raise Malformed("toolSetDigest.extras is neither null nor a digest in the sha256-<base64> form")
    if "remotes" in doc:
        remotes = doc["remotes"]
        if not isinstance(remotes, list) or not 1 <= len(remotes) <= 16:
            raise Malformed("remotes must be an array of 1 to 16 objects")
        for i, remote in enumerate(remotes):
            _exact(remote, {"type", "url"}, f"remotes[{i}]")
            if remote["type"] not in ("streamable-http", "sse") or not https_url(remote["url"]):
                raise Malformed(f"remotes[{i}] is not a streamable-http or sse remote at an https URL")
    if "payment" in doc:
        if not isinstance(doc["payment"], list) or not 1 <= len(doc["payment"]) <= 16:
            raise Malformed("payment must be an array of 1 to 16 options")
        for i, option in enumerate(doc["payment"]):
            _payment(option, i)
    if "contact" in doc and not _contact(doc["contact"]):
        raise Malformed("contact must be a mailto address or an https URL of at most 256 characters")
    issued = instant(doc["issuedAt"])
    if issued is None:
        raise Malformed("issuedAt must be a UTC time like 2026-11-19T03:14:55Z")
    if "expiresAt" in doc:
        expires = instant(doc["expiresAt"])
        if expires is None or expires <= issued:
            raise Malformed("expiresAt must be a UTC time later than issuedAt")
    if "previous" in doc and (not isinstance(doc["previous"], str) or not HASH.fullmatch(doc["previous"])):
        raise Malformed("previous must be a declaration hash")
    key = doc["key"]
    _exact(key, {"method", "domain", "alg", "id"}, "key")
    if key["method"] not in ("dns", "http") or key["alg"] not in ("ed25519", "ecdsap384") \
            or not domain_ok(key["domain"]) or not isinstance(key["id"], str) or not HASH.fullmatch(key["id"]):
        raise Malformed("key does not name a method, a domain, an algorithm and a key id")
    if canonical_base64(doc["signature"]) is None:
        raise Malformed("signature is not canonical padded base64")
    return spec


# -- sections 6, 7 and 9: signing input, hash, published records ------------------------------------


def signing_input(doc: dict[str, Any]) -> bytes:
    """The declaration's own ``spec``, one line feed, the RFC 8785 text without ``signature``."""
    body = {k: v for k, v in doc.items() if k != "signature"}
    return str(doc["spec"]).encode("utf-8") + b"\n" + canonical_tool_bytes(body)


def declaration_hash(doc: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(signing_input(doc)).hexdigest()


@dataclass(frozen=True)
class Record:
    """One published proof record: ``v=MCPv1`` (registry login key) or ``v=ADKv1`` (declaration only)."""

    kind: str
    alg: str
    raw: bytes

    @property
    def id(self) -> str:
        return "sha256:" + hashlib.sha256(self.raw).hexdigest()


def parse_records(text: str) -> list[Record]:
    """Every proof record in one string (a TXT record, or an mcp-registry-auth body), as the registry
    reads them: ``k=`` compared exactly, ``p=`` standard padded base64, keys of the right size only."""
    out: list[Record] = []
    for kind, alg, p in _RECORD.findall(text):
        try:
            raw = base64.b64decode(p, validate=True)
        except (binascii.Error, ValueError):
            continue
        if (alg == "ed25519" and len(raw) == 32) or (alg == "ecdsap384" and len(raw) == 49 and raw[0] in (2, 3)):
            out.append(Record(kind, alg, raw))
    return out


def _signature_ok(record: Record, signature: bytes, message: bytes) -> bool:
    try:
        if record.alg == "ed25519":
            Ed25519PublicKey.from_public_bytes(record.raw).verify(signature, message)
        else:
            if len(signature) != 96:
                return False
            public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP384R1(), record.raw)
            der = encode_dss_signature(int.from_bytes(signature[:48], "big"), int.from_bytes(signature[48:], "big"))
            public.verify(der, message, ec.ECDSA(hashes.SHA384()))
    except (InvalidSignature, ValueError):
        return False
    return True


def _record_for(key: dict[str, Any], records: list[Record]) -> Record | None:
    """The record a declaration's key names: same algorithm and id; an http key only among MCPv1 records."""
    for record in records:
        if record.alg == key["alg"] and record.id == key["id"] and (key["method"] == "dns" or record.kind == "MCPv1"):
            return record
    return None


def namespace_ok(name: str, key: dict[str, Any]) -> bool:
    """Section 11, step 2: does the key's domain prove the server's namespace under its method?"""
    ns = name.split("/", 1)[0].lower()
    if ns == "io.github" or ns.startswith("io.github."):
        return False
    rev = ".".join(reversed(key["domain"].split(".")))
    return ns == rev or (key["method"] == "dns" and ns.startswith(rev + "."))


# -- section 11: verification -----------------------------------------------------------------------


@dataclass
class Result:
    state: str
    reasons: list[str] = field(default_factory=list)
    doc: dict[str, Any] | None = None
    format: str | None = None
    hash: str | None = None
    chain: dict[str, Any] | None = None
    format_accepted: bool = True


def verify(body: bytes | dict[str, Any], records: list[Record], *, now: datetime,
           previous: bytes | dict[str, Any] | None = None, formats: tuple[str, ...] | None = None,
           check_time: bool = True) -> Result:
    """Steps 1 to 5 and 7 of section 11, stopping at the first failure. *records* are the proof records
    found for ``key.domain`` by ``key.method`` (or given offline). *check_time* is off for a declaration
    that only links a newer one to the listing (section 8a: only the newest is checked against the clock)."""
    try:
        doc = parse(body) if isinstance(body, (bytes, bytearray)) else body
        if not isinstance(doc, dict):
            raise Malformed("not a JSON object")
        spec = check_members(doc)
    except Malformed as exc:
        return Result("malformed", [str(exc)])
    if formats is not None and spec not in formats:
        return Result("malformed", [f"format {spec} is not accepted here ({', '.join(formats)})"], doc=doc, format=spec,
                      format_accepted=False)
    result = Result("valid", doc=doc, format=spec, hash=declaration_hash(doc))
    key = doc["key"]
    if not namespace_ok(doc["name"], key):
        result.state, result.reasons = "name_domain_mismatch", [f"a {key['method']} key at {key['domain']} does not prove this namespace"]
        return result
    record = _record_for(key, records)
    if record is None:
        result.state, result.reasons = "key_not_found", ["the named key is not among the published records"]
        return result
    signature = canonical_base64(doc["signature"]) or b""
    if not _signature_ok(record, signature, signing_input(doc)):
        result.state, result.reasons = "invalid_signature", ["the signature does not verify under the named key"]
        return result
    if check_time:
        if instant(doc["issuedAt"]) > now + FUTURE_SKEW:
            result.state, result.reasons = "not_yet_valid", ["issuedAt is in the future"]
            return result
        if "expiresAt" in doc and instant(doc["expiresAt"]) <= now:
            result.state, result.reasons = "expired", [f"expired on {doc['expiresAt'][:10]}"]
            return result
    if previous is not None:
        problem, chain = link(doc, previous, records)
        result.chain = chain
        if problem:
            result.state, result.reasons = "chain_mismatch", [problem]
    return result


def link(doc: dict[str, Any], previous: bytes | dict[str, Any], records: list[Record]) -> tuple[str | None, dict[str, Any] | None]:
    """The link check of section 7 (with 17.5): (why it fails or None, the chain object)."""
    try:
        earlier = parse(previous) if isinstance(previous, (bytes, bytearray)) else previous
        earlier_spec = check_members(earlier)
    except Malformed as exc:
        return f"the earlier declaration is malformed: {exc}", None
    if FORMATS.index(earlier_spec) > FORMATS.index(doc["spec"]):
        return f"a {doc['spec']} declaration cannot follow a {earlier_spec} one", None
    if doc.get("previous") != declaration_hash(earlier):
        return "previous is not the hash of the earlier declaration", None
    if earlier["name"] != doc["name"]:
        return "the earlier declaration is about another server", None
    if instant(earlier["issuedAt"]) > instant(doc["issuedAt"]):
        return "the earlier declaration was issued later", None
    key, old = doc["key"], earlier["key"]
    changed = (key["alg"], key["id"]) != (old["alg"], old["id"])
    record = _record_for(old, records)
    checked = "not_checked"
    if record is not None:
        if not _signature_ok(record, canonical_base64(earlier["signature"]) or b"", signing_input(earlier)):
            return "the signature of the earlier declaration does not verify", None
        checked = "checked"
    elif not changed:
        return "the signature of the earlier declaration does not verify", None
    return None, {"previous": doc["previous"], "keyChanged": changed, "previousSignature": checked,
                  "keyLocationChanged": (key["method"], key["domain"]) != (old["method"], old["domain"]),
                  "previousFormat": earlier_spec,
                  **({"previousKey": old} if changed else {})}
