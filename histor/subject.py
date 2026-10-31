"""MTL/1 subject construction — PROFILE.md sections 4 and 5, over the AWR/2 reference canonicalizer.

This is the normative digest computation of ``awr/adoption/mcp-trust-label/tools/mtl_subject.py``,
carried here so HISTOR installs without the monorepo. ``tests/test_subject_parity.py`` runs both
over the same inputs and fails if they ever disagree: a digest that drifts from the profile's own
tool surfaces to a registry as spurious drift on every server at once.

Two digests:

* the **tool-set digest** — SHA-256 over the RFC 8785 form of the normalised, code-unit-sorted
  tool array (section 5), every JSON number read as an IEEE 754 double (MTL/1.1, section 5.4);
* the **subject digest** — SHA-256 over the RFC 8785 form of the MCP Server Descriptor (section 4).

Neither is WARDEN's ``canonicalToolsHash``: that one drops ``outputSchema``, so a server could
change its output contract without moving it (PROFILE.md section 5.5).
"""

from __future__ import annotations

import math
from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from awr import AwrError, canonical_sri, canonicalize

from histor.untrusted import clean_text

MTL_SUBJ_001 = "MTL-SUBJ-001"  # tool entry is not an object, or name missing/empty
# MTL/1.0 refused empty sets (SUBJ-002) and duplicate names (SUBJ-003); 1.1 digests both. The
# codes stay so labels issued before 2026-10-31 still read.
MTL_SUBJ_002 = "MTL-SUBJ-002"
MTL_SUBJ_003 = "MTL-SUBJ-003"
MTL_NUM_001 = "MTL-NUM-001"  # a number with no IEEE 754 double (1e400): not digestible

MAX_SAFE_INTEGER = 2**53 - 1

REGISTRY_OFFICIAL = "urn:awr:mtl:1:registry:registry.modelcontextprotocol.io"
# HISTOR's own list of popular servers the official registry does not carry (histor/curated.json).
# A separate namespace, so a label never says the registry assigned a name it did not.
REGISTRY_CURATED = "urn:awr:mtl:1:registry:histor.modelmarket.dev/curated"


class MtlError(Exception):
    """A tool set or descriptor that MTL/1 cannot digest."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def utf16_sort_key(text: str) -> bytes:
    """RFC 8785 section 3.2.3 order: UTF-16 code units compared as unsigned integers.

    Big-endian UTF-16 bytes compare lexicographically exactly like the code-unit sequence.
    Not ``str.__lt__`` (code points, diverges outside the BMP) and never a locale collation.
    """
    return text.encode("utf-16-be", "surrogatepass")


def normalise_tool(tool: Any) -> dict[str, Any]:
    """One tool object reduced to the four members MTL/1 digests (section 5.2)."""
    if not isinstance(tool, dict):
        raise MtlError(MTL_SUBJ_001, "tool entry is not a JSON object")
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise MtlError(MTL_SUBJ_001, "tool entry has no non-empty string name")
    entry: dict[str, Any] = {
        "name": name,
        "description": tool["description"] if isinstance(tool.get("description"), str) else "",
        "inputSchema": tool["inputSchema"] if isinstance(tool.get("inputSchema"), dict) else {},
    }
    # Present iff the server sent an object: absent and {} are different output contracts.
    if isinstance(tool.get("outputSchema"), dict):
        entry["outputSchema"] = tool["outputSchema"]
    return entry


def es_number(value: float) -> str:
    """RFC 8785 section 3.2.2.3: a double in ECMAScript ``Number.prototype.toString`` form.

    ``repr`` gives the shortest digits that round-trip, which is the digit string ECMAScript picks;
    only the placement of the point and the exponent differ. Checked against Node's
    ``JSON.stringify`` on 399,880 doubles (random bit patterns and schema-like decimals), 0 apart.
    """
    if not math.isfinite(value):
        raise ValueError(f"{value!r} has no JSON form")
    if value == 0:
        return "0"  # -0 included
    sign = "-" if value < 0 else ""
    mantissa, _, exp = repr(abs(value)).partition("e")
    whole, _, frac = mantissa.partition(".")
    digits = whole + frac
    lead = len(digits) - len(digits.lstrip("0"))
    s = digits.strip("0")
    k = len(s)
    n = len(whole) + (int(exp) if exp else 0) - lead  # value = 0.s × 10^n
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + s
    e = n - 1
    return sign + (s if k == 1 else s[0] + "." + s[1:]) + "e" + ("+" if e >= 0 else "-") + str(abs(e))


def _serialize(value: Any, out: list[str]) -> None:
    # Strings and names go through AWR/2's own serializer, so escaping and the surrogate checks are
    # the canonicalizer's, not a second copy of them.
    if value is None or isinstance(value, bool):
        out.append("null" if value is None else ("true" if value else "false"))
    elif isinstance(value, int):
        # Beyond 2^53-1 a JSON parser in JavaScript already holds the nearest double, so that is
        # what is digested (float() rounds to nearest, ties to even, as JSON.parse does).
        out.append(str(value) if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else es_number(float(value)))
    elif isinstance(value, float):
        out.append(es_number(value))
    elif isinstance(value, str):
        out.append(canonicalize(value).decode("utf-8"))
    elif isinstance(value, list):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _serialize(item, out)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        for index, key in enumerate(sorted(value, key=utf16_sort_key)):
            if index:
                out.append(",")
            out.append(canonicalize(key).decode("utf-8"))
            out.append(":")
            _serialize(value[key], out)
        out.append("}")
    else:
        raise TypeError(f"{type(value).__name__} is not a JSON value")


def canonical_tool_bytes(value: Any) -> bytes:
    """RFC 8785 bytes of a tool set or entry, every JSON number read as an IEEE 754 double.

    A value with integers only inside ±(2^53-1) gets AWR/2's own bytes, so every digest issued
    before MTL/1.1 is unchanged; only what 1.0 refused (fractions, exponents, larger integers)
    takes the second path.
    """
    try:
        return canonicalize(value)
    except AwrError as exc:
        if exc.code not in ("AWR-CANON-001", "AWR-CANON-002"):
            raise
    out: list[str] = []
    _serialize(value, out)
    return "".join(out).encode("utf-8")


def canonical_tool_set(tools: list[Any]) -> list[dict[str, Any]]:
    """The normalised, sorted array MTL/1 digests: may be empty, may repeat a name.

    Duplicate names are ordered by their canonical bytes after the name, so the digest still
    does not depend on the order the server listed them in (section 5.3).
    """
    entries = [normalise_tool(tool) for tool in tools]
    if len({entry["name"] for entry in entries}) == len(entries):
        entries.sort(key=lambda e: utf16_sort_key(e["name"]))
    else:
        entries.sort(key=lambda e: (utf16_sort_key(e["name"]), canonical_tool_bytes(e)))
    return entries


def tool_set_digest(tools: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """``(sri, canonical_entries)``. ``MTL-NUM-001`` only for a number with no double (1e400)."""
    entries = canonical_tool_set(tools)
    try:
        canonical = canonical_tool_bytes(entries)
    except (ValueError, OverflowError) as exc:
        culprit = _first_uncanonicalizable(entries)
        where = f" in tool {culprit!r}" if culprit is not None else ""
        # Section 5.4 asks the reason to name the tool, so a server author can find it.
        raise MtlError(MTL_NUM_001, f"a tool schema{where} contains a number with no IEEE 754 double") from exc
    except (AwrError, TypeError) as exc:
        culprit = _first_uncanonicalizable(entries)
        where = f" in tool {culprit!r}" if culprit is not None else ""
        code = getattr(exc, "code", type(exc).__name__)
        raise MtlError(MTL_SUBJ_001, f"a tool entry{where} is not canonicalizable ({code})") from exc
    return "sha256-" + b64encode(sha256(canonical).digest()).decode("ascii"), entries


def _first_uncanonicalizable(entries: list[dict[str, Any]]) -> str | None:
    for entry in entries:
        try:
            canonical_tool_bytes(entry)
        except (AwrError, TypeError, ValueError, OverflowError):
            return entry["name"]
    return None


@dataclass(frozen=True)
class Subject:
    """An MCP Server Descriptor and everything derived from it."""

    descriptor: dict[str, Any]
    digest: str  # subject digest, SRI
    urn: str
    tool_set_digest: str | None
    entries: list[dict[str, Any]] | None
    hazard: MtlError | None  # set when a number has no double (MTL-NUM-001): no digest

    @property
    def names(self) -> list[str]:
        return list(self.descriptor["toolSet"]["names"])

    @property
    def count(self) -> int:
        return int(self.descriptor["toolSet"]["count"])

    def reference(self) -> dict[str, str]:
        """The ``verifiedWork`` digest reference a label carries (section 4.5)."""
        return {"id": self.urn, "digestSRI": self.digest}


def build_subject(
    *,
    server_name: str,
    registry: str,
    tools: list[Any],
    server_version: str | None = None,
    transport: str | None = None,
    package: str | None = None,
    endpoint: str | None = None,
) -> Subject:
    """Build the MSD. Raises :class:`MtlError` for SUBJ-001; NUM-001 becomes ``hazard``.

    Optional members are omitted, never ``null`` (section 4.3): presence changes the bytes.
    """
    if not isinstance(server_name, str) or not server_name:
        raise MtlError(MTL_SUBJ_001, "server.name must be a non-empty string")
    if not isinstance(registry, str) or not registry:
        raise MtlError(MTL_SUBJ_001, "server.registry must be a non-empty string")

    hazard: MtlError | None = None
    sri: str | None = None
    entries: list[dict[str, Any]] | None = None
    try:
        sri, entries = tool_set_digest(tools)
    except MtlError as exc:
        if exc.code != MTL_NUM_001:
            raise
        hazard = exc

    names = sorted((normalise_tool(t)["name"] for t in tools), key=utf16_sort_key)
    server: dict[str, Any] = {"name": server_name, "registry": registry}
    if server_version:
        server["version"] = server_version
    tool_set: dict[str, Any] = {"count": len(names), "names": names}
    if sri is not None:
        tool_set["digestSRI"] = sri
    descriptor: dict[str, Any] = {"mtl": "1", "server": server, "toolSet": tool_set}
    artifact: dict[str, Any] = {}
    if transport:
        artifact["transport"] = transport
    if package:
        artifact["package"] = package
    if endpoint:
        artifact["endpoint"] = endpoint
    if artifact:
        descriptor["artifact"] = artifact

    return Subject(
        descriptor=descriptor,
        digest=canonical_sri(descriptor),
        urn="urn:awr:mtl:1:subject:sha256:" + sha256(canonicalize(descriptor)).hexdigest(),
        tool_set_digest=sri,
        entries=entries,
        hazard=hazard,
    )


def fallback_subject(*, server_name: str, registry: str, tools: list[Any], transport: str | None = None,
                     endpoint: str | None = None, package: str | None = None) -> Subject:
    """A descriptor for a tool set that failed SUBJ-001: names only, never a digest.

    The label over it is ``inconclusive`` with the reason; the descriptor exists so that label
    still has a subject to point at (section 4.2 makes ``toolSet.digestSRI`` absent, not faked).
    """
    # Names as the server sent them, except that NUL and unpaired surrogates (which the
    # canonicalizer rightly refuses) become U+FFFD: this descriptor must always be buildable.
    names = sorted(
        (clean_text(t["name"]) for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]),
        key=utf16_sort_key,
    )
    descriptor: dict[str, Any] = {
        "mtl": "1",
        "server": {"name": clean_text(server_name), "registry": clean_text(registry)},
        "toolSet": {"count": len(tools), "names": names},
    }
    artifact = {k: v for k, v in (("transport", transport), ("package", package), ("endpoint", endpoint)) if v}
    if artifact:
        descriptor["artifact"] = artifact
    return Subject(
        descriptor=descriptor,
        digest=canonical_sri(descriptor),
        urn="urn:awr:mtl:1:subject:sha256:" + sha256(canonicalize(descriptor)).hexdigest(),
        tool_set_digest=None,
        entries=None,
        hazard=None,
    )
