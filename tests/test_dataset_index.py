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


def zenodo_answer(*versions):
    return json.dumps({"hits": {"hits": [
        {"doi": doi, "metadata": {"title": f"HISTOR snapshot {day}: MCP tool definitions and how they change"}}
        for day, doi in versions]}})


def cite_block(page):
    return page[page.index("<h2>Cite</h2>"):page.index("<h2>Check it</h2>")]


def test_the_citation_names_the_snapshots_own_doi_not_the_concept_doi(tmp_path):
    snapshot(tmp_path, "2026-10-20", 81879, 23821)
    snapshot(tmp_path, "2026-11-02", 90000, 38000)
    dois = dataset_index.refresh_zenodo(tmp_path, fetch=lambda url: zenodo_answer(
        ("2026-11-02", "10.5281/zenodo.23884408"), ("2026-10-26", "10.5281/zenodo.23884119"),
        ("2026-10-20", "10.5281/zenodo.23512760")))
    assert dois["2026-10-20"] == "10.5281/zenodo.23512760"
    assert json.loads((tmp_path / "zenodo.json").read_text()) == dois
    page = dataset_index.write_index(tmp_path).read_text()
    cite = cite_block(page)
    assert 'HISTOR snapshot 2026-11-02' in cite and 'href="https://doi.org/10.5281/zenodo.23884408"' in cite
    # the concept DOI is offered only as the one that stands for every snapshot
    assert cite.count(dataset_index.ZENODO_CONCEPT_DOI) == 2 and "stands for all of them" in cite
    listed = page[page.index("<h2>All snapshots</h2>"):]
    assert 'href="2026-10-20/">2026-10-20</a>' in listed and "doi:10.5281/zenodo.23512760" in listed
    # archived here no longer (pruned after eight weeks): still listed, with its DOI
    assert "2026-10-26 · files on Zenodo only" in listed and "doi:10.5281/zenodo.23884119" in listed


def test_a_snapshot_not_yet_on_zenodo_points_to_the_newest_archived_one(tmp_path):
    snapshot(tmp_path, "2026-10-20", 81879, 23821)
    snapshot(tmp_path, "2026-11-09", 91000, 40000)
    (tmp_path / "zenodo.json").write_text(json.dumps({"2026-10-20": "10.5281/zenodo.23512760"}))
    cite = cite_block(dataset_index.write_index(tmp_path).read_text())
    assert "HISTOR snapshot 2026-10-20" in cite and "https://doi.org/10.5281/zenodo.23512760" in cite
    assert "The latest snapshot, 2026-11-09, is not on Zenodo yet" in cite and 'href="2026-11-09/manifest.json"' in cite


def test_zenodo_down_or_garbled_keeps_the_dois_already_known(tmp_path):
    snapshot(tmp_path, "2026-10-20", 81879, 23821)
    (tmp_path / "zenodo.json").write_text(json.dumps({"2026-10-20": "10.5281/zenodo.23512760"}))

    def down(url):
        raise OSError("timed out")

    for fetch in (down, lambda url: "<html>busy</html>", lambda url: '{"hits": 3}',
                  lambda url: zenodo_answer(("2026-10-20", "javascript:alert(1)"))):
        assert dataset_index.refresh_zenodo(tmp_path, fetch=fetch) == {"2026-10-20": "10.5281/zenodo.23512760"}
    assert json.loads((tmp_path / "zenodo.json").read_text()) == {"2026-10-20": "10.5281/zenodo.23512760"}
    assert not list(tmp_path.glob(".zenodo.json.*"))


def test_without_any_known_version_the_page_still_cites_the_concept_doi(tmp_path):
    snapshot(tmp_path, "2026-10-20", 81879, 23821)
    (tmp_path / "zenodo.json").write_text('{"2026-10-20": "not a doi", "x": "10.5281/zenodo.1"}')
    cite = cite_block(dataset_index.write_index(tmp_path).read_text())
    assert f'https://doi.org/{dataset_index.ZENODO_CONCEPT_DOI}</a></p>' in cite
