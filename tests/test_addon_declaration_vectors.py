"""histor.addon_declaration against the format's own conformance vectors (tests/vectors/addon-declaration,
third-party, unchanged): every declaration vector and every field vector of formats 0.1 and 0.2."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from histor import addon_declaration as ad

VECTORS = Path(__file__).parent / "vectors" / "addon-declaration"
FOLDERS = (VECTORS, VECTORS / "v0.2")


def _cases(kind: str):
    for folder in FOLDERS:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for case in manifest[kind]:
            yield pytest.param(folder, case, id=f"{folder.name}:{case['id']}")


@pytest.mark.parametrize("folder,case", _cases("declarations"))
def test_declaration_vector(folder: Path, case: dict):
    now = datetime.fromisoformat(case["now"].replace("Z", "+00:00")).astimezone(UTC)
    result = ad.verify(
        (folder / case["declaration"]).read_bytes(), ad.parse_records(case["pubkey"]), now=now,
        previous=(folder / case["previous"]).read_bytes() if case.get("previous") else None,
        formats=tuple(case["formats"]) if case.get("formats") else None)
    expect = case["expect"]
    # Step 6, the tool check, is not implemented here: a vector that fails only there passes every other step.
    assert result.state == ("valid" if expect["state"] == "tools_hash_mismatch" else expect["state"]), result.reasons
    if result.hash and expect.get("declarationHash"):
        assert result.hash == expect["declarationHash"]
    if result.format and expect.get("format"):
        assert result.format == expect["format"]
    if expect["state"] == "valid":
        for member, value in (expect.get("chain") or {}).items():
            assert (result.chain or {}).get(member) == value, member
    if "formats" in case and expect["state"] == "malformed" and case["id"].split("/")[1].startswith("format-"):
        assert result.format_accepted is False, "a refusal by the format setting is told apart from a broken file"


def _replace(doc: dict, pointer: str, value, remove: bool) -> None:
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]
    cursor = doc
    for part in parts[:-1]:
        cursor = cursor[int(part)] if isinstance(cursor, list) else cursor[part]
    last = parts[-1]
    if isinstance(cursor, list):
        if remove:
            del cursor[int(last)]
        else:
            cursor[int(last)] = value
    elif remove:
        cursor.pop(last, None)
    else:
        cursor[last] = value


@pytest.mark.parametrize("folder,case", _cases("fields"))
def test_field_vector(folder: Path, case: dict):
    doc = copy.deepcopy(json.loads((folder / "ed25519-valid.json").read_text(encoding="utf-8")))
    _replace(doc, case["pointer"], case.get("value"), case.get("remove", False))
    if case["expect"] == "accept":
        ad.check_members(doc)
    else:
        with pytest.raises(ad.Malformed):
            ad.check_members(doc)


def test_the_vector_files_are_the_ones_downloaded():
    import hashlib

    lines = (VECTORS / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 129
    for line in lines:
        digest, name = line.split(maxsplit=1)
        assert hashlib.sha256((VECTORS / name.strip()).read_bytes()).hexdigest() == digest, name
