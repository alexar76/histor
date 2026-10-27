# HISTOR MCP upgrade brief guide

[English](upgrade-brief.md) · [Русский](upgrade-brief.ru.md) · [Español](upgrade-brief.es.md) · [Français](upgrade-brief.fr.md) · [中文](upgrade-brief.zh.md)

The local `histor upgrade-brief` prototype connects changes in MCP tool definitions to the
workflows you use. Give it two saved observations per dependency and an explicit selection of
tools. It produces `report.md` with retest suggestions and `report.json` with differences,
versions, comparison limits and input SHA-256 hashes.

Use the current source checkout for this prototype. The report command runs deterministically
offline, without an LLM, database, credentials or production settings. Tool descriptions are
treated as data. A changed definition gives you a reason to retest a workflow; it does not
establish that the server's behavior changed or that an upgrade is broken.

## Run and verify the example

You need Python 3.11 or newer. From the `histor/` directory in this workspace, run:

```bash
python3 --version
python3 -m histor upgrade-brief \
  --watchlist examples/upgrade-brief/watchlist.json \
  --out-dir ../output/histor-upgrade-brief-example/en
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

If your suitable interpreter is `.venv/bin/python`, use it instead of `python3` in each command.
The offline example requires no package installation. The verifier runs all five watchlists
twice, checks the expected changes and workflow statuses, checks byte-for-byte repeatability
and confirms that the input files stayed unchanged. Success prints `PASS` and exits with `0`;
a failed check exits with `1`. Each language directory contains `report.md` and `report.json`.
Repeating these commands replaces those generated reports.

The verifier also runs twice without `--lang` and requires identical output to explicit `--lang en`: 12 offline CLI runs in total.

All six observations are synthetic. No MCP server is started and no tool is called.
The [example walkthrough](../examples/upgrade-brief/README.md) explains the expected results
and concrete retests. The instructions, watchlist titles and workflow descriptions are available in five languages.

## Choose the report language

`--lang` accepts `ru`, `en`, `es`, `fr` or `zh`. The default is English (`en`) for the CLI, Python API and renderer fallback.
It selects the built-in Markdown headings and explanations, tool and workflow checklists, and
JSON checklist text and limits. The JSON `language` field records the choice. Machine field
names, status codes, diagnostic reasons, IDs, schemas and hashes remain unchanged.

Watchlist titles, workflow descriptions and source excerpts are preserved as supplied. Choose
one of the localized example watchlists for matching labels; `--lang` does not automatically
translate your inputs.

## Select dependencies and workflows

Create a bundle directory containing `watchlist.json` and `snapshots/`. Snapshot paths are relative
to the watchlist directory and must remain inside it. URLs, absolute paths, `..` components and
symlinks leading outside the bundle are rejected.

```json
{
  "format": "histor.upgrade-watchlist/v1",
  "title": "Our research workflow",
  "dependencies": [{
    "id": "our-research-server",
    "before": "snapshots/before.json",
    "after": "snapshots/after.json",
    "tools": ["search_docs"],
    "workflows": [{
      "id": "weekly-review",
      "description": "Prepare the weekly document review",
      "tools": ["search_docs"]
    }]
  }]
}
```

Every dependency needs a nonempty tool selection and at least one workflow. A workflow can use
only tools selected for that dependency. Keep dependency IDs, tool names and workflow IDs stable
when translating labels. `before` or `after` may be `null`; a missing observation leaves the
comparison unknown. Each snapshot's `targetId` must match its dependency's `id` for comparison.

Run your bundle from `histor/`:

```bash
python3 -m histor upgrade-brief \
  --watchlist /path/to/bundle/watchlist.json \
  --out-dir /path/to/report
```

## Save an HTTP observation

The separate `capture` command makes network requests to the endpoint you specify. It requires
HISTOR's runtime dependencies from `pyproject.toml`. If they are not installed, prepare an
environment from this source checkout first; dependency installation can access package indexes:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Capture a public HTTP MCP endpoint using its actual URL and a new output filename:

```bash
.venv/bin/python -m histor upgrade-brief capture \
  --url https://your-public-server.example/mcp \
  --target-id our-research-server \
  --out /path/to/bundle/snapshots/before.json
```

The `.example` URL and `/path/to/` paths are placeholders. Repeat after the update with
`after.json`, then run the offline report command. Capture uses discovery, legacy initialization
when needed and every `tools/list` page. It never calls tools or installs or starts MCP packages.
It supports no endpoint authentication and rejects URLs containing userinfo, a query or fragment.
Private addresses are blocked by default; use `--allow-private` explicitly for your own test
endpoint. `--timeout` defaults to 20 seconds and accepts a value greater than 0 and at most 300.

Capture never replaces an existing snapshot. Exit `0` means a complete observation was saved;
`1` means a failed observation was saved; `2` means invalid input or an output failure. Failed
observations do not stand for successful empty tool lists. A captured snapshot is unsigned local
evidence: the report's hashes detect input changes, without authenticating the creator.

## Preserve observation provenance

```json
{
  "format": "histor.upgrade-observation/v1",
  "targetId": "our-research-server",
  "status": "ok",
  "observedAt": "2026-10-27T09:00:00Z",
  "packageVersion": null,
  "serverName": "our-research-server",
  "serverVersion": "1.2.0",
  "protocol": "2026-07-28",
  "extrasCoverage": "observed",
  "launch": {"transport": "streamable-http", "endpoint": "https://your-public-server.example/mcp"},
  "tools": [{"name": "search_docs", "description": "Search documents", "inputSchema": {"type": "object"}}]
}
```

A successful observation needs the full `tools` array. Keep the actual timestamp with timezone,
observed protocol and launch context. Keep a package's observed version separate from the
server's self-reported `serverVersion`. For a remote endpoint, `packageVersion` may be `null`.
The report exposes a hash of `launch` and its known transport, without copying commands,
arguments or environment values; the original snapshots can still contain sensitive data.

When importing HISTOR data, associate definitions with the exact historical observation.
The local API implementation exposes `timeline[].protocolVersion`; copy it into `protocol`
when present. An older or deployed API may omit it: preserve that gap. Join the historical
`toolExtras` to tools by name only when those extras belong to that observation. Current
definitions or extras cannot fill an older snapshot. The prototype has no automatic API importer.

For an MTL/1 source containing only `name`, `description`, `inputSchema` and `outputSchema`, set
`extrasCoverage` to `"unknown"`. The report still compares these core fields, skips extras
comparisons and leaves overall comparability unknown. `"observed"` means the snapshot contains
all extras advertised in that observation; it is also the default for existing local v1 inputs.

## Read results and choose retests

| Dependency status | Meaning and next action |
|---|---|
| `changed` | Comparable observed definitions changed. Check affected selected tools and their workflows. |
| `unchanged` | Comparable observed definitions did not change. Runtime behavior has not been tested. |
| `context_changed` | Known protocol or launch conditions changed. Recreate comparable conditions before attributing differences to a package update. |
| `unknown` | A successful pair or comparison context is missing, dates are out of order, a selected tool is absent on both sides, or extras coverage is unknown. Resolve the listed gaps. |

Workflow statuses are `retest`, `no_observed_change` and `unknown`. A dependency can change
outside your selected tools; use the individual workflow status to set your retest scope.
`fieldCoverage` is `full`, `core-only` or `none`. Known differences remain visible even when
overall comparability is unknown. Annotation hints remain claims made by the server.

`serializedUtf8Bytes` measures compact model-facing JSON in UTF-8 bytes. It does not measure
tokens, a bill or achieved savings. SHA-256 values identify the exact input bytes. Neither
unchanged definitions nor a successful report establishes runtime correctness or safety.

## Limits and troubleshooting

The prototype accepts up to 30 dependencies, 128 selected tools per dependency, 64 workflows per
dependency and 2,000 tools per snapshot. A watchlist is limited to 256,000 bytes and each snapshot
to 2,000,000 bytes. Duplicate JSON keys, unknown format fields, nonfinite numbers, invalid tool
definitions and structures beyond 48 levels or 100,000 nodes are rejected.

The report command exits with `0` when reports are written, including reports with `unknown`
results; `2` means invalid input or a write failure. An existing output directory is reusable;
`report.md` and `report.json` are replaced, while overwriting an input file is rejected. For
`No module named histor`, run from the source checkout's `histor/` directory. For `unknown`,
inspect `limitations`, `sources` and `fieldCoverage` before making an upgrade decision.
