# Cross-checking with another observer

[English](cross-check.md) · [Русский](cross-check.ru.md) · [Español](cross-check.es.md) · [Français](cross-check.fr.md) · [中文](cross-check.zh.md)

HISTOR is not the only service that reads the tool lists of the servers in the MCP Registry. CrawlCheck
(crawlcheck.io) reads them too, and since 2026-11-07 it publishes the same tool-set digest HISTOR uses. Two observers
that read a server at about the same time and compute the digest independently check each other: a disagreement
means one of them was served something else (a server that answers differently by client or region), or one of
them has a bug.

## The digest

It is the MTL/1 tool-set digest ([labels.md](labels.md)). For every tool: `name`, `description` (`""` when absent),
`inputSchema` (`{}` when absent) and `outputSchema` only when the server sent an object; tools sorted by name in
UTF-16 code units; RFC 8785 canonical JSON; SHA-256, written as an SRI string. CrawlCheck calls it
`jcs_v1.tools_sha256` in its lockfile specification (https://crawlcheck.io/spec/mcp-lockfile).

Checked on 2026-11-07: both of CrawlCheck's published test vectors, six tool sets from HISTOR's records hashed by
CrawlCheck's service, and CrawlCheck's own server give equal digests on both sides. `annotations`, `title` and
`icons` are outside this digest. Both services keep them under a second digest, built differently, so those two do
not match and are not compared.

## The daily run

Every day at 12:00 UTC, HISTOR reads CrawlCheck's public change feed
(https://crawlcheck.io/api/v1/mcp/changes, its newest 500 events, about two and a half days) and compares every
event that carries the digest with HISTOR's timeline for that server (`/api/v1/servers/<id>`). The result is
published at https://histor.modelmarket.dev/datasets/xcheck-crawlcheck/ in a folder per day: the feed as it was
read (`feed.input.json`), the comparison (`feed.report.json`, `feed.report.md`) and `SHA256SUMS`. `latest/` points to
the newest day. Disagreements are published as they are, not filtered.

| Verdict | Meaning |
|---|---|
| `agree` | HISTOR's digest at the time of the other read is the same (between two of HISTOR's reads, either one counts) |
| `agree_nearby` | not the digest HISTOR had at that time, but HISTOR saw it within 24 hours: the server changed between the reads |
| `disagree` | HISTOR had digests within 24 hours and never this one |
| `histor_no_digest` | HISTOR has no digest for the server within 24 hours (not read, or a list MTL/1 cannot digest) |
| `not_listed` | HISTOR does not list this name and endpoint |
| `bad_row` | a field is missing or unreadable |

The 24-hour window is needed because some servers change their list on every read: on 2026-11-07, four of seven
servers tried got a new digest about every 30 minutes. Two observers then agree only "nearby".

## Run it yourself

```bash
python3 scripts/xcheck_digests.py https://crawlcheck.io/api/v1/mcp/changes --out report.json --md report.md
```

Python 3.11 or later, standard library only. The input is a file or an https URL: CrawlCheck's change feed as
published, or any list of rows with `name` (the registry name), `endpoint`, `digest` (SRI or hex) and `read_at`
(RFC 3339) in JSON, JSON Lines or CSV. Exit status 0 means no `disagree`, 1 means at least one, 2 that the input
could not be read.

## First result

2026-11-07, the first run on the feed: 8 events with the digest, 7 `agree`, 1 `histor_no_digest`, 0 `disagree`.
