"""C2SP checkpoints, signed notes and cosignatures — histor-tlog/2.

The log's head as a *checkpoint* (c2sp.org/tlog-checkpoint) inside a *signed note*
(c2sp.org/signed-note), so that any existing transparency-log witness can co-sign it with a
*cosignature/v1* (c2sp.org/tlog-cosignature), and so HISTOR can be such a witness for other
logs. Byte-exact rules, all of them from those three documents:

    note text     = <origin>\\n<tree size>\\n<base64 root>\\n            (extension lines: none)
    key id        = SHA-256(<key name> || 0x0A || <type byte> || <public key>)[:4]
    signature line= "— " <key name> " " base64(<key id> || <signature bytes>) "\\n"
    log signature : type 0x01, Ed25519 over the note text
    cosignature/v1: type 0x04, Ed25519 over
                    "cosignature/v1\\ntime <unix seconds>\\n" || note text,
                    signature bytes = <8-byte big-endian time> || <64-byte Ed25519>
    vkey          = <key name> "+" <8 hex of key id> "+" base64(<type byte> || <public key>)

Base64 is RFC 4648 standard alphabet with padding; decoders reject non-canonical input.
Nothing here depends on the database: the functions take bytes and return bytes, and the
test vectors (test-vectors/) pin them against the reference Go implementation.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass

from awr import SigningKey

from histor.signing import strict_ed25519

LOG_KEY_TYPE = 0x01          # signed-note Ed25519
COSIGNATURE_V1_TYPE = 0x04   # tlog-cosignature Ed25519 checkpoint cosignature
SIGNATURE_PREFIX = "— "  # em dash, space
COSIGNATURE_PREFIX = b"cosignature/v1\ntime "

_NAME_RE = re.compile(r"^[^\s+]+$")  # no Unicode spaces, no '+'


class NoteError(ValueError):
    """A note, checkpoint or signature line that does not follow the format."""


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode_strict(text: str) -> bytes:
    """Standard base64 with padding; a non-canonical encoding is refused, as the specs require."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise NoteError(f"bad base64: {exc}") from exc
    if b64(raw) != text:
        raise NoteError("non-canonical base64")
    return raw


def check_name(name: str) -> str:
    if not name or not _NAME_RE.match(name) or len(name.encode()) > 255:
        raise NoteError("a key name is non-empty, at most 255 bytes, with no spaces and no '+'")
    return name


def key_id(name: str, type_byte: int, public_key: bytes) -> bytes:
    return hashlib.sha256(name.encode("utf-8") + b"\n" + bytes([type_byte]) + public_key).digest()[:4]


def vkey(name: str, type_byte: int, public_key: bytes) -> str:
    """The verifier-key string a client is configured with (signed-note §Verifier keys)."""
    check_name(name)
    return f"{name}+{key_id(name, type_byte, public_key).hex()}+{b64(bytes([type_byte]) + public_key)}"


@dataclass(frozen=True)
class VerifierKey:
    name: str
    type_byte: int
    public_key: bytes

    @property
    def key_id(self) -> bytes:
        return key_id(self.name, self.type_byte, self.public_key)

    @classmethod
    def parse(cls, text: str) -> VerifierKey:
        # The name has no '+' (checked below), the key id is hex: only the base64 part may
        # contain '+', so the first two separators are the only separators.
        parts = text.strip().split("+", 2)
        if len(parts) != 3:
            raise NoteError("a vkey is <name>+<hex key id>+<base64 key>")
        name, hex_id, b64key = parts
        check_name(name)
        raw = b64decode_strict(b64key)
        if len(raw) != 33:
            raise NoteError("an Ed25519 vkey carries one type byte and 32 key bytes")
        key = cls(name, raw[0], raw[1:])
        if key.type_byte not in (LOG_KEY_TYPE, COSIGNATURE_V1_TYPE):
            raise NoteError(f"unsupported key type 0x{key.type_byte:02x}")
        if key.key_id.hex() != hex_id.lower():
            raise NoteError("vkey id does not match its name and key")
        return key


def checkpoint_text(origin: str, tree_size: int, root: bytes) -> str:
    """The note text of a checkpoint: origin, size, base64 root, each newline-terminated."""
    check_name(origin)
    if tree_size < 0:
        raise NoteError("tree size is not negative")
    if len(root) != 32:
        raise NoteError("a root hash is 32 bytes")
    return f"{origin}\n{tree_size}\n{b64(root)}\n"


@dataclass(frozen=True)
class Checkpoint:
    origin: str
    tree_size: int
    root: bytes
    extensions: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        text = checkpoint_text(self.origin, self.tree_size, self.root)
        return text + "".join(line + "\n" for line in self.extensions)


def parse_checkpoint_text(text: str) -> Checkpoint:
    """tlog-checkpoint: at least three non-empty lines; extension lines must be non-empty."""
    if not text.endswith("\n"):
        raise NoteError("checkpoint text ends with a newline")
    lines = text[:-1].split("\n")
    if len(lines) < 3 or any(not line for line in lines):
        raise NoteError("a checkpoint is at least three non-empty lines")
    origin, size_text, root_b64, *ext = lines
    check_name(origin)
    if not size_text.isdigit() or (size_text != "0" and size_text.startswith("0")):
        raise NoteError("tree size is an ASCII decimal without leading zeros")
    root = b64decode_strict(root_b64)
    if len(root) != 32:
        raise NoteError("a root hash is 32 bytes")
    for line in ext:
        if any(ord(c) < 0x20 for c in line):
            raise NoteError("an extension line carries no control characters")
    return Checkpoint(origin, int(size_text), root, tuple(ext))


@dataclass(frozen=True)
class SignatureLine:
    name: str
    key_id: bytes
    signature: bytes

    def render(self) -> str:
        return f"{SIGNATURE_PREFIX}{self.name} {b64(self.key_id + self.signature)}\n"


def parse_signature_line(line: str) -> SignatureLine:
    if not line.startswith(SIGNATURE_PREFIX):
        raise NoteError("a signature line starts with an em dash and a space")
    rest = line[len(SIGNATURE_PREFIX):]
    name, sep, sig_b64 = rest.partition(" ")
    if not sep or " " in sig_b64:
        raise NoteError("a signature line is: em dash, space, name, space, base64")
    check_name(name)
    raw = b64decode_strict(sig_b64)
    if len(raw) <= 4:
        raise NoteError("a signature carries a 4-byte key id and the signature bytes")
    return SignatureLine(name, raw[:4], raw[4:])


@dataclass(frozen=True)
class Note:
    text: str
    signatures: tuple[SignatureLine, ...]

    def render(self) -> str:
        return self.text + "\n" + "".join(s.render() for s in self.signatures)


def parse_note(data: str) -> Note:
    """signed-note: text (ending in newline), one blank line, then signature lines. The text is
    everything before the *last* blank line, so it may itself contain empty lines."""
    if any(ord(c) < 0x20 and c != "\n" for c in data):
        raise NoteError("a note carries no control characters other than newline")
    if not data.endswith("\n"):
        raise NoteError("a note ends with a newline")
    split = data.rfind("\n\n")
    if split < 0:
        raise NoteError("a note has a blank line between text and signatures")
    text, sig_block = data[: split + 1], data[split + 2:]
    if not text:
        raise NoteError("a note has a non-empty text")
    lines = sig_block[:-1].split("\n") if sig_block else []
    if not lines:
        raise NoteError("a note has at least one signature line")
    if len(lines) > 16:
        raise NoteError("more than 16 signature lines")
    sigs = tuple(parse_signature_line(line) for line in lines)
    seen = set()
    for s in sigs:
        if (s.name, s.key_id) in seen:
            raise NoteError("two signature lines with the same name and key id")
        seen.add((s.name, s.key_id))
    return Note(text, sigs)


# -- the log's own signature ---------------------------------------------------------

def sign_checkpoint(key: SigningKey, origin: str, tree_size: int, root: bytes) -> str:
    """A checkpoint note signed by the log key under ``origin`` (type 0x01)."""
    text = checkpoint_text(origin, tree_size, root)
    line = SignatureLine(origin, key_id(origin, LOG_KEY_TYPE, key.public_key_bytes), key.sign(text.encode()))
    return Note(text, (line,)).render()


def verify_log_signature(note: Note, verifier: VerifierKey) -> bool:
    """True when a signature line matching the verifier's name AND key id verifies over the
    text. A known key whose signature fails is a reason to reject the whole note; the caller
    decides (signed-note says SHOULD reject)."""
    if verifier.type_byte != LOG_KEY_TYPE:
        return False
    for s in note.signatures:
        if s.name == verifier.name and s.key_id == verifier.key_id:
            return len(s.signature) == 64 and strict_ed25519(verifier.public_key, s.signature, note.text.encode())
    return False


# -- cosignature/v1 ------------------------------------------------------------------

def cosignature_message(text: str, timestamp: int) -> bytes:
    return COSIGNATURE_PREFIX + str(int(timestamp)).encode() + b"\n" + text.encode()


def cosign(key: SigningKey, name: str, text: str, timestamp: int) -> SignatureLine:
    check_name(name)
    sig = key.sign(cosignature_message(text, timestamp))
    return SignatureLine(name, key_id(name, COSIGNATURE_V1_TYPE, key.public_key_bytes),
                         struct.pack(">Q", int(timestamp)) + sig)


def verify_cosignature(text: str, line: SignatureLine, verifier: VerifierKey) -> int | None:
    """The cosignature's timestamp when ``line`` is a valid cosignature/v1 by ``verifier`` over
    ``text``; None otherwise. Name and key id both have to match (signed-note)."""
    if verifier.type_byte != COSIGNATURE_V1_TYPE or line.name != verifier.name or line.key_id != verifier.key_id:
        return None
    if len(line.signature) != 72:
        return None
    timestamp = struct.unpack(">Q", line.signature[:8])[0]
    if strict_ed25519(verifier.public_key, line.signature[8:], cosignature_message(text, timestamp)):
        return timestamp
    return None


def origin_hash(origin: str) -> str:
    """tlog-witness monitoring path component: lowercase hex SHA-256 of the origin."""
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()
