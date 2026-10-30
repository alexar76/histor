"""histor-tlog/2: C2SP checkpoints, signed notes, cosignatures and the witness."""
from __future__ import annotations

import base64
import hashlib
import json
import struct

import pytest
from awr import SigningKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from histor import merkle
from histor.app import create_app
from histor.checkpoint import (
    COSIGNATURE_V1_TYPE,
    LOG_KEY_TYPE,
    Note,
    NoteError,
    SignatureLine,
    VerifierKey,
    checkpoint_text,
    cosign,
    key_id,
    origin_hash,
    parse_checkpoint_text,
    parse_note,
    parse_signature_line,
    sign_checkpoint,
    verify_cosignature,
    verify_log_signature,
    vkey,
)
from histor.service import build
from histor.witness import SIZE_MEDIA, Witness, load_known_logs

ORIGIN = "example.com/log"
LOG_KEY = SigningKey.from_seed(hashlib.sha256(b"log").digest())
WIT_KEY = SigningKey.from_seed(hashlib.sha256(b"witness").digest())
ROOT = hashlib.sha256(b"root").digest()


# -- format primitives, checked against an independent re-implementation of the spec ------

def _ref_key_id(name: str, t: int, pub: bytes) -> bytes:
    return hashlib.sha256(name.encode() + b"\n" + bytes([t]) + pub).digest()[:4]


def test_key_id_and_vkey_follow_signed_note():
    pub = LOG_KEY.public_key_bytes
    assert key_id(ORIGIN, LOG_KEY_TYPE, pub) == _ref_key_id(ORIGIN, 1, pub)
    v = vkey(ORIGIN, LOG_KEY_TYPE, pub)
    name, hex_id, b64key = v.split("+")
    assert name == ORIGIN and hex_id == _ref_key_id(ORIGIN, 1, pub).hex()
    assert base64.b64decode(b64key) == b"\x01" + pub
    parsed = VerifierKey.parse(v)
    assert parsed.public_key == pub and parsed.type_byte == 1


def test_checkpoint_note_is_byte_exact_and_verifies_with_raw_ed25519():
    full = sign_checkpoint(LOG_KEY, ORIGIN, 8, ROOT)
    lines = full.split("\n")
    assert lines[0] == ORIGIN and lines[3] == "" and lines[-1] == "" and len(lines) == 6
    expected_text = f"{ORIGIN}\n8\n{base64.b64encode(ROOT).decode()}\n"
    assert full.startswith(expected_text + "\n— " + ORIGIN + " ")
    raw = base64.b64decode(full.split(" ")[-1])
    assert raw[:4] == _ref_key_id(ORIGIN, 1, LOG_KEY.public_key_bytes)
    Ed25519PublicKey.from_public_bytes(LOG_KEY.public_key_bytes).verify(raw[4:], expected_text.encode())
    note = parse_note(full)
    assert note.text == expected_text
    assert verify_log_signature(note, VerifierKey.parse(vkey(ORIGIN, LOG_KEY_TYPE, LOG_KEY.public_key_bytes)))
    # another key with the same name does not verify, and a wrong id is ignored, not accepted
    other = SigningKey.from_seed(hashlib.sha256(b"other").digest())
    assert not verify_log_signature(note, VerifierKey(ORIGIN, LOG_KEY_TYPE, other.public_key_bytes))


def test_cosignature_v1_is_byte_exact():
    text = checkpoint_text(ORIGIN, 8, ROOT)
    line = cosign(WIT_KEY, "example.com/witness", text, 1761800000)
    assert line.key_id == _ref_key_id("example.com/witness", 4, WIT_KEY.public_key_bytes)
    assert line.signature[:8] == struct.pack(">Q", 1761800000)
    msg = b"cosignature/v1\ntime 1761800000\n" + text.encode()
    Ed25519PublicKey.from_public_bytes(WIT_KEY.public_key_bytes).verify(line.signature[8:], msg)
    rendered = line.render()
    assert rendered.startswith("— example.com/witness ") and rendered.endswith("\n")
    v = VerifierKey.parse(vkey("example.com/witness", COSIGNATURE_V1_TYPE, WIT_KEY.public_key_bytes))
    assert verify_cosignature(text, parse_signature_line(rendered[:-1]), v) == 1761800000
    # the cosignature does not verify as a log signature, nor over a changed text
    assert not verify_log_signature(Note(text, (line,)), VerifierKey(ORIGIN, LOG_KEY_TYPE, WIT_KEY.public_key_bytes))
    assert verify_cosignature(text.replace("8\n", "9\n"), parse_signature_line(rendered[:-1]), v) is None


@pytest.mark.parametrize("bad", [
    "example.com/log\n8\n" + base64.b64encode(ROOT).decode() + "\n",            # no signature block
    "example.com/log\n08\n" + base64.b64encode(ROOT).decode() + "\n\n— a AAAAAQ==\n",  # leading zero
    "example.com/log\n8\nnot-base64\n\n— a AAAAAQ==\n",
    "example.com/log\n8\n" + base64.b64encode(ROOT).decode() + "\n\n- a AAAAAQ==\n",  # hyphen, not em dash
    "ex ample\n8\n" + base64.b64encode(ROOT).decode() + "\n\n— a AAAAAQ==\n",  # space in origin
    "example.com/log\n8\n" + base64.b64encode(ROOT).decode()[:-1] + "A\n\n— a AAAAAQ==\n",  # non-canonical b64
])
def test_malformed_notes_and_checkpoints_are_refused(bad):
    with pytest.raises(NoteError):
        parse_checkpoint_text(parse_note(bad).text)


def test_duplicate_name_and_key_id_is_refused():
    text = checkpoint_text(ORIGIN, 8, ROOT)
    line = SignatureLine("a", b"\x00\x01\x02\x03", b"x" * 64).render()
    with pytest.raises(NoteError):
        parse_note(text + "\n" + line + line)


# -- the witness, through the HTTP API ----------------------------------------------------

def _tree(n: int):
    nodes: dict[tuple[int, int], bytes] = {}

    def read(level, index):
        return nodes[(level, index)]

    for i in range(n):
        for level, idx, digest in merkle.appended_nodes(i, merkle.leaf_hash(f"leaf-{i}".encode()), read):
            nodes[(level, idx)] = digest
    return read


def _b64(h: bytes) -> str:
    return base64.b64encode(h).decode()


@pytest.fixture
def witness_app(settings, tmp_path, monkeypatch):
    known = tmp_path / "known-logs.json"
    known.write_text(json.dumps([{"origin": ORIGIN, "vkey": vkey(ORIGIN, LOG_KEY_TYPE, LOG_KEY.public_key_bytes)}]))
    from dataclasses import replace
    settings = replace(settings, witness_logs=str(known), log_origin="histor.test/log",
                       witness_name="histor.test/witness", operator_token="t")
    services = build(settings)
    try:
        yield TestClient(create_app(services)), services
    finally:
        services.close()


def _request(old: int, proof: list[bytes], note: str) -> bytes:
    return (f"old {old}\n" + "".join(_b64(h) + "\n" for h in proof) + "\n" + note).encode()


def test_witness_cosigns_only_what_it_can_prove(witness_app):
    client, services = witness_app
    read = _tree(8)
    root5, root8 = merkle.tree_root(5, read), merkle.tree_root(8, read)
    note5 = sign_checkpoint(LOG_KEY, ORIGIN, 5, root5)
    note8 = sign_checkpoint(LOG_KEY, ORIGIN, 8, root8)
    w_vkey = VerifierKey.parse(vkey("histor.test/witness", COSIGNATURE_V1_TYPE, services.witness.key.public_key_bytes))

    # unknown origin → 404
    stranger = sign_checkpoint(LOG_KEY, "other.example/log", 5, root5)
    assert client.post("/witness/v1/add-checkpoint", content=_request(0, [], stranger)).status_code == 404
    # known origin, wrong key → 403
    other = SigningKey.from_seed(hashlib.sha256(b"other").digest())
    forged = sign_checkpoint(other, ORIGIN, 5, root5)
    assert client.post("/witness/v1/add-checkpoint", content=_request(0, [], forged)).status_code == 403
    # first contact: old 0, no proof → 200 with a cosignature line
    r = client.post("/witness/v1/add-checkpoint", content=_request(0, [], note5))
    assert r.status_code == 200, r.text
    line = parse_signature_line(r.text.rstrip("\n"))
    assert verify_cosignature(parse_note(note5).text, line, w_vkey) is not None
    # old size mismatch → 409 with the size
    r = client.post("/witness/v1/add-checkpoint", content=_request(0, [], note8))
    assert r.status_code == 409 and r.text == "5\n" and r.headers["content-type"].startswith(SIZE_MEDIA)
    # wrong proof → 422
    bad = [bytes(32)] * len(merkle.consistency_proof(5, 8, read))
    assert client.post("/witness/v1/add-checkpoint", content=_request(5, bad, note8)).status_code == 422
    # right proof → 200, and the monitoring endpoint serves the co-signed checkpoint
    proof = merkle.consistency_proof(5, 8, read)
    r = client.post("/witness/v1/add-checkpoint", content=_request(5, proof, note8))
    assert r.status_code == 200, r.text
    served = client.get(f"/witness/v1/logs/{origin_hash(ORIGIN)}/checkpoint")
    assert served.status_code == 200
    note = parse_note(served.text)
    assert parse_checkpoint_text(note.text).tree_size == 8 and len(note.signatures) == 2
    # old above checkpoint → 400; a shrink is a 409 then a 400, never a cosignature
    assert client.post("/witness/v1/add-checkpoint", content=_request(9, [], note8)).status_code == 400
    assert client.post("/witness/v1/add-checkpoint", content=_request(8, [], note5)).status_code == 400
    # unknown origin hash on monitoring → 404
    assert client.get("/witness/v1/logs/" + "0" * 64 + "/checkpoint").status_code == 404


def test_the_log_serves_its_own_head_as_a_checkpoint_and_keeps_cosignatures(witness_app):
    client, services = witness_app
    # an empty log has no checkpoint
    assert client.get("/checkpoint").status_code == 404
    # append two leaves and sign a head
    with services.store.db.transaction() as tx:
        tx.lock_log()
        for i in range(2):
            services.store.append_leaf(tx, merkle.leaf_hash(f"leaf-{i}".encode()))
    sth = services.logbook.publish_sth(timestamp="2026-10-30T00:00:00Z")
    r = client.get("/checkpoint")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    note = parse_note(r.text)
    cp = parse_checkpoint_text(note.text)
    assert cp.origin == "histor.test/log" and cp.tree_size == 2 and cp.root.hex() == sth["rootHash"]
    issuer = client.get("/api/v1/issuer").json()
    assert verify_log_signature(note, VerifierKey.parse(issuer["checkpoint"]["vkey"]))
    assert issuer["witness"]["name"] == "histor.test/witness"
    # store a cosignature line the operator got from a witness
    line = cosign(WIT_KEY, "example.com/witness", note.text, 1761800000).render()
    r = client.post("/api/v1/log/cosignatures", json={"treeSize": 2, "lines": [line.rstrip("\n")]},
                    headers={"X-Histor-Operator": "t"})
    assert r.status_code == 200 and r.json()["stored"] == 1
    assert client.post("/api/v1/log/cosignatures", json={"treeSize": 99, "lines": [line]},
                       headers={"X-Histor-Operator": "t"}).status_code == 404
    assert client.post("/api/v1/log/cosignatures", json={"treeSize": 2, "lines": ["junk"]},
                       headers={"X-Histor-Operator": "t"}).status_code == 400
    assert client.post("/api/v1/log/cosignatures", json={"treeSize": 2, "lines": [line]}).status_code == 401
    served = parse_note(client.get("/checkpoint").text)
    assert [s.name for s in served.signatures] == ["histor.test/log", "example.com/witness"]
    assert client.get("/api/v1/log/checkpoint/2").text == client.get("/checkpoint").text
    assert client.get("/api/v1/log/checkpoint/3").status_code == 404


def test_known_logs_file_is_validated(tmp_path):
    path = tmp_path / "logs.json"
    path.write_text(json.dumps([{"origin": ORIGIN, "vkey": vkey(ORIGIN, COSIGNATURE_V1_TYPE, LOG_KEY.public_key_bytes)}]))
    with pytest.raises(ValueError):
        load_known_logs(path)
    assert load_known_logs(None) == {}
