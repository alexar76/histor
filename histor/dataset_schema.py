"""Explicit scalar types for HF's batched JSON reader; no optional runtime dependencies.

An early batch can contain only nulls in a version/digest column while later rows
contain strings. Inference then fixes the column to null and rejects the later
batch. Keep types in card metadata, never rewrite the signed snapshot files.
"""

from __future__ import annotations

import re
from typing import Any


def _fields(names: str, *, integers: str = "", booleans: str = "") -> tuple[tuple[str, str], ...]:
    return tuple((name, "int64" if name in integers.split() else "bool" if name in booleans.split()
                  else "string") for name in names.split())


SCHEMAS = {
    "servers": _fields(
        "id name title description endpoint transport registry version repository website firstListed "
        "lastListed delisted skipReason lastAttempt lastStatus lastDetail lastObserved toolCount "
        "toolSetDigest subjectDigest firstPinned unchangedSince attempts observations changes "
        "blockMatches adviseMatches recordMatches classifierModel classifierFlags packageVersion "
        "toolExtrasDigest chainLabel page",
        integers="toolCount attempts observations changes blockMatches adviseMatches recordMatches classifierFlags",
        booleans="delisted",
    ),
    "toolsets": _fields("digest toolsJson"),
    "changes": _fields(
        "id serverId observedAt fromToolSetDigest toToolSetDigest fromSubjectDigest toSubjectDigest "
        "fromPackageVersion toPackageVersion fromServerVersion toServerVersion fromRegistryVersion "
        "toRegistryVersion labelId summaryJson", integers="id",
    ),
    "scans": _fields("toolSetDigest patternSet matchesJson"),
    "classifications": _fields("toolSetDigest model updatedAt verdictJson"),
    "tool_extras": _fields("digest extrasJson"),
    "extras_changes": _fields("id serverId observedAt fromExtrasDigest toExtrasDigest summaryJson", integers="id"),
    "package_observations": _fields(
        "id serverId observedAt status packageVersion toolSetDigest subjectDigest toolExtrasDigest toolCount "
        "serverName serverVersion registryVersion launchJson protocolVersion", integers="id toolCount",
    ),
}


def hf_metadata(files: dict[str, Any]) -> str:
    """HF configs and their declared Features for the nonempty tables in a manifest.

    Empty raw files remain downloadable and signed, but the JSON loader cannot
    materialize a train split with zero rows, even with declared Features.
    """
    unknown = {name for name in files if name.endswith(".jsonl.gz") and name[:-9] not in SCHEMAS}
    if unknown:
        raise ValueError(f"No HF scalar schema for: {', '.join(sorted(unknown))}")
    names = [name for name in SCHEMAS if files.get(name + ".jsonl.gz", {}).get("rows", 0) > 0]
    if not names:
        return "configs: []\ndataset_info: []"
    configs = "\n".join(f"- config_name: {name}\n  data_files: {name}.jsonl.gz" for name in names)
    infos = []
    for name in names:
        fields = "\n".join(f"  - name: {field}\n    dtype: {dtype}" for field, dtype in SCHEMAS[name])
        infos.append(f"- config_name: {name}\n  features:\n{fields}")
    return "configs:\n" + configs + "\ndataset_info:\n" + "\n".join(infos)


def with_hf_metadata(card: str, files: dict[str, Any]) -> str:
    """Refresh only configs/dataset_info in a HISTOR card, preserving its prose.

    This also repairs cards from older exporter images. The known top-level YAML
    block boundaries are retained verbatim; no YAML dependency or serialization
    of unrelated author metadata is needed on the exporter/uploader host.
    """
    if not card.startswith("---\n") or "\n---\n" not in card[4:]:
        raise ValueError("HISTOR dataset card must begin with YAML front matter")
    metadata, prose = card[4:].split("\n---\n", 1)
    lines = metadata.splitlines()
    keys = [i for i, line in enumerate(lines) if re.match(r"^[A-Za-z_][\w-]*:", line)]
    remove: set[int] = set()
    for i, start in enumerate(keys):
        if lines[start].split(":", 1)[0] in {"configs", "dataset_info"}:
            remove.update(range(start, keys[i + 1] if i + 1 < len(keys) else len(lines)))
    retained = "\n".join(line for i, line in enumerate(lines) if i not in remove).rstrip()
    return "---\n" + retained + "\n" + hf_metadata(files) + "\n---\n" + prose
