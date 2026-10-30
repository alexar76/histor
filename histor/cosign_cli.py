"""``python -m histor witness-submit`` — get a log's checkpoint co-signed by a witness.

    python -m histor witness-submit --log https://histor.modelmarket.dev \\
        --witness https://witness.example/witness/v1 [--state ~/.histor/witness-state.json] \\
        [--submit-to-log] [--operator-token-env HISTOR_OPERATOR_TOKEN]

Works for any log that serves a C2SP checkpoint at ``<log>/checkpoint`` and RFC 9162
consistency proofs at ``<log>/api/v1/log/proof/consistency``, and for any witness that speaks
c2sp.org/tlog-witness. It keeps the size the witness last co-signed (a 409 tells it), fetches
the consistency proof from that size, posts ``old <size>`` + proof + checkpoint, and prints the
cosignature lines. With ``--submit-to-log`` it also stores them in the log
(``POST /api/v1/log/cosignatures``, operator token from the named environment variable), so
the log serves its checkpoint with the witness's line on it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import httpx

from histor.checkpoint import parse_checkpoint_text, parse_note, parse_signature_line


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="histor witness-submit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", required=True, help="log base URL")
    ap.add_argument("--witness", required=True, help="witness submission prefix (ends with /witness/v1 for HISTOR)")
    ap.add_argument("--state", default="", help="JSON file remembering the size the witness last co-signed")
    ap.add_argument("--submit-to-log", action="store_true", help="store the cosignature lines in the log")
    ap.add_argument("--operator-token-env", default="HISTOR_OPERATOR_TOKEN")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    log = args.log.rstrip("/")
    witness = args.witness.rstrip("/")
    state_path = Path(args.state).expanduser() if args.state else None
    state = json.loads(state_path.read_text()) if state_path and state_path.is_file() else {}
    old = int(state.get(witness, 0))

    with httpx.Client(timeout=args.timeout, follow_redirects=False) as http:
        note_text = http.get(f"{log}/checkpoint").raise_for_status().text
        note = parse_note(note_text)
        cp = parse_checkpoint_text(note.text)
        for attempt in range(2):
            proof: list[str] = []
            if 0 < old < cp.tree_size:
                resp = http.get(f"{log}/api/v1/log/proof/consistency", params={"first": old, "second": cp.tree_size})
                resp.raise_for_status()
                proof = [base64.b64encode(bytes.fromhex(h)).decode() for h in resp.json()["proof"]]
            if old > cp.tree_size:
                print(f"the witness is at {old}, ahead of the log's checkpoint {cp.tree_size}; nothing to do", file=sys.stderr)
                return 0
            body = f"old {old}\n" + "".join(f"{h}\n" for h in proof) + "\n" + note_text
            resp = http.post(f"{witness}/add-checkpoint", content=body.encode(),
                             headers={"Content-Type": "text/plain; charset=utf-8"})
            if resp.status_code == 409 and attempt == 0:
                old = int(resp.text.strip() or "0")
                continue
            break
        if resp.status_code != 200:
            print(f"witness answered {resp.status_code}: {resp.text.strip()[:200]}", file=sys.stderr)
            return 1
        lines = [line for line in resp.text.split("\n") if line]
        for line in lines:
            parse_signature_line(line)  # refuse to store junk
        print("".join(line + "\n" for line in lines), end="")
        if state_path:
            state[witness] = cp.tree_size
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2) + "\n")
        if args.submit_to_log:
            token = os.environ.get(args.operator_token_env, "")
            if not token:
                print(f"{args.operator_token_env} is not set", file=sys.stderr)
                return 2
            resp = http.post(f"{log}/api/v1/log/cosignatures", json={"treeSize": cp.tree_size, "lines": lines},
                             headers={"X-Histor-Operator": token})
            if resp.status_code != 200:
                print(f"the log refused the cosignatures: {resp.status_code} {resp.text.strip()[:200]}", file=sys.stderr)
                return 1
            print(f"stored {resp.json().get('stored', 0)} cosignature line(s) at size {cp.tree_size}", file=sys.stderr)
    return 0
