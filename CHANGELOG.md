# Changelog

## Unreleased

- Bundle the published WARDEN 0.25.0 archive whose dist digest matches the accepted MOMUS build,
  replacing 0.22.3. Ordinary rebuilds now retain ruleset v19. Both Docker targets and `make scanner`
  verify the archive hashes, lock, installed version and exact build digest; stale scanner copies fail
  the build. The observer image test now checks the delegated image manager used by installation.
- **Two malloc arenas (`MALLOC_ARENA_MAX=2`, compose; `HISTOR_MALLOC_ARENA_MAX` overrides).** glibc gave nearly
  every thread of the web threadpool and the crawl its own arena and kept freed memory in each: prod held 40
  arenas = 528 MiB of a ~630 MiB heap at rest, grew to ~700 MiB without a crawl, and was OOM-killed at its
  1 GiB limit six times between 2026-11-06 and 11-08, interrupting crawls each time. Prod's limit is also
  1.5 GiB now (`HISTOR_MEM_LIMIT` in the host .env).
- **`/check` answers a GET and reads `url` as the endpoint.** Between 2026-10-27 and 11-08 every outside
  GET of `/api/v1/check` got 405: a server's own bot asking `?url=` about its endpoint, another author
  trying `?url=` and `?endpoint=`, and people who followed a link from PyPI. `GET /api/v1/check?endpoint=`
  (or `url=`, `package=`, `name=`, optional `toolSetDigest=`) returns the same signed answer, writes
  nothing (no contribution, no unlisted-host count) and is sent with `Cache-Control: no-store`. A browser
  that names no server is redirected to the form at `/check`. A POST with `url` and no `endpoint` is read
  as the endpoint, and the 400 for a missing server now shows a POST and a GET example.
- **READMEs: the `/check` example prints the answer.** The English one read `.verdict`, a field the answer
  does not have, and printed `null`; the four translations sent a placeholder that is not JSON and got a
  400. All five now run as written and add the GET lookup.

## 0.7.0 — 2026-11-08

- **Cross-checks with other observers come from one list**: `/etc/histor/peers.json` (template `deploy/peers.json`).
  Each observer is one entry: slug, name, contact, method, sources and window. Adding one is a configuration change.
  `histor/peers.py` runs every entry, including "field-diff" peers whose settings used to be a constant in
  `peer_xcheck.py`. ScraperWatch's cross-check now runs daily from this list instead of from the weekly dataset
  export. The digest comparison moved into the package as `histor/digest_xcheck.py`; `scripts/xcheck_digests.py`
  loads it by path, so it still runs from a checkout.
- **MTL/1.2** (PROFILE.md "Revision 1.2"): **labels name the revision that decided them, and why a set has no digest.** Every label
  carries `profileRevision` ("1.2.0"). A read set without a digest says `toolSet.noDigest`
  `{"reason": "not_digestible", "code": …}`, and a continuity label names each side without one
  under `noDigest` (`current` / `prior`). A failed read is `not_observed`, and the prior side carries
  the revision its own label named. The server API returns `noDigest` (reason). The server page adds
  the code, the revision and `decidedAt`, so a set 1.0 refused (before 2026-10-31) is told from one
  the current rules refuse (agent-control-standard#38).
- **A package whose set an older revision declined runs once more.** The version did not change, so
  nothing else would run it, and its record kept 1.0's "not digestible" for sets 1.1 digests (138 on
  2026-11-06). Each run records the revision that judged it (`package_signals.mtl`, not a security
  event), and an `undigestible` package judged by the current revision is settled until its version,
  observer or the revision changes. Crawl stats: `packages.undigestedAgain`.
- `/health` answers `HEAD`. Badge services probe with `HEAD` and got 405, so badges showed the service as down.

## 0.6.2 — 2026-11-04

- **The log is also a tiled log (c2sp.org/tlog-tiles).** Under `https://<origin>/` (the public instance:
  `https://histor.modelmarket.dev/log/`) HISTOR serves `checkpoint`, the Merkle tree tiles
  `tile/<L>/<N>[.p/<W>]` and the entry bundles `tile/entries/<N>[.p/<W>]`, read straight from the stored
  complete-subtree nodes. That is where a witness that found the log in a witness-network list looks for it,
  so it can poll the log and check consistency itself instead of seeing only what the log pushes.
  - Every entry of a bundle is hashed and compared with its leaf before the bundle is served; a mismatch, or
    an entry over 65,535 bytes, is refused with 500, never served wrong.
  - Tile paths are parsed strictly (one canonical spelling per tile); tiles the tree does not hold yet answer
    404; served tiles are cached as immutable.
  - Checked with `golang.org/x/mod/sumdb/tlog` on a 70,000-leaf tree: the root rebuilt from the tiles,
    consistency proofs from four sizes, inclusion of entries. Spec §4.5; `tests/test_tiles.py` (both backends).
- The log is listed on the staging witness network (witness-network.org, 2026-11-04) and co-signed by
  outside witnesses; spec §8.2.

## 0.6.1 — 2026-11-04

- The package description gives new MCP users a first query for their own dependency and
  a direct link to the browser's package search.
- The browser can copy a Markdown note from the MongoDB read-only comparison. Server
  records use descriptions and search metadata specific to the endpoint.

## 0.6.0 — 2026-11-03

- **The datasets page cites each snapshot by its own Zenodo version DOI.** It used to give the concept
  DOI next to the newest snapshot's name; the concept DOI opens the newest version, where the same query
  gives other numbers (the 20 October host count, 2,489 of 6,822, becomes 2,976 of 7,213 on 2 November).
  - `dataset_index.py --zenodo` reads the versions Zenodo lists into `zenodo.json` beside the snapshots;
    Zenodo unreachable or answering oddly leaves the file as it was.
  - The export runs it after each snapshot, and `histor-zenodo-reminder` each morning, so a version
    archived by hand reaches the page the next day.
  - A snapshot not archived yet points to the newest archived one and to its own signed manifest; every
    snapshot in the list carries its DOI, and archived snapshots older than the eight kept here stay listed.
  - The dataset card, the READMEs and `CITATION.cff` say to cite the version DOI next to a number.

- **The crawler follows robots.txt (RFC 9309).** Before an endpoint is read, its origin's robots.txt is
  asked for the product token `histor` (else `*`). A path it does not allow gets no MCP request; the
  attempt is recorded as `robots-disallowed`, or as `robots-unreachable` when robots.txt answers 5xx or
  not at all. Details:
  - Matching: the longest match, Allow on a tie, `*` and `$`, percent-encoding normalised.
  - Fetching: up to five redirects, 512 KiB, a 4xx meaning no robots.txt.
  - Reuse: a file is reused for 24 h; while an origin is unreachable its last file still applies, and
    after 30 days unreachable with no file, robots.txt counts as absent.
  - State per origin lives in `robots_txt` (migration 019).
  - The detail names the deciding rule and the two lines that let HISTOR in. A server observed before
    keeps its history and gets one continuity label with the status. Reading resumes when the site
    allows it.
  - The server page shows "not observed: robots.txt does not allow HISTOR", and `/api/v1/check` says so
    in its `note`.
  - One-off reads a person starts (`histor tool-cost`) are not crawling and do not consult it.
- **Server cards as declarations.** HISTOR reads each remote endpoint's MCP server card where robots.txt
  allows it: `<endpoint path>/server-card` and `/.well-known/mcp/server-card.json`.
  - Schedule: daily, a week after none was found; no redirects; 1 MiB.
  - What counts: a card listing every tool with a name and an `inputSchema` is digested like an
    observation and kept as a declared set with `source: "card"`. Registry and file syncs leave such sets
    alone. A card naming endpoints counts only for those; a host-wide card naming none counts only on a
    host with one remote endpoint.
  - Withdrawal: a missing card or one without tools withdraws its set; a failed read does not.
  - Where it shows: `serverCard` on `/api/v1/servers/<id>`, `target.serverCard` and the note on
    `/check`. The page shows the card beside the served set, with the tool names only one of them has,
    in five languages.
  - Storage: `server_cards` (migration 020).
  - A third path, `/.well-known/mcp.json`, read after the other two. It has no standard behind it, but on
    2026-11-02 34 of 400 sampled hosts with neither standard card served one there, 4 with a full tool list.
    Such files also name their endpoint as `mcpUrl`, `remote.url`, `servers[]`, `aliases[]` or a map of
    `endpoints`, all read now. Migration 021 makes earlier `absent` and `robots` reads due at once.
  - Why: sites that keep HISTOR off their MCP path, and servers that list tools only with a key, still
    get the operator's own account on record.
- **A 402 says why HISTOR did not read.** `pay-per-crawl` when the site charges crawlers for each read
  (`crawler-price` header or "pay per crawl" in the body), `payment-required` for an x402 endpoint where every
  call is paid, `http-402` otherwise.
  - HISTOR pays for no read and keeps a user agent that names it.
  - The server keeps its history. Its page and `/check` say that HISTOR does not read it, and the detail names the
    price.
  - Both statuses are asked again a day later at most, like 401 and 403.
- The crawler FAQ on the landing page was wrong about frequency and requests. It now says: every 6 h
  (30 min after repeated changes), robots.txt first, `server/discover` before `initialize`, and how to
  allow or refuse HISTOR. Five languages.
- **A guide to MongoDB MCP's read-only mode, with a tool-list comparison.** `/mongodb-mcp-read-only-tools.html`
  shows which tools `--readOnly` hides and compares two pasted `tools/list` answers in the browser (nothing is
  sent anywhere) to show which tools a client gains or loses, next to HISTOR and RugSnare observations. The
  app serves it for GET and HEAD and lists it in the sitemap when the landing folder holds it; the home page
  links it.

## 0.5.3 — 2026-11-01

- **Legacy MCP servers are read again.** In 0.5.2 the remote reader opened with the 2026-07-28
  `server/discover` and fell back to `initialize` only for HTTP 400/404/405 with a JSON-RPC
  −32600/−32601/−32602 under our own request id. Real legacy servers answer otherwise: the Python
  SDK 1.x sends `"id": "server-error"`, many gate every method except `initialize` and `tools/list`
  behind 401/403, some list only older versions or answer with a body that is not JSON-RPC. The public
  log stopped reading 2,195 servers from 2026-10-30. The reader now follows the 2026-07-28 Streamable
  HTTP rule: a first modern request refused with a 4xx that carries no recognized modern error means a
  legacy server, and it falls back to `initialize` once, also when the version list holds only
  pre-2026-07-28 versions, when a discover reply has no `supportedVersions`, or when the first modern
  `tools/list` page is refused that way. Rate limits, timeouts, 5xx, redirects, recognized modern
  errors and ambiguous bodies still stop the read. The same reader serves `histor tool-cost`, `/check`
  and self-hosted crawls ([docs/mcp-compatibility.md](docs/mcp-compatibility.md)).
- **Cross-check with another public log.** `python -m histor.peer_xcheck OUTDIR` compares HISTOR with
  ScraperWatch's daily MCP tool-change log in both directions (their changes seen field by field in
  HISTOR; HISTOR's changes on their panel, with the reason each is missing from their diff, robots.txt
  read per RFC 9309) and writes README, JSON and CSV; `deploy/export_dataset.sh` runs it after each
  weekly snapshot or alone with `--peers` ([docs/operations.md](docs/operations.md)).
- The sandbox scanner vendors WARDEN 0.22.3 (was 0.20.0): MTL/1.1 digests match the log's.
- The MCP registry descriptor's description is 90 characters; the registry allows at most 100.

- **HISTOR Watch for teams is for sale: $29 for 30 days, by card.** `/watch` and
  `/api/v1/watch/*`: a team names up to 100 servers and an HTTPS endpoint, pays on Stripe
  Checkout, and gets each change pushed, signed, from the moment it paid; a manage link (token
  kept only as its hash, no account, no e-mail) shows the period and the secret, edits the list,
  sends a test event and renews. Amounts are checked against the payment row HISTOR wrote, never
  the event; refunds take back their period, disputes end the subscription; a test key is
  refused in production and a live key elsewhere. `HISTOR_WATCH_DEMO=1` shows the plan with
  payment closed, which is how histor.modelmarket.dev runs for now. Migration 018. Tested end to
  end against the Stripe sandbox on Postgres ([docs/watch.md](docs/watch.md)).
- Change events carry `text`, one line a Slack incoming webhook posts, with names escaped.

- **HISTOR Watch for teams.** A webhook subscription may set `"source": "changes"` and
  `"watch": [server ids]`; it then receives each recorded definition change of those servers
  (`histor.change/v1`: added/removed/modified tools, new outside addresses, label id, links to
  the diff and the page), signed, in order, at least once. The security-event feed stays the
  default and keeps its cursors. [docs/watch.md](docs/watch.md).
- **Webhooks connect to a checked public address.** Deliveries used to connect by name; they
  now resolve the destination with `histor.netguard`, refuse it when any address is not public
  (IPv4-mapped, 6to4 and NAT64 forms judged by the IPv4 they carry), and connect to the checked
  address with Host and SNI kept.
- **`/api/v1/changes?after=<id>`** reads oldest first from a cursor and returns `nextCursor`;
  `watch=<id>,…` limits it to up to 100 servers — the incremental read for mirrors, registries
  and feed consumers. [docs/data-feed.md](docs/data-feed.md).
- `after`, `before` and a change id above 2^63−1 are refused (422/404) instead of overflowing
  the SQLite driver into a 500; Postgres answered an empty page. The docs now state the
  `limit` range (1–200, default 50) and what `nextCursor` is on an empty page.
- MTL/1.1 digests (awr/adoption/mcp-trust-label/PROFILE.md, Revision 1.1): every number in a tool
  schema is read as an IEEE 754 double and written as RFC 8785 says (`0.5`, `1e+21`, `5` for `5.0`),
  an empty set is digested, and tools that share a name are ordered by their canonical bytes. Before,
  5,162 servers HISTOR had read had no tool-set digest: 4,941 for a fraction or exponent, 21 for a
  large integer, 140 empty, 57 repeating a name (prod census 2026-10-31); only the 3 with a nameless
  entry, or a number with no double (`1e400`), stay without one. Integers-only sets keep their bytes
  and digests. A set that only gains its first digest is not recorded as a change.
- A continuity label after a set with no digest names that set's own reason, or none when it is not
  known. It used to say `MTL-NUM-001` whatever the reason was (GenAI-Security-Project/agent-control-
  standard#38).
- `/api/v1/servers?state=unobserved` now means no tool list read; a list read without a digest is
  `state=undigested`, and `/api/v1/stats` counts it as `undigested`. `unobserved` used to hold both.

- `GET /api/v1/servers/<id>/fingerprints?format=vercel-ai|heldfast|agentavow-v1`: the current tool
  set as one fingerprint per tool in the Vercel AI SDK's `fingerprintTools` format, heldfast's
  lockfile digest or AgentAvow's `agentavow.mcp-tool-definition.v1`, computed from the stored tool
  set and its extra members (`histor.fingerprints`). A Vercel AI SDK app compares what it was served
  with `detectToolDrift(fingerprints, …)`. Checked against each project's own implementation and
  vectors; `tests/fingerprint-vectors.json` holds values those implementations produced.

## 0.5.2 — 2026-10-30

- Publish C2SP checkpoints and witness support (`histor-tlog/2`), including consistency-proof
  verification, dedicated witness keys, cosignature storage and migration 017.
- Add `witness-submit`, verified multi-witness submission and per-witness state.
- Reject non-ASCII or out-of-range checkpoint sizes and stop reading oversized HTTP request
  bodies at the configured limit instead of buffering the complete request first.
- Keep distribution, runtime, citation and MCP registry descriptor versions aligned so these
  changes can ship as a new release rather than diverging at the published 0.5.1 version.

## 0.5.1 — 2026-10-29

- `histor mcp --remote [URL]` and the new console script `aimarket-histor` (what an MCP registry
  client runs with `uvx aimarket-histor`): the three stdio tools — `histor_changes`, `histor_check`,
  `histor_server` — query a public HISTOR instance's capability endpoint (default
  https://histor.modelmarket.dev, env `HISTOR_MCP_REMOTE`) instead of a local log. Until now the
  only mode was local, so a stranger who installed the package from the registry talked to an
  empty log. `--local` keeps the old behaviour; `histor mcp` without flags is unchanged.
- `server.json` for the official MCP registry (`io.github.alexar76/histor`) and the `mcp-name`
  marker in the README.
- Hugging Face dataset card: `task_categories`, `task_ids`, `annotations_creators`,
  `source_datasets`, `multilinguality`, two more tags, and a size bucket computed from the largest
  file (the card said 10K<n<100K for a 115,897-row file).
- README: an Install section (`pip install aimarket-histor` — the PyPI project `histor` is
  unrelated), `histor audit` as the first command, a `/check` example that runs as written;
  desk routes answer HEAD; `/datasets/` has og:image, a "start here" hint, the `load_dataset`
  one-liner and a note on which snapshot Zenodo holds; JSON-LD on the landing; `CITATION.cff`.
- Every change carries the server's own version (`serverInfo`) and the version the MCP registry
  listed, on both sides (`from_/to_server_version`, `from_/to_registry_version`; migration 016),
  and every observation the registry version. Equal pairs mark a list that moved at an unchanged
  version (agent-control-standard #38). Server versions are filled for older changes from the
  recorded runs; registry versions start on 2026-10-28. In the API, the timeline and the
  snapshot (`changes.jsonl.gz`, `package_observations.registryVersion`, manifest `changeVersions`).
  Live on histor.modelmarket.dev since 2026-10-28. This entry was listed under 0.5.0, but the
  0.5.0 on PyPI was built on 2026-10-27, before it landed.

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
