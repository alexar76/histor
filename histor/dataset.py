"""``histor export-dataset DIR`` — the whole log's view of MCP servers as one downloadable snapshot.

Everything here is already public one server at a time (``/api/v1/servers/{id}``, the blob
routes, ``/api/v1/changes``); a snapshot puts it in files a researcher can load in one go:

    servers.jsonl.gz          one row per listed (name, endpoint), delisted ones included
    toolsets.jsonl.gz         every tool set a server showed and every one a change moved away from
    changes.jsonl.gz          every recorded change, oldest first
    scans.jsonl.gz            WARDEN pattern matches per tool set, under the current pattern set
    classifications.jsonl.gz  the advisory meaning-based verdicts (never in the signed log)
    tool_extras.jsonl.gz      per tool, the members MTL/1 does not digest: annotations, _meta,
                              title, icons and the rest (histor.extras)
    extras_changes.jsonl.gz   every change between two of those, oldest first
    package_observations.jsonl.gz  every run of an npm/PyPI package, oldest first, with the version
                              it ran (recorded since migration 014, 2026-10-26)
    pattern-set.json          the rules the scans ran (and record-set.json, the name records)
    manifest.json             counts, SHA-256 of every file, the signed tree head the export
                              started under — signed by the log's own key (type histor.dataset/v1)
    README.md                 the dataset card

Shapes that vary from server to server (a tool list, a change summary, scan matches) are carried
as JSON text in ``*Json`` fields, so the files load as flat tables in pandas, DuckDB or Arrow.

The export reads in pages: the service shares its memory limit with this process, and the tool
sets alone are well over a gigabyte of JSON. Opted-out servers (``HISTOR_OPT_OUT``) are left out
entirely. Files are gzip with a zero mtime, so the same rows give the same bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from histor.db import Tx
from histor.logbook import sign_document
from histor.registry import OPT_OUT, load_opt_out, opted_out
from histor.store import Store

DATASET_TYPE = "histor.dataset/v1"
PAGE = 2000
BLOB_PAGE = 100
LICENSE = "CC-BY-4.0"
SITE = "https://histor.modelmarket.dev"
# Every snapshot is a version of one Zenodo record; the concept DOI resolves to the newest.
ZENODO_CONCEPT_DOI = "10.5281/zenodo.23512759"
HF_DATASET = "https://huggingface.co/datasets/alexar76/histor-mcp-tool-definitions"


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Writer:
    """One gzip JSON Lines file; counts its rows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows = 0
        self._raw: IO[bytes] = path.open("wb")
        self._gz = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=0, compresslevel=6)

    def write(self, row: dict[str, Any]) -> None:
        self._gz.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        self.rows += 1

    def close(self) -> None:
        self._gz.close()
        self._raw.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pages(store: Store, sql: str, key: str, start: Any, size: int,
           params: tuple[Any, ...] = ()) -> Iterator[dict[str, Any]]:
    """Keyset pagination: ``sql`` takes (*params, last key, limit) and orders by ``key``."""
    last = start
    while True:
        with store.db.read() as tx:
            rows = tx.execute(sql, (*params, last, size))
        yield from rows
        if len(rows) < size:
            return
        last = rows[-1][key]


def _server_row(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t["id"], "name": t["name"], "title": t["title"], "description": t["description"],
        "endpoint": t["endpoint"], "transport": t["transport"], "registry": t["registry"],
        "version": t["version"], "repository": t["repository"], "website": t["website"],
        "firstListed": t["first_listed"], "lastListed": t["last_listed"], "delisted": bool(t["delisted"]),
        "skipReason": t["skip_reason"], "lastAttempt": t["last_attempt"], "lastStatus": t["last_status"],
        "lastDetail": t["last_detail"], "lastObserved": t["last_ok"], "toolCount": t["current_count"],
        "toolSetDigest": t["current_toolset"], "subjectDigest": t["current_subject"],
        "firstPinned": t["first_pinned"], "unchangedSince": t["unchanged_since"],
        "attempts": t["observations"], "observations": t["ok_observations"], "changes": t["changes"],
        "blockMatches": t["block_matches"], "adviseMatches": t["advise_matches"],
        "recordMatches": t["record_matches"], "classifierModel": t.get("classifier_model"),
        "classifierFlags": t.get("classifier_flags"), "packageVersion": t.get("package_version"),
        "toolExtrasDigest": t.get("current_extras"),
        "chainLabel": t["chain_label"], "page": f"{SITE}/s/{t['id']}",
    }


def _has_migration(store: Store, version: int) -> bool:
    with store.db.read() as tx:
        row = tx.one("SELECT 1 AS x FROM schema_migrations WHERE version >= ? LIMIT 1", (version,))
    return row is not None


def _has_package_versions(store: Store) -> bool:
    """Whether the database has migration 014: observations and changes carry the package version."""
    return _has_migration(store, 14)


def _has_extras(store: Store) -> bool:
    """Whether the database has migration 011 (histor.extras). The export can run against a log the
    service has not migrated yet: then the two extras files are written empty."""
    with store.db.read() as tx:
        row = tx.one("SELECT 1 AS x FROM schema_migrations WHERE version >= 11 LIMIT 1")
    return row is not None


class _SnapshotReads:
    """Keep all export pages on one database snapshot while remote polling continues."""

    def __init__(self, tx: Tx) -> None:
        self.tx = tx

    @contextmanager
    def read(self) -> Iterator[Tx]:
        yield self.tx


def export(store: Store, key: Any, out: Path, *, opt_out: tuple[str, ...] = (), now: str | None = None) -> dict[str, Any]:
    """Write a snapshot into ``out`` (which must not exist yet) and return its signed manifest."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists; a snapshot is written once")
    now = now or _utcnow()
    partial = out.with_name(out.name + f".partial-{os.getpid()}")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True, mode=0o755)
    try:
        with store.db.read() as tx:
            tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                       if tx.backend_type == "postgresql" else "BEGIN")
            snapshot = Store(_SnapshotReads(tx))
            manifest = _write(snapshot, key, partial, opt_out=opt_out, now=now, sth=snapshot.latest_sth())
        os.replace(partial, out)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return manifest


def _write(store: Store, key: Any, d: Path, *, opt_out: tuple[str, ...], now: str,
           sth: dict[str, Any] | None) -> dict[str, Any]:
    excluded: set[str] = set()
    wanted: set[str] = set()
    wanted_extras: set[str] = set()
    tally = {"listed": 0, "delisted": 0, "pinned": 0, "everChanged": 0, "blockTier": 0, "adviseOnly": 0,
             "packages": 0, "remote": 0}

    servers = _Writer(d / "servers.jsonl.gz")
    for t in _pages(store, "SELECT * FROM targets WHERE id > ? ORDER BY id LIMIT ?", "id", "", PAGE):
        if t["skip_reason"] == OPT_OUT or opted_out(t["endpoint"], opt_out):
            excluded.add(t["id"])
            continue
        servers.write(_server_row(t))
        if t["delisted"]:
            tally["delisted"] += 1
            continue
        tally["listed"] += 1
        tally["packages" if t["transport"] == "stdio" else "remote"] += 1
        if t["current_toolset"]:
            tally["pinned"] += 1
            wanted.add(t["current_toolset"])
        if t.get("current_extras"):
            wanted_extras.add(t["current_extras"])
        if t["changes"]:
            tally["everChanged"] += 1
        if t["block_matches"]:
            tally["blockTier"] += 1
        elif t["advise_matches"]:
            tally["adviseOnly"] += 1
    servers.close()

    changes = _Writer(d / "changes.jsonl.gz")
    first_change = last_change = None
    for c in _pages(store, "SELECT * FROM changes WHERE id > ? ORDER BY id LIMIT ?", "id", 0, PAGE):
        if c["target_id"] in excluded:
            continue
        for digest in (c["from_toolset"], c["to_toolset"]):
            if digest:
                wanted.add(digest)
        first_change = first_change or c["observed_at"]
        last_change = c["observed_at"]
        changes.write({
            "id": c["id"], "serverId": c["target_id"], "observedAt": c["observed_at"],
            "fromToolSetDigest": c["from_toolset"], "toToolSetDigest": c["to_toolset"],
            "fromSubjectDigest": c["from_subject"], "toSubjectDigest": c["to_subject"],
            "fromPackageVersion": c.get("from_package_version"), "toPackageVersion": c.get("to_package_version"),
            "fromServerVersion": c.get("from_server_version"), "toServerVersion": c.get("to_server_version"),
            "fromRegistryVersion": c.get("from_registry_version"), "toRegistryVersion": c.get("to_registry_version"),
            "labelId": c["label_id"], "summaryJson": c["summary"],
        })
    changes.close()

    # Every run of a package, failed ones included, with the version it ran: which version served
    # which list. Remote servers are left out (a daily read each is millions of rows and says no more
    # than their changes do). packageVersion is null on rows from before migration 014.
    package_observations = _Writer(d / "package_observations.jsonl.gz")
    if _has_package_versions(store):
        launch = "o.launch" if _has_migration(store, 15) else "NULL AS launch"  # how the run was launched (015)
        listed = "o.registry_version" if _has_migration(store, 16) else "NULL AS registry_version"  # 016
        for o in _pages(store, "SELECT o.id, o.target_id, o.observed_at, o.status, o.package_version, o.toolset, "
                        f"o.subject, o.extras, o.tool_count, o.server_name, o.server_version, o.protocol, {launch}, {listed} "
                        "FROM observations o "
                        "JOIN targets t ON t.id = o.target_id WHERE t.transport = 'stdio' AND o.id > ? "
                        "ORDER BY o.id LIMIT ?", "id", 0, PAGE):
            if o["target_id"] in excluded:
                continue
            if o["toolset"]:
                wanted.add(o["toolset"])
            package_observations.write({
                "id": o["id"], "serverId": o["target_id"], "observedAt": o["observed_at"], "status": o["status"],
                "packageVersion": o["package_version"], "toolSetDigest": o["toolset"], "subjectDigest": o["subject"],
                "toolExtrasDigest": o["extras"], "toolCount": o["tool_count"], "serverName": o["server_name"],
                "serverVersion": o["server_version"], "registryVersion": o["registry_version"],
                "launchJson": o["launch"], "protocolVersion": o["protocol"] or None,
            })
    package_observations.close()

    # Annotations, _meta and the other members MTL/1 does not digest, and every change between two of them.
    extras_changes = _Writer(d / "extras_changes.jsonl.gz")
    tool_extras = _Writer(d / "tool_extras.jsonl.gz")
    if _has_extras(store):
        for c in _pages(store, "SELECT * FROM extras_changes WHERE seq > ? ORDER BY seq LIMIT ?", "seq", 0, PAGE):
            if c["target_id"] in excluded:
                continue
            for digest in (c["from_extras"], c["to_extras"]):
                if digest:
                    wanted_extras.add(digest)
            extras_changes.write({
                "id": c["seq"], "serverId": c["target_id"], "observedAt": c["observed_at"],
                "fromExtrasDigest": c["from_extras"], "toExtrasDigest": c["to_extras"], "summaryJson": c["summary"],
            })
        for b in _pages(store, "SELECT digest, body FROM blobs WHERE kind = 'toolextras' AND digest > ? "
                        "ORDER BY digest LIMIT ?", "digest", "", BLOB_PAGE):
            if b["digest"] in wanted_extras:
                tool_extras.write({"digest": b["digest"], "extrasJson": b["body"]})
    extras_changes.close()
    tool_extras.close()

    toolsets = _Writer(d / "toolsets.jsonl.gz")
    for b in _pages(store, "SELECT digest, body FROM blobs WHERE kind = 'toolset' AND digest > ? ORDER BY digest LIMIT ?",
                    "digest", "", BLOB_PAGE):
        if b["digest"] in wanted:
            toolsets.write({"digest": b["digest"], "toolsJson": b["body"]})
    toolsets.close()

    pattern_set = store.get_meta("pattern_set")
    record_set = store.get_meta("record_set")
    scans = _Writer(d / "scans.jsonl.gz")
    if pattern_set:
        for s in _pages(store, "SELECT toolset, matches FROM scans WHERE pattern_set = ? AND toolset > ? "
                        "ORDER BY toolset LIMIT ?", "toolset", "", PAGE, (pattern_set,)):
            if s["toolset"] in wanted:
                scans.write({"toolSetDigest": s["toolset"], "patternSet": pattern_set, "matchesJson": s["matches"]})
    scans.close()

    classifications = _Writer(d / "classifications.jsonl.gz")
    with store.db.read() as tx:
        # A few thousand short verdicts: one read. (Keyed by (toolset, model), so no single-column keyset.)
        verdicts = tx.execute("SELECT toolset, model, verdict, updated_at FROM classifications ORDER BY toolset, model")
    for c in verdicts:
        if c["toolset"] in wanted:
            classifications.write({"toolSetDigest": c["toolset"], "model": c["model"], "updatedAt": c["updated_at"],
                                   "verdictJson": c["verdict"]})
    classifications.close()

    extra: list[str] = []
    for name, digest, kind in (("pattern-set.json", pattern_set, "pattern-set"), ("record-set.json", record_set, "record-set")):
        body = store.blob_text(digest, kind) if digest else None
        if body is not None:
            (d / name).write_text(body, encoding="utf-8")
            extra.append(name)

    writers = (servers, toolsets, changes, scans, classifications, tool_extras, extras_changes, package_observations)
    files = {w.path.name: {"sha256": _sha256(w.path), "bytes": w.path.stat().st_size, "rows": w.rows} for w in writers}
    for name in extra:
        files[name] = {"sha256": _sha256(d / name), "bytes": (d / name).stat().st_size}
    body = {
        "type": DATASET_TYPE,
        "log": key.did,
        "createdAt": now,
        "license": LICENSE,
        "source": SITE,
        "code": "https://github.com/alexar76/histor",
        "treeHead": sth,
        "patternSet": pattern_set,
        "recordSet": record_set,
        "changesFrom": first_change,
        "changesUntil": last_change,
        # Whether changes carry server and registry versions on both sides (migration 016).
        "changeVersions": _has_migration(store, 16),
        "counts": tally,
        "files": files,
    }
    manifest = sign_document(key, body)
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "README.md").write_text(dataset_card(manifest), encoding="utf-8")
    return manifest


def _extras_counting(f: dict[str, Any]) -> str:
    """Where the members outside the digest went, for the card's counting section."""
    if "tool_extras.jsonl.gz" in f:
        return "they are kept in `tool_extras`, and their changes in `extras_changes`."
    return "this snapshot does not record them, so a change to them alone is not in it."


def _extras_fields(f: dict[str, Any]) -> str:
    """The card's lines for the extras files, when the snapshot has them."""
    if "tool_extras.jsonl.gz" not in f:
        return ""
    return """
- **tool_extras** — `digest`, `extrasJson`: `{"tools": {name: {member: value}}}`, every member a tool
  carried beyond those four (`annotations`, `_meta`, `title`, `icons`, `execution`, vendor fields),
  only for tools that carried one. Join from `servers.toolExtrasDigest`: `""` there means observed with
  none, null not observed since HISTOR keeps them (2026-10-22). Not part of the MTL/1 digest or of any
  label; its digest is SHA-256 over sorted-key JSON, not RFC 8785 (prices in `_meta` are fractions).
- **extras_changes** — `serverId`, `observedAt`, `fromExtrasDigest` → `toExtrasDigest`, `summaryJson`:
  per tool, every annotation that moved (`[before, after]`) and every other member added, removed or
  changed; `escalations` names tools that stopped saying `readOnlyHint: true` or started saying
  `destructiveHint: true`."""


def _package_change_fields(f: dict[str, Any]) -> str:
    """The changes line about package versions, when the snapshot records them."""
    if "package_observations.jsonl.gz" not in f:
        return ""
    return """
  For a package, `fromPackageVersion` → `toPackageVersion`: the last version that served the old list
  and the one that served the new list (null for remote servers and before 2026-10-26)."""


def _version_change_fields(m: dict[str, Any]) -> str:
    """The changes line about server and registry versions, when the snapshot records them."""
    if not m.get("changeVersions"):
        return ""
    return """
  For every change, `fromServerVersion` → `toServerVersion`: the version the server reported about itself
  (`serverInfo`) in the last run that served the old list and in the run that saw the new one; and
  `fromRegistryVersion` → `toRegistryVersion`: the version the MCP registry listed for the entry at those
  two runs. A change whose versions are equal on both sides moved at an unchanged version: a record keyed
  on the version cannot see it. Server versions come from every run since the log began (null when the
  server reported none); registry versions are recorded since 2026-10-28 and null before, and null for
  entries that come from no registry."""


def _package_observation_fields(f: dict[str, Any]) -> str:
    """The card's line for package_observations, when the snapshot has it."""
    if "package_observations.jsonl.gz" not in f:
        return ""
    return """
- **package_observations** — every run of an npm/PyPI package, failed ones included, oldest first:
  `serverId`, `observedAt`, `status`, `packageVersion` (the version the sandbox ran), `toolSetDigest`
  (join to toolsets; null when the run failed), `subjectDigest`, `toolExtrasDigest`, `toolCount`, and
  `serverName`/`serverVersion` as the server reported them in legacy initialization or modern result
  metadata (often not the package version), `registryVersion` (what the MCP registry listed for the entry
  at that run; since 2026-10-28), and `protocolVersion` when recorded (otherwise null).
  `packageVersion` is recorded since 2026-10-26; older rows have it null. `launchJson`
  says how the run was launched: `observer` (the sandbox's version), `image`/`imageTag`/`runtime`/
  `entry`, `network` (`no route out`: a server that fetches something at startup gets nothing), and
  `supplied` when HISTOR started it again with configuration: the env variable NAMES it set to
  placeholders (never a value), start `args`, `pins`. The same version can serve different tools under
  other launch args or with the network, so compare lists launched alike. Null on runs before it was
  recorded (2026-10-26). Remote servers are not in this file: their record is their changes."""


def _mb(n: int) -> str:
    return f"{n / 1_000_000:,.0f} MB" if n >= 1_000_000 else f"{n / 1000:,.0f} kB"


def dataset_card(m: dict[str, Any], doi: str | None = None) -> str:
    """The README: a Hugging Face dataset card (YAML front matter) that also reads well anywhere else.

    ``doi`` is this snapshot's own Zenodo DOI, known only after it is archived; without it the card
    cites the concept DOI, which resolves to the newest snapshot."""
    c, f = m["counts"], m["files"]
    head = m.get("treeHead") or {}
    day = m["createdAt"][:10]
    rows = "\n".join(
        f"| `{name}` | {meta.get('rows', '—'):,} | {_mb(meta['bytes'])} | `{meta['sha256'][:16]}…` |"
        if isinstance(meta.get("rows"), int) else
        f"| `{name}` | — | {_mb(meta['bytes'])} | `{meta['sha256'][:16]}…` |"
        for name, meta in f.items()
    )
    configs = "\n".join(
        f"- config_name: {name.split('.')[0]}\n  data_files: {name}"
        for name in ("servers.jsonl.gz", "toolsets.jsonl.gz", "changes.jsonl.gz", "scans.jsonl.gz",
                     "classifications.jsonl.gz", "tool_extras.jsonl.gz", "extras_changes.jsonl.gz",
                     "package_observations.jsonl.gz")
        if name in f  # a card regenerated for an older snapshot names only the files it has
    )
    return f"""---
license: cc-by-4.0
pretty_name: HISTOR — MCP tool definitions and how they change
language:
- en
tags:
- mcp
- model-context-protocol
- ai-security
- prompt-injection
- tool-poisoning
- transparency-log
size_categories:
- 10K<n<100K
configs:
{configs}
---

# HISTOR snapshot {day}: what MCP servers advertise, and when it changes

HISTOR reads the tool list (`tools/list`) of every remote MCP server in the official MCP registry
on a polling schedule, and of npm/PyPI MCP packages run in a sandbox once per published version (new versions
first, a few hundred a day). It records the exact tool definitions,
pins them by digest, and writes a signed label to a public RFC 9162 Merkle log whenever a tool set
first appears, stays the same, or changes. This snapshot is that record as files.

- **{c['listed']:,}** listed servers ({c['remote']:,} remote endpoints, {c['packages']:,} packages), plus {c['delisted']:,} delisted
- **{c['pinned']:,}** with a recorded tool set; **{c['everChanged']:,}** changed at least once
- **{f['changes.jsonl.gz']['rows']:,}** recorded changes, {(m.get('changesFrom') or '—')[:10]} to {(m.get('changesUntil') or '—')[:10]}
- **{f['toolsets.jsonl.gz']['rows']:,}** distinct tool sets (current ones and every one a change replaced)
- **{c['blockTier']:,}** servers with a block-tier WARDEN pattern match, **{c['adviseOnly']:,}** advise-only

Live view and per-server pages: {m['source']} · code: {m['code']}

## Files

| File | Rows | Size | SHA-256 |
| --- | ---: | ---: | --- |
{rows}

`manifest.json` holds the full hashes and the counts, signed by the log's key `{m['log']}`
(type `{DATASET_TYPE}`, Ed25519 over RFC 8785 JSON). It also carries the signed tree head the
export started under (tree size {head.get('treeSize', 0):,}), so any label id below can be checked
against the live log with `GET {m['source']}/api/v1/labels/<id>/proof`.

## Fields

Fields whose shape varies between servers are JSON text (`*Json`), so every file loads as a flat
table. Digests are SRI strings (`sha256-…`) over the RFC 8785 form of the value.

- **servers** — `id` (stable per name+endpoint), `name`, `title`, `description`, `endpoint`
  (`https://…` or `npm:`/`pypi:` for packages), `transport`, `registry`, `version`, `repository`,
  `firstListed`/`lastListed`, `delisted`, `skipReason` (why it was not dialled), `lastStatus` and
  `lastDetail` (the last attempt's outcome), `lastObserved`, `toolCount`, `toolSetDigest` (join to
  toolsets), `firstPinned`, `unchangedSince`, `attempts`, `observations`, `changes`,
  `blockMatches`/`adviseMatches`/`recordMatches`, `classifierFlags`, `chainLabel` (latest label id), `page`.
- **toolsets** — `digest`, `toolsJson`: per tool the four members the digest covers, as served
  (`name`, `description`, `inputSchema`, and `outputSchema` when sent).{_extras_fields(f)}
- **changes** — `serverId`, `observedAt`, `fromToolSetDigest` → `toToolSetDigest`, `summaryJson`
  (tools added, removed and changed, with the field that moved), `labelId` (the signed continuity label).{_package_change_fields(f)}{_version_change_fields(m)}
- **scans** — `toolSetDigest`, `patternSet`, `matchesJson` (rule code, tier, severity, tool, where).
  The rules are in `pattern-set.json`. A match is a pattern hit to look at, not a verdict.
- **classifications** — `toolSetDigest`, `model`, `verdictJson`: an LLM's advisory reading of the
  tool set. Not reproducible, so it never enters the signed log.{_package_observation_fields(f)}

## How a change is counted

- **What is compared.** Per tool, the members in `toolsets`: `name`, `description`, `inputSchema`, and
  `outputSchema` when the server sent one. Tools are sorted by name and the set is hashed in its RFC 8785
  form, so tool order, object key order and JSON whitespace never make a change. Strings are compared as
  sent (a description that only gains a space is a change), and so is the order of arrays inside a schema
  (`required`, `enum`). Other members (`title`, `annotations`, `_meta`, `icons`) are not in the digest;
  {_extras_counting(f)}
- **When.** A change is a successful read whose digest differs from the server's previous successful
  read. Remote polling cadence depends on the observer configuration and available capacity; packages are read
  once per published version. States between polls can be missed. Every recorded change is
  recorded, including one that goes back to a list the server showed before.
- **Failed reads.** A timeout, an HTTP error, a server that asks for credentials or one HISTOR does not
  dial is never a change: the server keeps its last list, and `lastStatus` says how the last attempt
  went. A server that never answered without credentials has no tool set.
- **One client.** HISTOR reads without credentials, from one host. Lists that differ by user, account or
  region are not seen; a list that differs per session shows up as changes alternating between two digests.
- **What moved.** In `summaryJson`, a modified tool names the members that moved: `description` as a
  word diff, `inputSchema`/`outputSchema` as JSON paths (a parameter description is a path ending in
  `"description"`). A renamed tool is a removal plus an addition. `detail` is `names-only` when one side
  could not be digested; that change compares names only.

## Load it

```python
import pandas as pd
servers = pd.read_json("servers.jsonl.gz", lines=True)
changes = pd.read_json("changes.jsonl.gz", lines=True)
```

```sql
-- DuckDB
SELECT name, count(*) AS changes FROM 'changes.jsonl.gz' c
JOIN 'servers.jsonl.gz' s ON s.id = c.serverId GROUP BY name ORDER BY changes DESC LIMIT 20;

-- Changes in which nothing moved but tool descriptions
SELECT count(*) FROM (SELECT summaryJson::JSON AS s FROM 'changes.jsonl.gz')
WHERE s->>'detail' = 'full' AND json_array_length(s->'added') = 0 AND json_array_length(s->'removed') = 0
  AND json_array_length(s->'modified') > 0
  AND list_bool_and(list_transform(from_json(s->'modified', '["JSON"]'), m -> json_keys(m->'fields') = ['description']));
```

`scripts/change_kinds.py` in the code repository sorts every change by the fields it touched.

## What it is not

- A tool list is what a server advertised to an anonymous client on that day. Servers that need
  credentials, or that refused HISTOR, have no tool set here (`lastStatus`, `skipReason` say why).
- Pattern matches and classifier flags point at text worth reading; they do not say a server is malicious.
- A change is a change in the definitions, not a judgement of it. Most are ordinary releases.

## Cite

Artamokhov, A. ({day[:4]}). *HISTOR snapshot {day}: MCP tool definitions and how they change* [Data set].
Zenodo. https://doi.org/{doi or ZENODO_CONCEPT_DOI}

{"This snapshot's DOI is " + doi + "; all snapshots: " if doi else "This is the DOI of every snapshot (it resolves to the newest; each has its own version DOI on the record): "}https://doi.org/{ZENODO_CONCEPT_DOI}.
Also on Hugging Face: {HF_DATASET} (tag `snapshot-{day}`).

## License and rights

The compilation, digests, labels, scans and verdicts are {LICENSE}. Tool names, descriptions and schemas are reproduced as each server
published them; their rights stay with their authors. They are included so the definitions can be
studied and checked. A server operator who wants out: open an issue on {m['code']} with the
endpoint or host. Opted-out servers are left out of every later snapshot.
"""


def main(store: Store, key: Any, settings: Any, args: list[str]) -> int:
    import sys

    paths = [a for a in args if not a.startswith("-")]
    if len(paths) != 1:
        print("usage: histor export-dataset DIR [--force]   (DIR must not exist)", file=sys.stderr)
        return 2
    running = store.last_run(finished_only=False)
    if running and not running.get("finished_at") and "--force" not in args:
        # A crawl writes all day; a snapshot taken mid-crawl mixes two days. Wait for it.
        print(f"a crawl started at {running['started_at']} is still running; export after it, or pass --force",
              file=sys.stderr)
        return 75
    manifest = export(store, key, Path(paths[0]), opt_out=load_opt_out(settings.opt_out, settings.opt_out_path))
    print(json.dumps({"dir": paths[0], "counts": manifest["counts"],
                      "files": {k: v.get("rows", v["bytes"]) for k, v in manifest["files"].items()}}))
    return 0


def open_readonly(settings: Any) -> tuple[Store, Any, Any]:
    """The store and the issuer key without writing anything: no migrations, no key-file chmod, no
    marker. The host runs the export beside the live service with the data volume mounted read-only,
    where the service's own start-up path (which repairs and binds) cannot run."""
    from awr import SigningKey

    from histor.db import open_backend

    path = settings.issuer_key_path
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"issuer key {path} is missing or a link")
    seed = path.read_bytes()
    if len(seed) != 32:
        raise RuntimeError(f"issuer key {path} is corrupted; expected 32 bytes")
    key = SigningKey.from_seed(seed)
    backend = open_backend(settings.db_path, settings.database_url)
    store = Store(backend)
    recorded = store.get_meta("issuer_did")
    if recorded != key.did:
        backend.close()
        raise RuntimeError(f"this database is the log of {recorded}, not of the key on disk ({key.did})")
    return store, key, backend


if __name__ == "__main__":
    # ``python -m histor.dataset DIR``: the same command without the ``histor`` entry point, so a
    # host can run a one-off export from an image that predates it (deploy/export_dataset.sh).
    import sys

    from histor.config import load_settings

    _settings = load_settings()
    _store, _key, _backend = open_readonly(_settings)
    try:
        raise SystemExit(main(_store, _key, _settings, sys.argv[1:]))
    finally:
        _backend.close()
