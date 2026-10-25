#!/usr/bin/env bash
# Mirror a HISTOR snapshot (deploy/export_dataset.sh) to a Hugging Face dataset repository.
# Installed as /usr/local/sbin/histor-upload-hf; histor-export-dataset calls it after each new
# snapshot when it is set up, and it can be run by hand:
#
#   histor-upload-hf                                  the latest snapshot
#   histor-upload-hf /var/lib/histor-datasets/2026-10-20
#
# Setup (once, on the host): the repository id in /etc/histor-hf/repo (e.g. modelmarket/histor-mcp-tool-definitions)
# and a write token in /etc/histor-hf/token, both root-only. The token never leaves that file: it
# is read by the uploader, not passed on a command line or printed. The library lives in its own
# venv at /opt/histor-hf/venv (huggingface_hub pinned below).
#
# Each upload replaces the files at the repository root (the dataset card is the snapshot's
# README.md) and tags the commit snapshot-<date>, so every snapshot stays citable by revision.
set -euo pipefail

CONF="${HISTOR_HF_CONF:-/etc/histor-hf}"
VENV="${HISTOR_HF_VENV:-/opt/histor-hf/venv}"
HUB_VERSION="2.1.1"
SNAP="${1:-/var/lib/histor-datasets/latest}"

[[ -r "$CONF/repo" && -r "$CONF/token" ]] || { echo "not set up: $CONF/repo and $CONF/token" >&2; exit 1; }
[[ -f "$SNAP/manifest.json" ]] || { echo "no snapshot at $SNAP" >&2; exit 1; }
if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c "import huggingface_hub" 2> /dev/null; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q "huggingface_hub==$HUB_VERSION"
fi

"$VENV/bin/python" - "$CONF" "$(readlink -f "$SNAP")" <<'PY'
import json
import sys
from pathlib import Path

from huggingface_hub import HfApi

conf, snap = Path(sys.argv[1]), Path(sys.argv[2])
repo = (conf / "repo").read_text().strip()
api = HfApi(token=(conf / "token").read_text().strip())
manifest = json.loads((snap / "manifest.json").read_text())
day = manifest["createdAt"][:10]
api.create_repo(repo, repo_type="dataset", exist_ok=True)
files = sorted(p.name for p in snap.iterdir() if p.is_file())
api.upload_folder(
    folder_path=str(snap), repo_id=repo, repo_type="dataset", allow_patterns=files,
    delete_patterns=["*.jsonl.gz", "*.json"],
    commit_message=f"HISTOR snapshot {day}",
    commit_description=f"Tree head {manifest.get('treeHead', {}).get('treeSize')} · manifest signed by {manifest['log']}",
)
api.create_tag(repo, tag=f"snapshot-{day}", repo_type="dataset", exist_ok=True)
print(f"https://huggingface.co/datasets/{repo} ← {len(files)} files, tag snapshot-{day}")
PY
