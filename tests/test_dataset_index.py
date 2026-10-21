"""/datasets/index.html: every snapshot listed, the newest first, and a schema.org Dataset block a crawler can parse."""

from __future__ import annotations

import json
import re

from histor import dataset_index


def snapshot(root, day, listed, changes, log="did:key:z6Mk<test>"):
    d = root / day
    d.mkdir()
    files = {name: {"sha256": "0" * 64, "bytes": 1234567, "rows": 10}
             for name in ("servers.jsonl.gz", "toolsets.jsonl.gz", "scans.jsonl.gz", "classifications.jsonl.gz")}
    files["changes.jsonl.gz"] = {"sha256": "0" * 64, "bytes": 2000, "rows": changes}
    files["pattern-set.json"] = {"sha256": "0" * 64, "bytes": 900}
    manifest = {"type": "histor.dataset/v1", "log": log, "createdAt": f"{day}T15:41:00Z", "changesFrom": "2026-09-24T10:53:44Z",
                "counts": {"listed": listed, "everChanged": 7}, "files": files}
    (d / "manifest.json").write_text(json.dumps(manifest))


def test_the_page_lists_snapshots_newest_first_with_a_parseable_dataset_block(tmp_path):
    snapshot(tmp_path, "2026-10-20", 81879, 23821)
    snapshot(tmp_path, "2026-11-01", 83000, 30000)
    (tmp_path / "2026-12-01").mkdir()  # no manifest yet: not listed
    (tmp_path / "latest").symlink_to("2026-11-01")
    out = dataset_index.write_index(tmp_path)
    page = out.read_text()
    assert "Latest snapshot: 2026-11-01" in page and 'href="2026-11-01/changes.jsonl.gz"' in page
    assert page.index('href="2026-11-01/"') < page.index('href="2026-10-20/"')
    assert "2026-12-01" not in page
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1)
    ld = json.loads(block)
    assert ld["@type"] == "Dataset" and ld["license"].endswith("/by/4.0/")
    assert ld["temporalCoverage"] == "2026-09-24/2026-11-01" and ld["version"] == "2026-11-01"
    assert ld["identifier"] == "https://doi.org/" + dataset_index.ZENODO_CONCEPT_DOI
    urls = {d["contentUrl"] for d in ld["distribution"]}
    assert "https://histor.modelmarket.dev/datasets/2026-11-01/toolsets.jsonl.gz" in urls
    assert not list(tmp_path.glob(".index.html.*"))


def test_text_from_a_manifest_is_escaped(tmp_path):
    snapshot(tmp_path, "2026-10-20", 1, 1, log='did:key:<script>alert(1)</script>')
    page = dataset_index.write_index(tmp_path).read_text()
    assert "<script>alert" not in page and "&lt;script&gt;" in page


def test_no_snapshot_writes_nothing(tmp_path):
    assert dataset_index.write_index(tmp_path) is None
    assert not (tmp_path / "index.html").exists()
