# Changelog

## 0.5.0 — 2026-10-27

- Add `upgrade-brief --lang ru|en|es|fr|zh` for Markdown explanations and tool/workflow
  checklists, including checklist text and limits in JSON. Use English as the default in the
  CLI, Python API and renderer fallback, and record the selected language in `report.json`. Machine status
  codes, diagnostic reasons, schemas, IDs and hashes stay stable; supplied titles, workflow
  descriptions and definition excerpts remain verbatim. Verify all five report languages
  with the reproducible offline example.

- Real HISTOR imports can declare unknown historical extras with `extrasCoverage: "unknown"`.
  The personal brief preserves observed core definition changes, skips unsupported annotation
  comparisons and leaves overall comparability unknown instead of treating missing extras as
  unchanged or removed.

- Support MCP `2026-07-28` in the remote reader, package sandbox observer, `tool-cost` stdio
  client and local `histor mcp` server. Probe discovery before legacy initialization; send
  per-request modern metadata and preserve bounded legacy fallback. Recognized modern errors
  never trigger a downgrade, and interactive/incomplete results never become empty successful
  observations. Existing network, deadline and sandbox boundaries remain in force.
- Keep observed protocol versions in API timelines and package dataset rows, including separate
  timeline rows when the same tool list is read under another protocol. Sandbox observer 5
  preserves completed observations from observers 3 and 4 while retrying their failures.
- Add the local `histor upgrade-brief` prototype: an explicit watchlist connects definition,
  schema and annotation changes to chosen workflows, produces a retest checklist and records
  missing observations and changed launch/protocol context. Reports run offline; the separate
  `capture` subcommand reads an HTTP endpoint into a new unsigned local snapshot. Include a
  fully synthetic example bundle, [guide](docs/upgrade-brief.md) and
  [reproducible walkthrough](examples/upgrade-brief/README.md) in English, Russian, Spanish,
  French and Chinese. Localized watchlists share the same observations; a standard-library
  verifier checks expected findings, input hashes and byte-for-byte repeatability across five
  languages.
- Preserve JSON type differences in schema diffs, so `const: true` changing to `const: 1`
  is visible to the upgrade report and existing continuity summaries.

## 0.4.2 — 2026-10-27

- **Reviewed launch defaults for the capture gaps found with RugSnare.** Filesystem receives only
  the container's `/work/app`; Azure DevOps uses a synthetic organization and envvar auth;
  LaunchDarkly starts on stdio with a per-run placeholder; Figma starts with `--stdio` and a
  placeholder variable. Previously failed captures retry with the profile, including on an
  unchanged package version, and retain their earlier history. Explicit registry arguments take
  precedence. No network route or real credential is introduced; a service requiring either can
  still remain unobserved. See [capture conditions](docs/capture-conditions.md).
- **Do not substitute a requested package version for an unreported observed version.** Failed
  resolution, and responses that omit the version, now leave it unknown rather than attributing
  a capture to the version in the request. Existing stored observations are not rewritten.

- **Every package observation records how the run was launched.** The same version can serve other
  tools under other launch args (mongodb-mcp-server with `--readOnly` has nine write tools fewer) or
  when it reaches the network at startup (@netlify/mcp registers `netlify-coding-rules` only after its
  topic fetch), so a list without its launch cannot be compared with another capture (RugSnare,
  2026-10-26). Migration 015 adds `observations.launch`: the observer version, image, runtime and
  entry, the network (`no route out`) and, when HISTOR started a server again with configuration,
  the env variable names (never values), args and pins it supplied. The timeline gives it per row,
  the desk shows what was supplied, and `package_observations.jsonl.gz` carries it as `launchJson`.
  Until now these facts were kept only for a target's last run. Older rows stay `null`.
- **Every observation of a package records the version it ran.** Only the target kept a version (the
  last one), so a history could not be matched to releases: cross-checking 26 packages against the
  RugSnare pins on 2026-10-26 took npm publish times to guess which version served which list.
  Migration 014 adds `observations.package_version` and, on a change, `from_package_version` (the
  last version that served the old list) and `to_package_version`. The server page's timeline gives
  `packageVersion` per row and no longer folds two versions that served the same list into one row;
  `/api/v1/changes` and `/cost` give both versions; the desk shows them. Snapshots gain
  `package_observations.jsonl.gz`, every run of a package with its version, and the two version
  fields in `changes`. Rows from before the migration stay `null`: the version they ran was not
  recorded, and a guess is not data.
- **Pages traced before `gvisor-trace/9` are traced once more.** Readers 2–8 did not record renames,
  links or creat, nor report `.pth` files or code written into the installed package after install
  as persistence (Vulnetix GCVE-110-PYPI-2026-022091), and nothing in their traces says which pages
  missed something. An ok, complete page by such a reader is now run again once; the new trace is by
  the current reader, so it is never picked again. These runs share, with an older observer's
  failures, at least half of what is left of a crawl, so never-observed packages keep the rest
  (`stats.packages.reread` / `rereadable`). On 2026-10-26 that was 3,916 pages. The owner chose the
  sandbox load over leaving the pages as they were.

## 0.4.1 — 2026-10-25

- Add the offline `histor payer-audit` command and a bounded, read-only Base USDC evidence
  collector. Export transaction-scoped source annotations, explicit heuristic labels,
  settlement checks and coverage limits without excluding payments from totals or inferring
  buyer identities. See [payer audit](docs/payer-audit.md).

- **Fix: the crawler took the new trace reader for an old one.** The sandbox answers `gvisor-trace/9`
  but the crawler still called `gvisor-trace/8` current, so it re-traced every 9 run that listed a
  relative write, on every crawl. 9 is current now; an 8 trace keeps its verdict, since nothing in it
  shows what 9 adds.
- **Fix: the crawler's address guard accepted IPv6 forms of private IPv4 addresses.** It unwrapped
  IPv4-mapped addresses only; NAT64 (`64:ff9b::/96`, which Python calls global) and 6to4 forms are
  now judged by the IPv4 address inside, so a registry endpoint resolving to one is not dialled.
- **Fix: the sandbox observer missed files moved into place** (Vulnetix GCVE-110-PYPI-2026-022091,
  CWE-693). The trace runtime did not record `rename`, `renameat2`, `link`, `linkat`, `symlink`,
  `symlinkat` or `creat`, and the parser ignored them, so a file renamed onto `.bashrc` went
  unreported and the run was marked complete; writes under `/work` (where the package is installed,
  e.g. a `.pth` file) were never reported. `sandbox/install.sh` now records those calls, the parser
  judges each target like a write (a relative target with no printed directory leaves the run
  incomplete and still reports a persistent name), `.pth`/`sitecustomize.py`/`usercustomize.py`
  written outside `/tmp` and code written into the installed package after install are reported, and
  a trace runtime that does not record every needed call is treated as no trace runtime at all
  (`trace-unavailable`). Trace format checked against runsc's own strace output. Observer
  `gvisor-trace/9`.

## 0.4.0 — 2026-10-24

- Poll remote MCP endpoints independently of registry discovery and package sandbox runs.
  Target intervals are six hours normally and thirty minutes after at least two observed
  changes in seven days. Persist scheduling and host backoff, and report actual gaps and backlog.
- Scan and save completed remote observations without waiting for slower endpoints. Group
  results already ready together and bound outstanding observations to twice the worker count.
- After HTTP 429, wait at least the normal remote interval before retrying, including for
  frequently changing endpoints. Repeated failures back off, and the hostname shares the pause.
- Apply migration 013 for separate run kinds, durable queue state and explicit run membership.
  Keep interrupted package plans and daily-run reporting separate from remote checks.
- Share a persistent UTC-day classifier-call ceiling across independently scheduled lanes;
  cached verdicts remain free. Invalidate a previous verdict when the observed tool set changes.
- Export datasets from a consistent database snapshot while polling continues.

Intervals are scheduling targets, not guarantees. Polling can miss every intermediate state
between observations, including A→B→A. See [remote polling operations](docs/remote-polling.md).
