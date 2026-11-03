"""The landing page of /datasets/: what the snapshots are, how to cite and load them, and a
schema.org ``Dataset`` block so dataset search engines index the collection.

    python3 dataset_index.py [--zenodo] /var/lib/histor-datasets

Standard library only: deploy/export_dataset.sh runs it on the host after each snapshot, outside
the service image, and deploy/zenodo_reminder.sh each morning. It reads every
``<YYYY-MM-DD>/manifest.json`` and writes ``index.html`` next to them (written aside, then renamed).

Each archived snapshot has its own Zenodo version DOI, and only that one names the files a number
was counted on: the concept DOI opens the newest version, where the same query gives another
count (found 2026-11-03, when a citation of the 20 October host count was about to use the concept
DOI the page offered). ``--zenodo`` adds the
versions Zenodo lists to ``zenodo.json`` beside the snapshots, and the page cites from that file.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

SITE = "https://histor.modelmarket.dev"
BASE = f"{SITE}/datasets"
ZENODO_CONCEPT_ID = "23512759"
ZENODO_CONCEPT_DOI = f"10.5281/zenodo.{ZENODO_CONCEPT_ID}"
# 25 is the most Zenodo gives without a token; the newest 25 are enough, as zenodo.json keeps the rest.
ZENODO_VERSIONS = (f"https://zenodo.org/api/records?q=conceptrecid:{ZENODO_CONCEPT_ID}"
                   "&allversions=true&sort=newest&size=25")
ZENODO_FILE = "zenodo.json"  # {snapshot day: its version DOI}
HF_DATASET = "https://huggingface.co/datasets/alexar76/histor-mcp-tool-definitions"
CODE = "https://github.com/alexar76/histor"
CREATOR = "Aleksandr Artamokhov"
NAME = "HISTOR: MCP tool definitions and how they change"
DESCRIPTION = (
    "Weekly snapshots of a public transparency log of Model Context Protocol (MCP) servers: every "
    "remote server in the official MCP registry and every npm/PyPI MCP package HISTOR observes, the "
    "exact tool definitions each one served, every recorded change field by field, WARDEN pattern "
    "scans and advisory classifier verdicts. JSON Lines (gzip) with a manifest signed by the log's key."
)
FILES = (
    ("servers.jsonl.gz", "One row per listed server: endpoint, transport, status, tool count, the digest of its current tool list."),
    ("toolsets.jsonl.gz", "Every tool list a server served (name, description and schemas, as served), current ones and every one a change replaced."),
    ("changes.jsonl.gz", "Every recorded change: tools added, removed and modified, field by field, with the signed label."),
    ("scans.jsonl.gz", "WARDEN pattern matches per tool list (rules in pattern-set.json). Leads to read, not verdicts."),
    ("classifications.jsonl.gz", "Advisory LLM readings of tool lists. Not reproducible, so never in the signed log."),
    ("tool_extras.jsonl.gz", "What each tool carried beyond those: annotations (readOnlyHint, destructiveHint…), _meta, title, icons. Since 2026-10-22."),
    ("extras_changes.jsonl.gz", "Every change to those members, annotation by annotation, with the tools that dropped readOnlyHint."),
    ("package_observations.jsonl.gz", "Every run of an npm/PyPI package with the version it ran and the list it served. Versions since 2026-10-26."),
    ("manifest.json", "Counts and SHA-256 of every file, signed by the log's key, with the tree head of the snapshot."),
    ("README.md", "The dataset card: fields, loading, citation, what the data does not say."),
)
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOI = re.compile(r"^10\.5281/zenodo\.\d+$")
TITLE_DAY = re.compile(r"HISTOR snapshot (\d{4}-\d{2}-\d{2})")


def zenodo_dois(root: Path) -> dict[str, str]:
    """The version DOI of each archived snapshot, as ``--zenodo`` last saw them."""
    try:
        data = json.loads((root / ZENODO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {d: v for d, v in data.items()
            if isinstance(d, str) and DAY.match(d) and isinstance(v, str) and DOI.match(v)}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": f"histor-dataset-index (+{SITE})"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def refresh_zenodo(root: Path, fetch: Callable[[str], str] = _get) -> dict[str, str]:
    """Add the versions Zenodo lists (a version's title names its snapshot day) to ``zenodo.json``.

    Zenodo unreachable or an answer without hits: the file stays as it was, so a bad morning never
    takes a DOI off the page."""
    known = zenodo_dois(root)
    try:
        hits = json.loads(fetch(ZENODO_VERSIONS))["hits"]["hits"]
        found = {}
        for h in hits:
            day = TITLE_DAY.search(str(h["metadata"]["title"]))
            doi = str(h.get("doi") or "")
            if day and DOI.match(doi):
                found.setdefault(day.group(1), doi)  # newest first: a re-upload of a day keeps the newest
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"Zenodo not read ({type(e).__name__}); the version DOIs stay as they were", file=sys.stderr)
        return known
    merged = {**known, **found}
    if merged != known:
        tmp = root / f".{ZENODO_FILE}.{os.getpid()}"
        tmp.write_text(json.dumps(dict(sorted(merged.items())), indent=1) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, root / ZENODO_FILE)
    return merged


def snapshots(root: Path) -> list[dict[str, Any]]:
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir() and DAY.match(p.name)), key=lambda p: p.name, reverse=True):
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a snapshot without a readable manifest is not listed
        out.append({"day": d.name, "manifest": m})
    return out


def _size(n: int) -> str:
    return f"{n / 1_000_000:,.1f} MB" if n >= 1_000_000 else f"{n / 1000:,.0f} kB"


def json_ld(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    latest = snaps[0]["manifest"]
    days = [s["day"] for s in snaps]
    first_change = min((s["manifest"].get("changesFrom") or s["day"]) for s in snaps)[:10]
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": NAME,
        "description": DESCRIPTION,
        "url": f"{BASE}/",
        "sameAs": [f"https://doi.org/{ZENODO_CONCEPT_DOI}", HF_DATASET],
        "identifier": f"https://doi.org/{ZENODO_CONCEPT_DOI}",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "isAccessibleForFree": True,
        "creator": {"@type": "Person", "name": CREATOR},
        "publisher": {"@type": "Organization", "name": "HISTOR", "url": SITE},
        "keywords": ["Model Context Protocol", "MCP", "AI agents", "tool poisoning", "prompt injection",
                     "AI security", "transparency log", "software supply chain"],
        "temporalCoverage": f"{first_change}/{days[0]}",
        "dateModified": latest["createdAt"],
        "version": days[0],
        "variableMeasured": ["tool definitions", "tool definition changes", "pattern matches"],
        "distribution": [
            {"@type": "DataDownload", "name": name, "contentUrl": f"{BASE}/{days[0]}/{name}",
             "encodingFormat": "application/gzip" if name.endswith(".gz") else
             ("application/json" if name.endswith(".json") else "text/markdown")}
            for name, _ in FILES if name in latest["files"] or name in ("manifest.json", "README.md")
        ],
        "isBasedOn": CODE,
    }


def _citation(day: str, doi: str) -> str:
    esc = html.escape
    return (f'<p class="cite">Artamokhov, A. ({esc(day[:4])}). <i>HISTOR snapshot {esc(day)}: MCP tool definitions and '
            f'how they change</i> [Data set].\nZenodo. <a href="https://doi.org/{esc(doi)}">https://doi.org/{esc(doi)}</a></p>')


def cite_section(day: str, dois: dict[str, str]) -> str:
    """Cite the snapshot by its version DOI; the concept DOI only stands for all of them."""
    esc = html.escape
    archived = sorted((d for d in dois if d <= day), reverse=True)
    if day in dois:
        head = _citation(day, dois[day])
    elif archived:
        head = (_citation(archived[0], dois[archived[0]]) +
                f'\n<p>The latest snapshot, {esc(day)}, is not on Zenodo yet; the citation above is the newest one that is. '
                f'To cite {esc(day)} itself, give its signed manifest: <a href="{esc(day)}/manifest.json">'
                f'{BASE}/{esc(day)}/manifest.json</a>.</p>')
    else:
        head = _citation(day, ZENODO_CONCEPT_DOI)
    return (head + f'\n<p>Cite the snapshot you counted on: the same query gives other numbers on another snapshot. '
            f'<a href="https://doi.org/{ZENODO_CONCEPT_DOI}">doi:{ZENODO_CONCEPT_DOI}</a> stands for all of them and '
            f'always opens the newest.</p>')


def page(snaps: list[dict[str, Any]], dois: dict[str, str] | None = None) -> str:
    esc = html.escape
    dois = dois or {}
    latest = snaps[0]
    m, day = latest["manifest"], latest["day"]
    c = m["counts"]
    f = m["files"]
    rows = []
    for name, what in FILES:
        meta = f.get(name)
        if meta is None and name not in ("manifest.json", "README.md"):
            continue  # a file this snapshot does not have (older snapshots predate the extras files)
        size = _size(meta["bytes"]) if meta else ""
        nrows = f"{meta['rows']:,}" if meta and isinstance(meta.get("rows"), int) else ""
        rows.append(f'<tr><td><a href="{esc(day)}/{esc(name)}"><code>{esc(name)}</code></a></td>'
                    f'<td class="n">{nrows}</td><td class="n">{size}</td><td>{esc(what)}</td></tr>')
    def doi_of(d: str) -> str:
        return f' · <a href="https://doi.org/{esc(dois[d])}">doi:{esc(dois[d])}</a>' if d in dois else ""

    here = {s["day"]: s for s in snaps}
    older = "".join(
        (f'<li><a href="{esc(d)}/">{esc(d)}</a> · {here[d]["manifest"]["counts"]["listed"]:,} servers · '
         f'{here[d]["manifest"]["files"]["changes.jsonl.gz"]["rows"]:,} changes{doi_of(d)}</li>') if d in here else
        f'<li>{esc(d)} · files on Zenodo only{doi_of(d)}</li>'  # older than the snapshots kept here
        for d in sorted(set(here) | set(dois), reverse=True)
    )
    ld = json.dumps(json_ld(snaps), ensure_ascii=False, indent=1).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HISTOR open data: MCP tool definitions and how they change</title>
<meta name="description" content="{esc(DESCRIPTION)}">
<link rel="canonical" href="{BASE}/">
<meta property="og:type" content="website"><meta property="og:title" content="HISTOR open data: MCP tool definitions and how they change">
<meta property="og:description" content="{esc(DESCRIPTION)}"><meta property="og:url" content="{BASE}/">
<meta property="og:image" content="{SITE}/assets/og.png"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{SITE}/assets/og.png">
<script type="application/ld+json">
{ld}
</script>
<style>
:root{{--bg:#05080d;--panel:#0b111b;--ink:#efe9dc;--muted:#9aa6b8;--line:rgba(255,255,255,.09);--accent:#ffd58a;--link:#5fe3ff}}
@media (prefers-color-scheme: light){{:root{{--bg:#fbfaf7;--panel:#fff;--ink:#16181d;--muted:#5a6270;--line:rgba(0,0,0,.1);--accent:#8a5a00;--link:#00609a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:980px;margin:0 auto;padding:40px 16px 64px}}a{{color:var(--link)}}
h1{{font-size:clamp(26px,4vw,38px);line-height:1.15;margin:0 0 12px}}h2{{font-size:21px;margin:36px 0 10px}}
.lead{{color:var(--muted);max-width:760px}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}
.stat{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}}.stat b{{display:block;font-size:24px;color:var(--accent)}}
.stat span{{color:var(--muted);font-size:14px}}table{{width:100%;border-collapse:collapse;font-size:15px}}
.tw{{overflow-x:auto}}td,th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
td.n{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px}}
pre{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;overflow-x:auto}}
.cite{{background:var(--panel);border-left:3px solid var(--accent);padding:12px 16px;border-radius:6px}}ul{{padding-left:20px}}
p code,li code{{overflow-wrap:anywhere}}.cite a{{overflow-wrap:anywhere}}
@media (max-width:640px){{th:nth-child(4),td:nth-child(4){{display:none}}td,th{{padding:8px 6px}}table{{font-size:14px}}td code{{font-size:13px;overflow-wrap:anywhere}}}}
</style></head><body><main>
<h1>MCP tool definitions and how they change</h1>
<p class="lead">HISTOR reads the tool list of every remote MCP server in the official MCP registry once a day, and runs every
new version of npm and PyPI MCP packages in a sandbox. Once a week the whole record is published here as files:
what each server advertised, and every change, field by field. Free under CC BY 4.0.</p>
<div class="stats">
<div class="stat"><b>{c['listed']:,}</b><span>listed servers and packages</span></div>
<div class="stat"><b>{f['toolsets.jsonl.gz']['rows']:,}</b><span>distinct tool lists</span></div>
<div class="stat"><b>{f['changes.jsonl.gz']['rows']:,}</b><span>recorded changes</span></div>
<div class="stat"><b>{c['everChanged']:,}</b><span>servers changed at least once</span></div>
</div>
<h2>Latest snapshot: {esc(day)}</h2>
<div class="tw"><table><thead><tr><th>File</th><th>Rows</th><th>Size</th><th>What</th></tr></thead><tbody>
{"".join(rows)}
</tbody></table></div>
<p><b>New here?</b> Start with <code>servers.jsonl.gz</code> (one row per listed server) and <code>changes.jsonl.gz</code>
(one row per recorded change, field by field); <code>toolsets.jsonl.gz</code> is the large one — every distinct tool list in full.
Sizes are in the table.</p>
<p>The same files: <a href="{HF_DATASET}">Hugging Face</a> (one tag per snapshot) ·
<a href="https://doi.org/{ZENODO_CONCEPT_DOI}">Zenodo</a> (a DOI per archived snapshot; the record names its date, and <a href="latest/">latest/</a> here may be newer) ·
<a href="latest/">latest/</a> always points at the newest snapshot.</p>
<h2>Load it</h2>
<pre>from datasets import load_dataset   # Hugging Face, any snapshot tag
changes = load_dataset("alexar76/histor-mcp-tool-definitions", data_files="changes.jsonl.gz", split="train")</pre>
<pre>-- DuckDB: the servers whose tool lists changed most often
SELECT s.name, count(*) AS changes
FROM '{BASE}/latest/changes.jsonl.gz' c
JOIN '{BASE}/latest/servers.jsonl.gz' s ON s.id = c.serverId
GROUP BY s.name ORDER BY changes DESC LIMIT 20;</pre>
<pre>import pandas as pd
servers = pd.read_json("{BASE}/latest/servers.jsonl.gz", lines=True)</pre>
<h2>Cite</h2>
{cite_section(day, dois)}
<h2>Check it</h2>
<p><code>manifest.json</code> lists the SHA-256 of every file and is signed (Ed25519) by the log's key
<code>{esc(m['log'])}</code>, with the signed tree head the snapshot was taken under. Any label in it can be
proven against the live log: <a href="{SITE}/">{SITE}</a>. Code: <a href="{CODE}">{CODE}</a>.</p>
<h2>All snapshots</h2>
<ul>{older}</ul>
<h2>What it does not say</h2>
<ul>
<li>A tool list is what a server showed an anonymous client on that day; servers that need a key show less or nothing.</li>
<li>A change is a change in the definitions, not a judgement. Most are ordinary releases.</li>
<li>Pattern matches and classifier flags are text worth reading, not proof that a server is malicious.</li>
<li>A server operator who wants out of future snapshots can open an issue on <a href="{CODE}">{CODE}</a>.</li>
</ul>
</main></body></html>
"""


def write_index(root: Path, zenodo: bool = False) -> Path | None:
    snaps = snapshots(root)
    if not snaps:
        return None
    dois = refresh_zenodo(root) if zenodo else zenodo_dois(root)
    out = root / "index.html"
    tmp = root / f".index.html.{os.getpid()}"
    tmp.write_text(page(snaps, dois), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    zenodo = "--zenodo" in args
    args = [a for a in args if a != "--zenodo"]
    if len(args) != 1:
        print("usage: dataset_index.py [--zenodo] DATASETS_DIR", file=sys.stderr)
        raise SystemExit(2)
    written = write_index(Path(args[0]), zenodo=zenodo)
    print(written or "no snapshots with a manifest")
