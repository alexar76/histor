# Changelog

## Unreleased

- Add the offline `histor payer-audit` command and a bounded, read-only Base USDC evidence
  collector. Export transaction-scoped source annotations, explicit heuristic labels,
  settlement checks and coverage limits without excluding payments from totals or inferring
  buyer identities. See [payer audit](docs/payer-audit.md).

## 0.4.1 — 2026-10-25

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
