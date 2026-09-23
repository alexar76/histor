"""Both keys live on the data volume, survive restarts, and refuse links and corruption."""

from __future__ import annotations

import os

import pytest

from histor.keys import issuer_key, load_or_create_seed
from histor.signing import _PQ_LIB, ProviderSigner, manifest_canonical, object_canonical


def test_the_issuer_key_is_created_once_and_reloaded(tmp_path):
    path = tmp_path / "k" / "issuer.key"
    first = issuer_key(path).did
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert issuer_key(path).did == first


def test_a_corrupted_or_linked_issuer_key_is_refused(tmp_path):
    bad = tmp_path / "issuer.key"
    bad.write_bytes(b"short")
    with pytest.raises(RuntimeError, match="corrupted"):
        load_or_create_seed(bad)
    real = tmp_path / "real.key"
    real.write_bytes(os.urandom(32))
    link = tmp_path / "link.key"
    link.symlink_to(real)
    with pytest.raises(RuntimeError, match="link"):
        load_or_create_seed(link)
    linkdir = tmp_path / "dirlink"
    linkdir.symlink_to(tmp_path)
    with pytest.raises(RuntimeError, match="link"):
        load_or_create_seed(linkdir / "x.key")


def test_the_provider_key_reloads_and_signs_verifiably(tmp_path):
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    a = ProviderSigner(tmp_path / "provider.key", pqc=False)
    b = ProviderSigner(tmp_path / "provider.key", pqc=False)
    assert a.public_key_b64 == b.public_key_b64 and a.pq_public_key_b64 == ""
    doc = {"b": 2, "a": 1}
    sig = a.sign_object(doc)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(a.public_key_b64)).verify(
        base64.b64decode(sig["value"]), object_canonical(doc).encode())
    assert "tools_hash:" in manifest_canonical({"tools": []})


@pytest.mark.skipif(not _PQ_LIB, reason="dilithium-py not installed")
def test_the_pq_identity_is_persisted_and_signs(tmp_path):
    first = ProviderSigner(tmp_path / "provider.key", pqc=True)
    again = ProviderSigner(tmp_path / "provider.key", pqc=True)
    assert first.pq_public_key_b64 and first.pq_public_key_b64 == again.pq_public_key_b64
    sig = first.sign_manifest({"tools": [], "capabilities_count": 0})
    assert sig["pq_algorithm"] == "ml-dsa-65" and sig["pq_value"]


def test_a_corrupted_provider_key_is_refused(tmp_path):
    (tmp_path / "provider.key").write_bytes(b"x" * 5)
    with pytest.raises(RuntimeError, match="corrupted"):
        ProviderSigner(tmp_path / "provider.key", pqc=False)


# -- the key and the log belong together (audit F10) ---------------------------------------

def _crawled(settings):
    from histor.service import build

    s = build(settings)
    with s.store.db.transaction() as tx:
        s.logbook.append(tx, s.issuer.undigestible(
            subject_ref={"id": "urn:x", "digestSRI": "sha256-" + "A" * 43 + "="}, server={"name": "n", "registry": "r"},
            count=0, code="MTL-SUBJ-002", message="empty", observed_at="2026-09-01T00:00:00Z",
            issued_at="2026-09-01T00:00:00Z"), target_id=None)
    s.logbook.publish_sth()
    return s


def test_a_replaced_key_is_refused_instead_of_starting_a_second_log(settings):
    import pytest

    from histor.keys import KeyMismatch
    from histor.service import build

    _crawled(settings).close()
    settings.issuer_key_path.unlink()
    with pytest.raises(KeyMismatch, match="missing"):
        build(settings)
    assert not settings.issuer_key_path.exists(), "a refused start must not leave a new key behind"
    settings.issuer_key_path.write_bytes(b"\x01" * 32)
    settings.issuer_key_path.chmod(0o600)
    with pytest.raises(KeyMismatch, match="log of"):
        build(settings)


def test_a_database_older_than_the_last_signed_head_is_refused(settings):
    import pytest

    from histor.keys import KeyMismatch, read_head_marker
    from histor.service import build

    s = _crawled(settings)
    assert read_head_marker(settings.head_marker_path)["treeSize"] == 1
    with s.store.db.transaction() as tx:  # what a stale restore or a reset looks like
        tx.execute("DELETE FROM sths")
    s.close()
    with pytest.raises(KeyMismatch, match="older than the log"):
        build(settings)


def test_only_the_serving_process_declares_a_run_dead(settings):
    from histor.service import build

    s = build(settings)
    with s.store.db.transaction() as tx:
        tx.execute("INSERT INTO runs(started_at) VALUES(?)", ("2026-09-01T00:00:00Z",))
    s.close()
    cli = build(settings)  # e.g. `histor issuer` run by the deploy script next to a live crawl
    assert cli.store.last_run(finished_only=False)["finished_at"] is None
    cli.close()
    served = build(settings, serving=True)
    assert "interrupted" in served.store.last_run(finished_only=False)["stats"]["error"]
    served.close()


def test_the_cli_will_not_start_a_second_crawl_next_to_a_running_one(settings, capsys):
    from histor.__main__ import main
    from histor.service import build

    s = build(settings)
    with s.store.db.transaction() as tx:
        tx.execute("INSERT INTO runs(started_at) VALUES(?)", ("2026-09-01T00:00:00Z",))
    s.close()
    assert main(["crawl"]) == 1
    assert "has not finished" in capsys.readouterr().err
