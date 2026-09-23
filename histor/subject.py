"""MTL/1 subject construction — PROFILE.md sections 4 and 5, over the AWR/2 reference canonicalizer.

This is the normative digest computation of ``awr/adoption/mcp-trust-label/tools/mtl_subject.py``,
carried here so HISTOR installs without the monorepo. ``tests/test_subject_parity.py`` runs both
over the same inputs and fails if they ever disagree: a digest that drifts from the profile's own
tool surfaces to a registry as spurious drift on every server at once.

Two digests:

* the **tool-set digest** — SHA-256 over the RFC 8785 form of the normalised, code-unit-sorted
  tool array (section 5);
* the **subject digest** — SHA-256 over the RFC 8785 form of the MCP Server Descriptor (section 4).

Neither is WARDEN's ``canonicalToolsHash``: that one drops ``outputSchema``, so a server could
change its output contract without moving it (PROFILE.md section 5.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from awr import AwrError, canonical_sri, canonicalize

from histor.untrusted import clean_text

MTL_SUBJ_001 = "MTL-SUBJ-001"  # tool entry is not an object, or name missing/empty
MTL_SUBJ_002 = "MTL-SUBJ-002"  # tool set is empty
MTL_SUBJ_003 = "MTL-SUBJ-003"  # duplicate tool name
MTL_NUM_001 = "MTL-NUM-001"  # non-integer JSON number in a tool schema: not digestible

REGISTRY_OFFICIAL = "urn:awr:mtl:1:registry:registry.modelcontextprotocol.io"


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


def canonical_tool_set(tools: list[Any]) -> list[dict[str, Any]]:
    """The normalised, sorted array MTL/1 digests. Raises on empty sets and duplicate names."""
    if not tools:
        raise MtlError(MTL_SUBJ_002, "the tool set is empty; there is nothing to pin")
    entries = [normalise_tool(tool) for tool in tools]
    seen: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if entry["name"] in seen:
            raise MtlError(
                MTL_SUBJ_003,
                f"duplicate tool name {entry['name']!r} at positions {seen[entry['name']]} and {index}",
            )
        seen[entry["name"]] = index
    entries.sort(key=lambda e: utf16_sort_key(e["name"]))
    return entries


def tool_set_digest(tools: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    """``(sri, canonical_entries)``. ``MTL-NUM-001`` when a schema carries a non-integer number."""
    entries = canonical_tool_set(tools)
    try:
        sri = canonical_sri(entries)
    except AwrError as exc:
        culprit = _first_uncanonicalizable(entries)
        where = f" in tool {culprit!r}" if culprit is not None else ""
        if exc.code in ("AWR-CANON-001", "AWR-CANON-002"):
            # 001 is a fraction or exponent, 002 an integer outside the interoperable range.
            # Both are numbers two implementations may serialise differently, which is the
            # exact case section 5.4 withholds the digest for. 5.4 also asks the reason to name
            # the tool, so a server author can find it.
            raise MtlError(
                MTL_NUM_001,
                f"a tool schema{where} contains a JSON number MTL/1 cannot digest reproducibly ({exc.code})",
            ) from exc
        raise MtlError(MTL_SUBJ_001, f"a tool entry{where} is not canonicalizable ({exc.code})") from exc
    return sri, entries


def _first_uncanonicalizable(entries: list[dict[str, Any]]) -> str | None:
    for entry in entries:
        try:
            canonicalize(entry)
        except AwrError:
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
    hazard: MtlError | None  # set when the tool set is not digestible (MTL-NUM-001)

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
    """Build the MSD. Raises :class:`MtlError` for SUBJ-001/002/003; NUM-001 becomes ``hazard``.

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
                     endpoint: str | None = None) -> Subject:
    """A descriptor for a tool set that failed SUBJ-001/002/003: names only, never a digest.

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
    artifact = {k: v for k, v in (("transport", transport), ("endpoint", endpoint)) if v}
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
