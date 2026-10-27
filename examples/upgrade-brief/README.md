# Reproduce a HISTOR MCP upgrade brief

[English](README.md) · [Русский](README.ru.md) · [Español](README.es.md) · [Français](README.fr.md) · [中文](README.zh.md)

This example compares six saved, synthetic observations across three dependencies. It connects
definition changes to four workflows and gives you specific checks before an upgrade. The
fixtures do not describe a published package, live endpoint or customer deployment.

## Run from the source checkout

Use Python 3.11 or newer, from `histor/`. No installation, credentials, model or network is needed:

```bash
python3 -m histor upgrade-brief \
  --watchlist examples/upgrade-brief/watchlist.json \
  --out-dir ../output/histor-upgrade-brief-example/en
python3 examples/upgrade-brief/verify.py \
  --out-dir ../output/histor-upgrade-brief-example
```

Use `.venv/bin/python` instead of `python3` if that is your Python 3.11+ interpreter. The first
command writes `report.md` and `report.json`. The second verifies all five language watchlists
twice against [expected.json](expected.json), checks identical repeat output and unchanged
inputs, then prints `PASS` and exits with `0`. A failed check exits with `1`. Reports are stored
under `en/`, `ru/`, `es/`, `fr/` and `zh/`; rerunning replaces generated reports.

The verifier also runs twice without `--lang` and requires identical output to explicit `--lang en`: 12 offline CLI runs in total. The canonical `watchlist.json` is English; `watchlist.en.json` remains an equivalent alias.

## Expected results

The summary is `changed: 1`, `unchanged: 0`, `context_changed: 1`, `unknown: 1`.

| Dependency | Result | Workflow result |
|---|---|---|
| `demo:research` | `changed`, comparable definitions | `weekly-review` and `archive-import`: `retest` |
| `demo:migration` | `context_changed`, protocol changed | `status-check`: `unknown` |
| `demo:offline` | `unknown`, new observation failed | `daily-status`: `unknown` |

For `weekly-review`, replay a saved `search_docs` request with the new required `workspace`
field. Check that `limit` is now declared as a string and update parsing from `items` to
`results`. Read the changed description and confirm expected tool selection. `export_brief`
changes `readOnlyHint` from `true` to `false` and `destructiveHint` from `false` to `true`;
review confirmation and permission handling before testing an export. These hints are server claims.

For `archive-import`, the selected `legacy_fetch` tool is removed. The new unselected
`fetch_document` tool takes `id` instead of `document_id`. Inspect that candidate, explicitly
update the workflow selection if appropriate, and test a known document; the brief does not
declare it an automatic replacement.

For `status-check`, `read_status` definitions are unchanged, while the protocol moves from
`2025-06-18` to `2026-07-28`. Repeat observations with a client supporting the intended protocol
under matching launch and access conditions. For `daily-status`, obtain a complete successful
new observation before comparing tools. A failed capture does not establish tool removal.

The verifier checks definitions and expected report structure. These workflow retests are
instructions for your own harness; no MCP server or tool is executed by the example.

## Language variants and your own data

| Language | Watchlist |
|---|---|
| English | [watchlist.json](watchlist.json) |
| Русский | [watchlist.ru.json](watchlist.ru.json) |
| Español | [watchlist.es.json](watchlist.es.json) |
| Français | [watchlist.fr.json](watchlist.fr.json) |
| 中文 | [watchlist.zh.json](watchlist.zh.json) |

The variants translate titles and workflow descriptions while sharing snapshots, IDs and
selected tools. Use `--lang ru|en|es|fr|zh` to choose the built-in report explanations, checklists
and JSON checklist text and limits. The default is English (`en`). Field names, status codes, diagnostic
reasons, IDs, schemas and hashes are stable. The JSON `language` field records the choice.
Your titles, workflow descriptions and source excerpts are preserved, so pair the option with
the corresponding localized watchlist; inputs are not automatically translated.

To use your own data, copy the bundle to a separate directory, replace its snapshots and edit
the tool selections and workflows. `expected.json` and `verify.py` describe this fixed synthetic
example and will need different expectations for new data. The separate `capture` command can
save HTTP observations with network access; see the [guide](../../docs/upgrade-brief.md).
