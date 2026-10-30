"""HISTOR as a witness for other transparency logs — c2sp.org/tlog-witness.

A witness keeps, per log it knows, the newest checkpoint it co-signed, and co-signs a newer
one only after proving the log only grew (RFC 6962/9162 consistency). It co-signs nothing it
cannot prove. The logs it knows are configured, not discovered: an origin and the log's
verifier key (``vkey``), from a JSON file the operator writes.

Statuses and bodies are the ones the specification names, so a client written for any
witness works here: 200 with cosignature lines, 400 old size above checkpoint size, 403 no
trusted signature, 404 unknown origin, 409 old size is not the witness's size (body = that
size, ``text/x.tlog.size``), 422 proof or root does not check out.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from awr import SigningKey

from histor import merkle
from histor.checkpoint import (
    LOG_KEY_TYPE,
    Note,
    NoteError,
    SignatureLine,
    VerifierKey,
    b64decode_strict,
    cosign,
    origin_hash,
    parse_checkpoint_text,
    parse_note,
    verify_log_signature,
)

MAX_PROOF_LINES = 63
SIZE_MEDIA = "text/x.tlog.size"


@dataclass(frozen=True)
class WitnessReply:
    status: int
    body: str
    media_type: str = "text/plain; charset=utf-8"


def load_known_logs(path: Path | None) -> dict[str, tuple[VerifierKey, ...]]:
    """``[{"origin": "...", "vkey": "..."}]`` → origin → verifier keys. A key whose name differs
    from the origin is accepted (the spec recommends, not requires, that they match)."""
    if path is None or not path.is_file():
        return {}
    out: dict[str, list[VerifierKey]] = {}
    for entry in json.loads(path.read_text(encoding="utf-8")):
        origin = str(entry["origin"])
        key = VerifierKey.parse(str(entry["vkey"]))
        if key.type_byte != LOG_KEY_TYPE:
            raise ValueError(f"{origin}: a log is verified with a type-0x01 key, not 0x{key.type_byte:02x}")
        out.setdefault(origin, []).append(key)
    return {origin: tuple(keys) for origin, keys in out.items()}


class Witness:
    def __init__(self, store, key: SigningKey, name: str, known: dict[str, tuple[VerifierKey, ...]]) -> None:
        self.store = store
        self.key = key
        self.name = name
        self.known = known

    # -- state ------------------------------------------------------------------------

    def state(self, origin: str) -> dict | None:
        with self.store.db.read() as tx:
            return tx.one("SELECT origin, tree_size, root, checkpoint, updated_at FROM witness_logs WHERE origin=?",
                          (origin,))

    def _persist(self, origin: str, expected_old: int, size: int, root_hex: str, checkpoint: str) -> bool:
        """Atomic check-and-set: the row's size must still be ``expected_old`` (0 = no row)."""
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.store.db.transaction() as tx:
            tx.lock_log(key=0x57495400)  # "WIT": this table's own lock, not the log append lock
            row = tx.one("SELECT tree_size FROM witness_logs WHERE origin=?", (origin,))
            current = int(row["tree_size"]) if row else 0
            if current != expected_old:
                return False
            if row:
                tx.execute("UPDATE witness_logs SET tree_size=?, root=?, checkpoint=?, updated_at=? WHERE origin=?",
                           (size, root_hex, checkpoint, now, origin))
            else:
                tx.execute("INSERT INTO witness_logs(origin, tree_size, root, checkpoint, updated_at) "
                           "VALUES(?, ?, ?, ?, ?)", (origin, size, root_hex, checkpoint, now))
        return True

    # -- add-checkpoint ---------------------------------------------------------------

    def add_checkpoint(self, body: str, *, now: int | None = None) -> WitnessReply:
        try:
            old, proof, note = _parse_request(body)
        except NoteError as exc:
            return WitnessReply(400, f"bad request: {exc}\n")
        try:
            cp = parse_checkpoint_text(note.text)
        except NoteError as exc:
            return WitnessReply(400, f"bad checkpoint: {exc}\n")

        keys = self.known.get(cp.origin)
        if not keys:
            return WitnessReply(404, "unknown log origin\n")
        # Signatures from unknown keys are ignored; a matching known key that fails rejects.
        verified = False
        for key in keys:
            for s in note.signatures:
                if s.name == key.name and s.key_id == key.key_id:
                    if verify_log_signature(Note(note.text, (s,)), key):
                        verified = True
                    else:
                        return WitnessReply(403, "a signature by a trusted key does not verify\n")
        if not verified:
            return WitnessReply(403, "no signature from a trusted key for this origin\n")

        if old > cp.tree_size:
            return WitnessReply(400, "old size is above the checkpoint size\n")
        row = self.state(cp.origin)
        current = int(row["tree_size"]) if row else 0
        if old != current:
            return WitnessReply(409, f"{current}\n", SIZE_MEDIA)
        if cp.tree_size == 0:
            if cp.root != merkle.EMPTY_ROOT:
                return WitnessReply(422, "a size-0 checkpoint carries the empty-tree hash\n")
            if proof:
                return WitnessReply(422, "no proof goes with old size 0\n")
        elif old == 0:
            if proof:
                return WitnessReply(422, "no proof goes with old size 0\n")
        else:
            old_root = bytes.fromhex(row["root"])
            if old == cp.tree_size:
                if cp.root != old_root:
                    return WitnessReply(422, "two roots for one tree size\n")
                if proof:
                    return WitnessReply(422, "no proof goes with an unchanged size\n")
            elif not merkle.verify_consistency(old, cp.tree_size, proof, old_root, cp.root):
                return WitnessReply(422, "the consistency proof does not verify\n")

        timestamp = int(time.time()) if now is None else int(now)
        line = cosign(self.key, self.name, note.text, timestamp)
        signed = Note(note.text, tuple(note.signatures) + (line,)).render()
        if not self._persist(cp.origin, old, cp.tree_size, cp.root.hex(), signed):
            fresh = self.state(cp.origin)
            return WitnessReply(409, f"{int(fresh['tree_size']) if fresh else 0}\n", SIZE_MEDIA)
        return WitnessReply(200, line.render())

    def checkpoint_for(self, origin_hex: str) -> str | None:
        """tlog-witness monitoring: the newest co-signed checkpoint, by origin hash."""
        for origin in self.known:
            if origin_hash(origin) == origin_hex:
                row = self.state(origin)
                return row["checkpoint"] if row else None
        return None


def _parse_request(body: str) -> tuple[int, list[bytes], Note]:
    """``old <n>`` line, up to 63 base64 proof lines, an empty line, then the checkpoint note."""
    split = body.find("\n\n")
    if split < 0 or not body.startswith("old "):
        raise NoteError("the body is 'old <size>', proof lines, an empty line, a checkpoint")
    head, note_text = body[:split], body[split + 2:]
    lines = head.split("\n")
    size_text = lines[0][4:]
    if (not size_text.isascii() or not size_text.isdigit() or len(size_text) > 19
            or (len(size_text) == 19 and size_text > "9223372036854775807")
            or (size_text != "0" and size_text.startswith("0"))):
        raise NoteError("old size is an ASCII decimal without leading zeros")
    proof_lines = lines[1:]
    if len(proof_lines) > MAX_PROOF_LINES:
        raise NoteError("more than 63 consistency proof lines")
    proof = []
    for line in proof_lines:
        raw = b64decode_strict(line)
        if len(raw) != 32:
            raise NoteError("a proof hash is 32 bytes")
        proof.append(raw)
    return int(size_text), proof, parse_note(note_text)
