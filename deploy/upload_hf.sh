#!/usr/bin/env bash
# Mirror a HISTOR snapshot (deploy/export_dataset.sh) to a Hugging Face dataset repository.
# Installed as /usr/local/sbin/histor-upload-hf; histor-export-dataset calls it after each new
# snapshot when it is set up, and it can be run by hand:
#
#   histor-upload-hf                                  the latest snapshot
#   histor-upload-hf /var/lib/histor-datasets/2026-10-20
#   histor-upload-hf --card-only                      update only the live dataset card
#
# Setup (once, on the host): the repository id in /etc/histor-hf/repo (e.g. modelmarket/histor-mcp-tool-definitions)
# and a write token in /etc/histor-hf/token, both root-only. The token never leaves that file: it
# is read by the uploader, not passed on a command line or printed. The library lives in its own
# venv at /opt/histor-hf/venv (huggingface_hub pinned below).
#
# Each snapshot upload adds the Hugging Face entry link to a copy of the snapshot's README
# and tags the commit snapshot-<date>, so every snapshot stays citable by revision.
set -euo pipefail

CONF="${HISTOR_HF_CONF:-/etc/histor-hf}"
VENV="${HISTOR_HF_VENV:-/opt/histor-hf/venv}"
SCHEMA_MODULE="${HISTOR_HF_SCHEMA_MODULE:-/usr/local/lib/histor-dataset/dataset_schema.py}"
HUB_VERSION="2.1.1"
SNAP="${1:-/var/lib/histor-datasets/latest}"

[[ -r "$CONF/repo" && -r "$CONF/token" ]] || { echo "not set up: $CONF/repo and $CONF/token" >&2; exit 1; }
[[ -r "$SCHEMA_MODULE" ]] || { echo "missing dataset schema: $SCHEMA_MODULE" >&2; exit 1; }
if [[ "$SNAP" != --card-only ]]; then
  [[ -f "$SNAP/manifest.json" ]] || { echo "no snapshot at $SNAP" >&2; exit 1; }
  SNAP="$(readlink -f "$SNAP")"
fi
if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c "import huggingface_hub" 2> /dev/null; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q "huggingface_hub==$HUB_VERSION"
fi

"$VENV/bin/python" - "$CONF" "$SNAP" "$SCHEMA_MODULE" <<'PY'
import importlib.util
import json
import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path

from huggingface_hub import HfApi

spec = importlib.util.spec_from_file_location("histor_dataset_schema", sys.argv[3])
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)

CARD_LINK = "https://histor.modelmarket.dev/servers?from=hf-dataset"
CARD_SECTION = f"""## Find your MCP package in the live log

[Open the HISTOR package search]({CARD_LINK}). Enter the package name, or use
`npm:mongodb-mcp-server` or `pypi:ipinfo-mcp-server` to select a registry.
Open the matching record to see the tool definitions HISTOR observed and their
change history. The browser view works without downloading this dataset.

"""


def linked_card(card: str) -> str:
    if CARD_LINK in card:
        return card
    heading = "\n## Files\n"
    if card.count(heading) != 1:
        raise ValueError("HISTOR dataset card must contain a single Files heading")
    return card.replace(heading, "\n" + CARD_SECTION + "## Files\n", 1)


conf, source = Path(sys.argv[1]), sys.argv[2]
repo = (conf / "repo").read_text().strip()
api = HfApi(token=(conf / "token").read_text().strip())

if source == "--card-only":
    revision = api.dataset_info(repo_id=repo).sha
    if not revision:
        raise RuntimeError("Hugging Face did not return a dataset revision")
    card_path = api.hf_hub_download(repo_id=repo, filename="README.md", repo_type="dataset", revision=revision)
    original = Path(card_path).read_text(encoding="utf-8")
    manifest_path = api.hf_hub_download(repo_id=repo, filename="manifest.json", repo_type="dataset", revision=revision)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    updated = schema.with_hf_metadata(linked_card(original), manifest["files"])
    if updated != original:
        commit = api.upload_file(
            path_or_fileobj=updated.encode("utf-8"), path_in_repo="README.md",
            repo_id=repo, repo_type="dataset", parent_commit=revision,
            commit_message="Declare nullable column types for the HISTOR dataset viewer",
        )
        revision = commit.oid
    print(f"https://huggingface.co/datasets/{repo} · card revision {revision}")
    sys.exit(0)

snap = Path(source)
manifest = json.loads((snap / "manifest.json").read_text())
day = manifest["createdAt"][:10]
api.create_repo(repo, repo_type="dataset", exist_ok=True)
files = sorted(p.name for p in snap.iterdir() if p.is_file())
with TemporaryDirectory(prefix=".hf-upload-", dir=snap.parent) as temporary:
    stage = Path(temporary)
    for name in files:
        if name == "README.md":
            card = schema.with_hf_metadata(linked_card((snap / name).read_text(encoding="utf-8")), manifest["files"])
            (stage / name).write_text(card, encoding="utf-8")
        else:
            os.link(snap / name, stage / name)
    commit = api.upload_folder(
        folder_path=str(stage), repo_id=repo, repo_type="dataset", allow_patterns=files,
        delete_patterns=["*.jsonl.gz", "*.json"],
        commit_message=f"HISTOR snapshot {day}",
        commit_description=f"Tree head {manifest.get('treeHead', {}).get('treeSize')} · manifest signed by {manifest['log']}",
    )
api.create_tag(repo, tag=f"snapshot-{day}", repo_type="dataset", revision=commit.oid, exist_ok=True)
print(f"https://huggingface.co/datasets/{repo} ← {len(files)} files, tag snapshot-{day}")
PY
