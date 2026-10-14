"""Tool sets a publisher declares in its registry entry.

A package's tool set can depend on the machine it runs on. A launcher hands stdio to a program
installed next to it and lists that program's tools there, while in HISTOR's sandbox, where the
program is missing, it lists one "install me" tool. A real key can unlock tools a placeholder does
not. HISTOR observes one of those sets; the publisher knows the others.

So a publisher may name them, by MTL/1 tool-set digest, in the server.json it publishes to the
official MCP registry, under the field the registry keeps for downstream registries::

    "_meta": {
      "io.modelcontextprotocol.registry/publisher-provided": {
        "dev.modelmarket.histor/tool-sets": {
          "mtl": "1",
          "sets": [{"digest": "sha256-…", "count": 23, "when": "the Auten runner is installed"}]
        }
      }
    }

The registry authenticates the namespace (``ai.auten/*`` was proven by its DNS or GitHub owner),
so the declaration is the publisher's, not a stranger's. It stays the publisher's word: HISTOR
records it with the dates it read it and answers /check with it, and never counts it as an
observation, never issues a label about it, never reads the tools behind it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from histor.mcpclient import MAX_TOOLS

PUBLISHER_PROVIDED = "io.modelcontextprotocol.registry/publisher-provided"
FIELD = "dev.modelmarket.histor/tool-sets"
MAX_SETS = 8        # the registry keeps ~4 KB of publisher-provided metadata; 8 sets fit with room
MAX_WHEN = 200      # a condition, not a document
SRI = re.compile(r"^sha256-[A-Za-z0-9+/]{43}=$")


def _when(value: Any) -> str | None:
    """The condition, as one line of visible text: control and format characters (zero-width,
    bidi overrides) are dropped, so what a page shows is what the publisher wrote."""
    if not isinstance(value, str):
        return None
    text = "".join(" " if ch in "\t\r\n" else ch for ch in value
                   if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn") or ch in "\t\r\n")
    text = " ".join(text.split())
    return text[:MAX_WHEN] or None


def declared_tool_sets(server: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(sets, ignored) from one server.json. A set is ``{"digest", "count"?, "when"?}``; *ignored*
    says, per entry or for the whole field, why HISTOR did not take it."""
    meta = server.get("_meta") if isinstance(server.get("_meta"), dict) else {}
    provided = meta.get(PUBLISHER_PROVIDED) if isinstance(meta.get(PUBLISHER_PROVIDED), dict) else {}
    if FIELD not in provided:
        return [], []
    block = provided[FIELD]
    if not isinstance(block, dict):
        return [], [f"{FIELD} is not an object"]
    if block.get("mtl") != "1":
        return [], ['"mtl" must be "1": the digests are MTL/1 tool-set digests']
    entries = block.get("sets")
    if not isinstance(entries, list) or not entries:
        return [], ['"sets" must be a non-empty list']
    sets: list[dict[str, Any]] = []
    ignored: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if len(sets) == MAX_SETS:
            ignored.append(f"sets[{i}:]: more than {MAX_SETS} sets")
            break
        if not isinstance(entry, dict):
            ignored.append(f"sets[{i}]: not an object")
            continue
        digest = entry.get("digest")
        if not isinstance(digest, str) or not SRI.match(digest):
            ignored.append(f"sets[{i}]: digest is not an MTL/1 tool-set digest (sha256-<base64>)")
            continue
        if digest in seen:
            ignored.append(f"sets[{i}]: the same digest twice")
            continue
        count = entry.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_TOOLS):
            ignored.append(f"sets[{i}]: count must be an integer from 0 to {MAX_TOOLS}")
            continue
        seen.add(digest)
        out: dict[str, Any] = {"digest": digest}
        if count is not None:
            out["count"] = count
        when = _when(entry.get("when"))
        if when:
            out["when"] = when
        sets.append(out)
    return sets, ignored


def public(row: dict[str, Any]) -> dict[str, Any]:
    """A stored declaration as the API and /check show it."""
    return {k: v for k, v in (
        ("toolSetDigest", row["toolset"]),
        ("toolCount", row["tool_count"]),
        ("when", row["when_text"]),
        ("registryVersion", row["registry_version"]),
        ("firstDeclared", row["first_declared"]),
        ("lastDeclared", row["last_declared"]),
        ("withdrawn", row["withdrawn_at"]),
    ) if v is not None}
