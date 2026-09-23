"""HISTOR is indexable by a real AIMarket hub: checked with the hub's own signer and validator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from histor.app import create_app

HUB = Path(__file__).resolve().parents[2] / "aimarket-hub"
if HUB.is_dir() and str(HUB) not in sys.path:
    sys.path.insert(0, str(HUB))


def hub_bits():
    try:
        from aimarket_hub.signing import Signer
        from aimarket_hub.validator import validate_manifest
    except ImportError:  # pragma: no cover - standalone checkout
        pytest.skip("sibling aimarket-hub sources are not on the path")
    return Signer, validate_manifest


@pytest.fixture
def client(world):
    with TestClient(create_app(world["services"])) as c:
        yield c


def test_manifest_passes_the_hubs_validator_and_signature(client):
    Signer, validate_manifest = hub_bits()
    manifest = client.get("/ai-market/v2/manifest").json()
    assert validate_manifest(manifest) == []
    key = client.get("/.well-known/ai-market.json").json()["signer_public_key"]
    hub = Signer.__new__(Signer)
    assert Signer.verify_hybrid(key, manifest["signature"], hub.manifest_canonical(manifest))


def test_well_known_verifies_as_a_whole_object(client):
    Signer, _ = hub_bits()
    wk = client.get("/.well-known/ai-market.json").json()
    assert Signer.verify_object_signature(wk, wk["signer_public_key"])


def test_the_interop_receipt_verifies_under_the_advertised_key_only(client, tmp_path):
    Signer, _ = hub_bits()
    reply = client.post("/ai-market/v2/invoke", json={"capability_id": "histor.changes@v1", "input": {}}).json()
    key = client.get("/.well-known/ai-market.json").json()["signer_public_key"]
    hub = Signer(tmp_path / "hub.key")
    assert hub.verify_receipt_signature(reply["receipt"], key) is True
    assert hub.verify_receipt_signature(reply["receipt"], hub.public_key_b64) is False
