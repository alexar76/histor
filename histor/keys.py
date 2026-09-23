"""The issuer key: one Ed25519 seed on the data volume, never in the database, never in env.

It signs every label (as an AWR/2 ``did:key`` issuer), every signed tree head and every /check
answer. Each of those is a different document type with its own ``type`` member inside the
signed bytes, so a signature over one can never be replayed as another.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from awr import SigningKey


class KeyMismatch(RuntimeError):
    """The key on disk and the log in the database do not belong together. Refuse to serve."""


def load_or_create_seed(path: Path, *, create: bool = True) -> bytes:
    if path.parent.is_symlink():
        raise RuntimeError(f"key directory {path.parent} must not be a link")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"issuer key {path} must be a regular file, not a link")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            seed = handle.read(33)
        if len(seed) != 32:
            raise RuntimeError(f"issuer key {path} is corrupted; expected 32 bytes")
        return seed
    if not create:
        raise KeyMismatch(
            f"issuer key {path} is missing but the database already holds a log signed by a key. "
            "A new key would silently start a second log over the old tree. Restore the key file "
            "(see docs/operations.md, Backups), or move the database aside to start a new log on purpose."
        )
    seed = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(seed)
    return seed


def issuer_key(path: Path, *, create: bool = True) -> SigningKey:
    return SigningKey.from_seed(load_or_create_seed(path, create=create))


def write_head_marker(path: Path, tree_size: int, root_hash: str) -> None:
    """Remember, next to the key, the newest tree head this key has signed.

    The key and the log live on different volumes. A database restored from an old backup, or
    reset, would otherwise let the same key sign a second, different history — an equivocation
    no auditor could tell from an attack. The marker is how startup notices.
    """
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps({"treeSize": tree_size, "rootHash": root_hash}))
    os.replace(tmp, path)


def read_head_marker(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise KeyMismatch(f"tree-head marker {path} is unreadable: {exc}") from exc


def bind_key_to_log(store, key: SigningKey, marker_path: Path) -> None:
    """Refuse a key that is not this log's, and a log older than the last head this key signed."""
    recorded = store.get_meta("issuer_did")
    if recorded is None:
        latest = store.latest_sth()
        if latest is not None and latest.get("log") != key.did:
            raise KeyMismatch(f"the log's tree heads are signed by {latest.get('log')}, not by this key ({key.did})")
        store.set_meta_value("issuer_did", key.did)
    elif recorded != key.did:
        raise KeyMismatch(
            f"this database is the log of {recorded}, but the issuer key on disk is {key.did}. "
            "Restore the log's key, or move the database aside to start a new log on purpose."
        )
    marker = read_head_marker(marker_path)
    latest = store.latest_sth()
    if marker and (latest is None or int(latest["treeSize"]) < int(marker["treeSize"])):
        raise KeyMismatch(
            f"this key signed a tree head of {marker['treeSize']} labels, but the database's newest head is "
            f"{latest['treeSize'] if latest else 0}: the database is older than the log it belongs to (a stale "
            "restore or a reset). Serving it would sign a second history under the same key. Restore the "
            "current database, or delete the marker only if you are starting a new log with a new key."
        )
    if marker and latest and int(latest["treeSize"]) == int(marker["treeSize"]) and latest["rootHash"] != marker["rootHash"]:
        raise KeyMismatch("the database's newest tree head differs from the one this key signed at the same size")
