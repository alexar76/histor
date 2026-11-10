"""Typed HF reads must survive old all-null batches without changing signed raw files."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from histor import dataset, dataset_schema
from tests.test_dataset import crawl_with_a_change, rows


def test_every_exported_column_has_a_declared_scalar_type(world, tmp_path):
    s = crawl_with_a_change(world)
    manifest = dataset.export(s.store, s.key, tmp_path / "snapshot")
    card = (tmp_path / "snapshot" / "README.md").read_text()
    for name, fields in dataset_schema.SCHEMAS.items():
        for row in rows(tmp_path / "snapshot" / (name + ".jsonl.gz")):
            assert set(row) == dict(fields).keys()
            for column, dtype in fields:
                value = row[column]
                if value is not None:
                    assert type(value) is {"string": str, "int64": int, "bool": bool}[dtype]
        declared = f"- config_name: {name}\n"
        assert (declared in card) == (manifest["files"][name + ".jsonl.gz"]["rows"] > 0)


def test_repair_preserves_prose_other_metadata_and_is_idempotent():
    card = "---\nlicense: cc-by-4.0\nconfigs:\n- config_name: changes\n  data_files: changes.jsonl.gz\n" \
           "tags:\n- mcp\ndataset_info:\n- config_name: changes\n  features:\n  - name: id\n    dtype: null\n---\n" \
           "\n# Original prose\nA signed raw snapshot.\n"
    files = {"changes.jsonl.gz": {"rows": 2}, "scans.jsonl.gz": {"rows": 0}}
    repaired = dataset_schema.with_hf_metadata(card, files)
    assert repaired.split("\n---\n", 1)[1] == card.split("\n---\n", 1)[1]
    assert "tags:\n- mcp\n" in repaired and "license: cc-by-4.0\n" in repaired
    assert repaired.count("dataset_info:") == 1 and "dtype: null" not in repaired
    assert "config_name: scans" not in repaired
    assert dataset_schema.with_hf_metadata(repaired, files) == repaired
    with pytest.raises(ValueError, match="No HF scalar schema"):
        dataset_schema.hf_metadata({"future.jsonl.gz": {"rows": 1}})
    with pytest.raises(ValueError, match="YAML front matter"):
        dataset_schema.with_hf_metadata("# Not a card", files)


@pytest.mark.parametrize("name", dataset_schema.SCHEMAS)
def test_hf_loader_uses_card_types_after_an_all_null_first_batch(name, tmp_path):
    datasets = pytest.importorskip("datasets", reason="HF reader regression needs optional datasets")
    fields = dataset_schema.SCHEMAS[name]
    first = dict.fromkeys(dict(fields))
    later = {field: {"string": "value", "int64": 7, "bool": True}[dtype] for field, dtype in fields}
    path = tmp_path / (name + ".jsonl.gz")
    # Force separate Arrow batches, as older rows before version tracking do in production.
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(first) + "\n" + json.dumps(later) + "\n")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    card = "---\n" + dataset_schema.hf_metadata({path.name: {"rows": 2}}) + "\n---\n\n# Test\n"
    (tmp_path / "README.md").write_text(card)
    builder = datasets.load_dataset_builder(str(tmp_path), name=name, chunksize=1,
                                            cache_dir=str(tmp_path / "cache"))
    assert {column: feature.dtype for column, feature in builder.info.features.items()} == dict(fields)
    builder.download_and_prepare()
    loaded = builder.as_dataset(split="train")
    assert loaded.to_list() == [first, later]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_empty_raw_file_is_signed_download_but_not_an_empty_hf_split(world, tmp_path):
    s = world["services"]
    manifest = dataset.export(s.store, s.key, tmp_path / "empty")
    assert all(meta["rows"] == 0 for name, meta in manifest["files"].items() if name.endswith(".jsonl.gz"))
    card = (tmp_path / "empty" / "README.md").read_text()
    assert "configs: []\ndataset_info: []" in card
    assert "`changes.jsonl.gz`" in card
    assert all((tmp_path / "empty" / name).is_file() for name in manifest["files"])


@pytest.mark.parametrize("card_only", [True, False])
def test_uploader_repairs_old_cards_without_changing_raw_or_snapshot_tags(card_only, tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "deploy" / "upload_hf.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    source = script.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    conf, snapshot = tmp_path / "conf", tmp_path / "snapshot"
    conf.mkdir()
    snapshot.mkdir()
    (conf / "repo").write_text("alexar76/test-only")
    (conf / "token").write_text("local-test-placeholder")
    original = "---\nlicense: cc-by-4.0\nconfigs:\n- config_name: changes\n  data_files: changes.jsonl.gz\n---\n\n## Files\n\nOriginal prose.\n"
    (snapshot / "README.md").write_text(original)
    manifest = {"createdAt": "2026-11-09T09:20:00Z", "log": "did:key:test", "files": {
        "changes.jsonl.gz": {"rows": 2}, "scans.jsonl.gz": {"rows": 0}}, "treeHead": {"treeSize": 2}}
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    (snapshot / "changes.jsonl.gz").write_bytes(b"unchanged signed bytes")
    (snapshot / "scans.jsonl.gz").write_bytes(b"unchanged empty signed bytes")
    before = {p.name: p.read_bytes() for p in snapshot.iterdir()}
    captured = {}

    class FakeApi:
        def __init__(self, *, token):
            assert token == "local-test-placeholder"

        def dataset_info(self, *, repo_id):
            return SimpleNamespace(sha="pinned-before")

        def hf_hub_download(self, *, filename, revision, **_):
            assert revision == "pinned-before"
            return str(snapshot / filename)

        def upload_file(self, **kwargs):
            assert kwargs["path_in_repo"] == "README.md" and kwargs["parent_commit"] == "pinned-before"
            captured["card"] = kwargs["path_or_fileobj"].decode()
            return SimpleNamespace(oid="card-after")

        def create_repo(self, *_, **__):
            assert not card_only

        def upload_folder(self, **kwargs):
            assert not card_only
            stage = Path(kwargs["folder_path"])
            captured["card"] = (stage / "README.md").read_text()
            for name, raw in before.items():
                if name != "README.md":
                    assert (stage / name).read_bytes() == raw
            return SimpleNamespace(oid="new-snapshot")

        def create_tag(self, *_, **kwargs):
            assert not card_only, "card-only repair must leave snapshot tags intact"
            assert kwargs["revision"] == "new-snapshot"

    fake = ModuleType("huggingface_hub")
    fake.HfApi = FakeApi
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    monkeypatch.setattr(sys, "argv", ["-", str(conf), "--card-only" if card_only else str(snapshot),
                                      str(Path(dataset_schema.__file__))])
    if card_only:
        with pytest.raises(SystemExit) as exit_info:
            exec(compile(source, str(script), "exec"), {})
        assert exit_info.value.code == 0
    else:
        exec(compile(source, str(script), "exec"), {})
    assert "dataset_info:\n- config_name: changes\n  features:" in captured["card"]
    assert "config_name: scans" not in captured["card"]
    assert {p.name: p.read_bytes() for p in snapshot.iterdir()} == before
