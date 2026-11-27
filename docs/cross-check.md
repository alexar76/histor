# Cross-checks with other observers

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

HISTOR is not the only service that reads the tool lists of the servers in the MCP Registry. Observers that read the
same servers independently check each other: when they disagree, a server answered them differently (by client,
region or time), or one of them has a bug. HISTOR compares its records with such observers on a schedule and
publishes every comparison, disagreements included, under https://histor.modelmarket.dev/datasets/peers/.

## The observers

| Observer | Method | Schedule | Published at | Since |
|---|---|---|---|---|
| CrawlCheck (crawlcheck.io) | tool-set digests | daily, 12:00 UTC | `/datasets/peers/crawlcheck/` | 2026-11-07 |
| ScraperWatch (scraperwatch.com) | field-level changes | daily, 12:00 UTC | `/datasets/peers/scraperwatch/` | 2026-11-01 |
| MCP Transparency Log (yassinht/mcp-transparency-log) | tool-set digests, 1,000 servers a day | daily, 12:00 UTC | `/datasets/peers/mcp-transparency-log/` | 2026-11-27 |

The list is one configuration file, `/etc/histor/peers.json` on the host (`deploy/peers.json` in this repository).
Its public form is https://histor.modelmarket.dev/datasets/peers/index.json: every observer, its method, sources
and schedule, and the newest day compared. Each day is a folder, `/datasets/peers/<observer>/<YYYY-MM-DD>/`, with
`latest/` pointing to the newest one. `/datasets/xcheck-crawlcheck/`, the address used before the list existed,
leads to the same place.

## Adding an observer

An observer that publishes MTL/1 tool-set digests (below) needs one entry in the list and nothing else:

```json
{
  "slug": "example",
  "name": "Example Observer",
  "contact": "ops@example.org",
  "method": "digest",
  "window_hours": 24,
  "sources": [{"name": "list", "url": "https://example.org/histor-list.jsonl"}]
}
```

A source is any file the observer publishes over https with one row per server read: `name` (the registry name),
`endpoint`, `digest` (SRI or hex) and `read_at` (RFC 3339), in JSON, JSON Lines or CSV. CrawlCheck's change feed is
read as it is published. A row with `digest` null and an `outcome` says the observer could not read that server.
`histor-peers.timer` runs every entry each day (`deploy/peers_check.sh`, `histor/peers.py`); see
[operations.md](operations.md). An observer with a field-level change log in ScraperWatch's format is added with method `field-diff` and the settings its entry in `deploy/peers.json` shows.

A source that lists the whole registry rather than its changes is compared in daily shares. With `"max_rows": 1000`
in its entry, a file of N rows is split into ceil(1.1 × N / 1000) parts by the SHA-256 of `name`, a newline and `endpoint`
(first eight bytes, big-endian, modulo the number of parts), and each day compares the part numbered by the day's
ordinal modulo the number of parts. Every server comes round once in that many days, the report says which part was
compared, and anyone can recompute which rows it held. Each compared row is one request to HISTOR's API, which is why
the share is bounded.

## Method: tool-set digests

The digest is the MTL/1 tool-set digest ([labels.md](labels.md)). For every tool: `name`, `description` (`""` when
absent), `inputSchema` (`{}` when absent) and `outputSchema` only when the server sent an object; tools sorted by
name in UTF-16 code units; RFC 8785 canonical JSON; SHA-256, written as an SRI string. CrawlCheck calls it
`jcs_v1.tools_sha256` in its lockfile specification (https://crawlcheck.io/spec/mcp-lockfile). Checked on 2026-11-07:
both of CrawlCheck's test vectors, six tool sets from HISTOR's records hashed by CrawlCheck's service, and CrawlCheck's
own server give equal digests on both sides.

Both services put `annotations`, `title` and `icons` in a separate extras digest. On 2026-11-07,
[CrawlCheck reported](https://github.com/modelcontextprotocol/registry/issues/82) 30 matching extras results,
including `sha256-G44DGgW7…` for crawlcheck.io/mcp. That sample establishes agreement for those reads.
Extras comparisons also depend on encoding and storage limits. The daily run compares only the tool-set
digest because neither source carries an extras digest.

Each row the observer published is compared with HISTOR's timeline for that server (`/api/v1/servers/<id>`):

| Verdict | Meaning |
|---|---|
| `agree` | HISTOR's digest at the time of the other read is the same (between two of HISTOR's reads, either one counts) |
| `agree_nearby` | not the digest HISTOR had at that time, but HISTOR saw it within the window: the server changed between the reads |
| `disagree` | HISTOR had digests within the window and never this one |
| `histor_no_digest` | HISTOR has no digest for the server within the window (not read, or a list MTL/1 cannot digest) |
| `not_listed` | HISTOR does not list this name and endpoint |
| `theirs_no_digest` | the other observer could not read the list (`digest` null, with an `outcome` saying why) |
| `bad_row` | a field is missing or unreadable |

The window, 24 hours by default, is needed because some servers change their list on every read: on 2026-11-07,
four of seven servers tried got a new digest about every 30 minutes. Two observers then agree only "nearby".

For CrawlCheck the run reads two sources: its public change feed (https://crawlcheck.io/api/v1/mcp/changes, the
newest 500 events, about two and a half days, which is why the run is daily) and its weekly shared list of 30
servers, re-read every Monday before 11:30 UTC (https://crawlcheck.io/data/xcheck/histor/latest.jsonl, dated editions
with a signed statement beside each). Each day's folder holds, per source, the file as read (`<source>.input.*`),
the comparison (`<source>.report.json`, `<source>.report.md`) and `SHA256SUMS`.

To run the comparison yourself, with Python 3.11 or later and nothing installed:

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/data/xcheck/histor/latest.jsonl --out report.json --md report.md
```

Exit status 0 means no `disagree`, 1 means at least one, 2 that the input could not be read.

First results, 2026-11-07: the shared list's first edition (sha256 `9271b8ac…`), 30 servers, 30 `agree`; the change
feed, 16 rows with the digest, 15 `agree`, 1 `histor_no_digest`, 0 `disagree`.

## Method: field-level changes

ScraperWatch publishes a daily log of tool changes on a fixed panel of remote endpoints, as JSON paths with old and
new values. Every day HISTOR checks the newest day of that log in both directions: every change in their log
against HISTOR's records path by path, and every HISTOR change on their panel that their log does not list, each
with a reason (`histor/peer_xcheck.py`, [operations.md](operations.md)). It reads HISTOR's database, so it runs in a container of its own with the database settings. Its parameters (log and
panel addresses, the robots.txt agent, run time and first run) are in its entry in the list. Until 2026-11-07 it ran
weekly, after the dataset export.

## Witnesses

Seven witnesses co-sign HISTOR's checkpoints (c2sp.org/tlog-witness, cosignature/v1). HISTOR pushes its checkpoint
to each of them every 15 minutes, 96 requests a day, below the 144 a day (`qpd`) the log was listed with; a
witness that answers 429 is left alone for the time its `Retry-After` names, or six hours. The log is on the witness network's staging list `log-list-10qps-4klogs.1`
(witness-network.org has no production list yet); six of the witnesses pick it up from that list, and CrawlCheck
configured it directly. Mullvad, Markovian and TrustFabric have co-signed since 2026-11-04, CrawlCheck since
2026-11-07, GDB Security, Geomys and IPng since 2026-11-19.

| Witness | Operator | vkey |
|---|---|---|
| `witness.stagemole.eu` | Mullvad VPN AB | `witness.stagemole.eu+67f7aea0+BEqSG3yu9YrmcM3BHvQYTxwFj3uSWakQepafafpUqklv` |
| `markovianprotocol.com/witness` | Markovian Protocol | `markovianprotocol.com/witness+41b8827f+BJKLl8rBYM0oUxSaWh9rjTldTprEpYj/SWVIGgacA9XC` |
| `staging.witness.transparency.goog/ring-any-bells` | TrustFabric | `staging.witness.transparency.goog/ring-any-bells+2e1a8dc9+BG5JTpLc3FJtwzgh1Uv+Qelz9qeOH2bfWjS1s0s+y4rL` |
| `crawlcheck.io/witness` | CrawlCheck | `crawlcheck.io/witness+f6b056e2+BCYE6WcaJVekqxEETT9IF/pKvxP7yZM6dLW65YDWpQpE` |
| `witness.gdbsecurity.com` | GDB Security | `witness.gdbsecurity.com+a1398f73+BAbIijGonVIk3ikDac+KKlSm1ELHOzcddhjjB6zB4BNf` |
| `witness.navigli.sunlight.geomys.org` | Geomys | `witness.navigli.sunlight.geomys.org+a3e00fe2+BNy/co4C1Hn1p+INwJrfUlgz7W55dSZReusH/GhUhJ/G` |
| `krabbel.ct.ipng.ch` | IPng Networks GmbH | `krabbel.ct.ipng.ch+4b10dbd8+BO4sLOtnhktlqMCejDx9C5LDd+/xZxbmBb6BP9ZD2Def` |

The newest checkpoint, https://histor.modelmarket.dev/log/checkpoint, carries only the log's signature, because the
witnesses have not seen that size yet. A checkpoint the witnesses co-signed is served with their lines at
`/api/v1/log/checkpoint/<size>`, for example https://histor.modelmarket.dev/api/v1/log/checkpoint/2836615 (log signature and seven cosignatures). The newest such checkpoint is always at https://histor.modelmarket.dev/api/v1/log/checkpoint/cosigned (its size in the `Histor-Tree-Size` header): prove an entry against that size. The HESTIA agent `histor.witnessed@v1` does exactly that for a tool set. Each line verifies under
the witness's vkey above. TrustFabric, Geomys and IPng also have ML-DSA keys; HISTOR keeps only the Ed25519 cosignatures.
