"""``python -m histor witness-submit`` — get a log's checkpoint co-signed by witnesses.

One witness:

    python -m histor witness-submit --log https://histor.modelmarket.dev \\
        --witness https://witness.example [--state ~/.histor/witness-state.json] [--submit-to-log]

Several, from a file, each cosignature verified against the witness's own key before it is
kept (a witness answering with a line its key did not make is reported, never stored):

    python -m histor witness-submit --log https://histor.modelmarket.dev \\
        --witnesses-file /etc/histor/witnesses.json --state /var/lib/histor-witness/state.json \\
        --submit-to-log

``witnesses.json`` is ``[{"name": "...", "vkey": "<name>+<id>+<base64>", "url": "<prefix>"}]``;
``url`` is the witness's submission prefix — this command POSTs ``<url>/add-checkpoint``.

Works for any log that serves a C2SP checkpoint at ``<log>/checkpoint`` and RFC 9162
consistency proofs at ``<log>/api/v1/log/proof/consistency``, and for any witness that speaks
c2sp.org/tlog-witness. It keeps, per witness, the size that witness last co-signed (a 409
tells it), fetches the consistency proof from that size, posts ``old <size>`` + proof +
checkpoint, and prints the cosignature lines. With ``--submit-to-log`` the verified lines
are stored in the log (``POST /api/v1/log/cosignatures``, operator token from the named
environment variable), so the log serves its checkpoint with them on it.

Exit status: 0 when every witness co-signed or had nothing new to sign, 1 when any witness
refused or answered with something that does not verify, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from histor.checkpoint import (
    COSIGNATURE_V1_TYPE,
    NoteError,
    VerifierKey,
    parse_checkpoint_text,
    parse_note,
    parse_signature_line,
    verify_cosignature,
)


@dataclass(frozen=True)
class WitnessTarget:
    url: str
    name: str = ""
    vkey: VerifierKey | None = None


def load_witnesses(path: Path) -> list[WitnessTarget]:
    out = []
    for entry in json.loads(path.read_text(encoding="utf-8")):
        key = VerifierKey.parse(str(entry["vkey"]))
        if key.type_byte != COSIGNATURE_V1_TYPE:
            raise ValueError(f"{entry.get('name')}: a witness key is type 0x04, not 0x{key.type_byte:02x}")
        if entry.get("name") and entry["name"] != key.name:
            raise ValueError(f"{entry['name']}: the vkey names {key.name}")
        out.append(WitnessTarget(url=str(entry["url"]).rstrip("/"), name=key.name, vkey=key))
    return out


def submit_one(http: httpx.Client, log: str, note_text: str, target: WitnessTarget,
               old: int) -> tuple[int, list[str], str]:
    """Returns (new size the witness holds, cosignature lines to keep, problem or "")."""
    note = parse_note(note_text)
    cp = parse_checkpoint_text(note.text)
    if old > cp.tree_size:
        return old, [], ""  # the witness is ahead of this checkpoint: nothing to sign
    resp = None
    for attempt in range(2):
        proof: list[str] = []
        if 0 < old < cp.tree_size:
            r = http.get(f"{log}/api/v1/log/proof/consistency", params={"first": old, "second": cp.tree_size})
            r.raise_for_status()
            proof = [base64.b64encode(bytes.fromhex(h)).decode() for h in r.json()["proof"]]
        body = f"old {old}\n" + "".join(f"{h}\n" for h in proof) + "\n" + note_text
        resp = http.post(f"{target.url}/add-checkpoint", content=body.encode(),
                         headers={"Content-Type": "text/plain; charset=utf-8"})
        if resp.status_code == 409 and attempt == 0:
            try:
                old = int(resp.text.strip() or "0")
            except ValueError:
                return old, [], f"409 without a size: {resp.text.strip()[:80]}"
            if old > cp.tree_size:
                return old, [], ""
            continue
        break
    assert resp is not None
    if resp.status_code != 200:
        return old, [], f"{resp.status_code}: {resp.text.strip()[:160]}"
    kept, problems = [], []
    for line in (ln for ln in resp.text.split("\n") if ln):
        try:
            sig = parse_signature_line(line)
        except NoteError as exc:
            problems.append(f"unparseable line ({exc})")
            continue
        if target.vkey is not None:
            if sig.name != target.vkey.name or sig.key_id != target.vkey.key_id:
                continue  # a line for another key of the same witness (e.g. ML-DSA): not ours to check
            if verify_cosignature(note.text, sig, target.vkey) is None:
                problems.append("a cosignature under the configured key does not verify")
                continue
        kept.append(line)
    if target.vkey is not None and not kept and not problems:
        problems.append("no cosignature under the configured key")
    return cp.tree_size, kept, "; ".join(problems)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="histor witness-submit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", required=True, help="log base URL")
    ap.add_argument("--witness", action="append", default=[], help="witness submission prefix (repeatable)")
    ap.add_argument("--witnesses-file", default="", help="JSON [{name, vkey, url}]; cosignatures are verified")
    ap.add_argument("--state", default="", help="JSON file remembering the size each witness last co-signed")
    ap.add_argument("--submit-to-log", action="store_true", help="store the cosignature lines in the log")
    ap.add_argument("--operator-token-env", default="HISTOR_OPERATOR_TOKEN")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    targets = [WitnessTarget(url=w.rstrip("/")) for w in args.witness]
    if args.witnesses_file:
        targets += load_witnesses(Path(args.witnesses_file))
    if not targets:
        print("name at least one witness (--witness or --witnesses-file)", file=sys.stderr)
        return 2
    token = ""
    if args.submit_to_log:
        token = os.environ.get(args.operator_token_env, "")
        if not token:
            print(f"{args.operator_token_env} is not set", file=sys.stderr)
            return 2

    log = args.log.rstrip("/")
    state_path = Path(args.state).expanduser() if args.state else None
    state = json.loads(state_path.read_text()) if state_path and state_path.is_file() else {}
    failed = False
    with httpx.Client(timeout=args.timeout, follow_redirects=False) as http:
        note_text = http.get(f"{log}/checkpoint").raise_for_status().text
        size = parse_checkpoint_text(parse_note(note_text).text).tree_size
        lines: list[str] = []
        for target in targets:
            label = target.name or target.url
            try:
                new_size, kept, problem = submit_one(http, log, note_text, target, int(state.get(target.url, 0)))
            except (httpx.HTTPError, NoteError, ValueError) as exc:
                new_size, kept, problem = int(state.get(target.url, 0)), [], f"{type(exc).__name__}: {exc}"
            if problem:
                failed = True
                print(f"{label}: {problem}", file=sys.stderr)
            if kept:
                state[target.url] = new_size
                lines.extend(kept)
                print("".join(line + "\n" for line in kept), end="")
            elif not problem:
                state[target.url] = max(new_size, int(state.get(target.url, 0)))
        if state_path:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
            tmp.replace(state_path)
        if args.submit_to_log and lines:
            resp = http.post(f"{log}/api/v1/log/cosignatures", json={"treeSize": size, "lines": lines[:16]},
                             headers={"X-Histor-Operator": token})
            if resp.status_code != 200:
                print(f"the log refused the cosignatures: {resp.status_code} {resp.text.strip()[:200]}", file=sys.stderr)
                return 1
            print(f"stored {resp.json().get('stored', 0)} cosignature line(s) at size {size}", file=sys.stderr)
    return 1 if failed else 0
