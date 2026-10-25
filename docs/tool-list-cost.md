# What a tool list costs

> 🌐 **English** · [Русский](tool-list-cost.ru.md) · [Español](tool-list-cost.es.md) · [Français](tool-list-cost.fr.md) · [中文](tool-list-cost.zh.md)

Every MCP server a client connects lists its tools: a name, a description and an input schema for each. A client
may send the full list, a selected subset or load tools on demand; this study counts the stated full-list representation. A reader of our survey of 12,915
packages (Paul Irolla) asked for this in tokens. HISTOR keeps the current tool list of every server it observes, so
we counted.

## The numbers

For Raju's CI workflow, the [self-contained public example](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) checks total, descriptions and schemas independently with pinned CLI/tokenizer versions. Its forward comparison exits 0; the intentionally reversed comparison exits 1, not evidence of a later regression. These estimates are not invoices: [measurement limits](measurement-limits.md) cover client selection, cache behavior and actual usage records.

Snapshot of 2026-10-19: every package (npm, PyPI) and every remote server in the log with at least one tool, each
with its current list. A list is counted as a client sends it to a model: for each tool `{name, description,
input_schema}` as compact JSON. The tokenizer is `o200k_base` (tiktoken 0.12, the GPT-4o family).

| | Packages | Remote servers |
|---|---|---|
| Servers | 18,204 | 16,170 |
| Tool definitions | 337,449 | 232,427 |
| Tools per server: median / mean / 90th percentile | 8 / 18.5 / 40 | 7 / 14.4 / 31 |
| **Tokens per server: median** | **1,272** | **1,328** |
| mean | 3,812 | 3,412 |
| 90th percentile | 8,361 | 7,425 |
| 99th percentile | 39,612 | 22,294 |
| largest | 304,780 | 547,935 |
| Tokens per tool: median / mean | 129 / 206 | 231 / 237 |
| Servers over 10,000 tokens | 1,447 | 618 |
| Servers over 100,000 tokens | 31 | 9 |

The mean is three times the median: a few servers with hundreds or thousands of tools carry most of the tokens. The
survey's "about 24 tools per server" was the mean over the 7,656 packages that answered on 2026-10-13; over today's
larger set the mean is 18.5 and the median 8.

## In money

The list is resent with every request, so its cost is tokens × price × requests. Per 1,000 requests, at list prices
on 2026-10-19 (OpenRouter):

| One server's list | Tokens | $0.10 per million (Claude Haiku 5.5) | $2 per million (Claude Sonnet 5.5) | Sonnet 5.5 with a prompt cache, requests under 5 minutes apart | The same, more than 5 minutes apart |
|---|---|---|---|---|---|
| median package | 1,272 | $0.13 | $2.54 | $0.13 | $3.18 |
| mean package | 3,812 | $0.38 | $7.62 | $0.39 | $9.53 |
| 90th percentile | 8,361 | $0.84 | $16.72 | $0.86 | $20.90 |
| 99th percentile | 39,612 | $3.96 | $79.22 | $4.06 | $99.03 |

The cache columns use Sonnet 5.5's cache prices: $2.50 per million to write the 5-minute cache (1.25 times the input
price) and $0.10 to read it. The cache keeps a prefix for five minutes after the last request that used it, so 1,000
requests that start less than five minutes apart pay one write and 999 reads; requests further apart write it every
time, a quarter more than no cache at all. The 1-hour cache covers gaps of up to an hour at twice the input price per
write. The tools come first in the prompt, ahead of the system prompt and the conversation: a changed list (another
order, or a refetch after `notifications/tools/list_changed`) writes all of it to the cache again.

A client with five median servers sends about 6,400 tokens of tool definitions before the user's first word. The
largest lists do not fit at all: 304,780 tokens is more than a 200,000-token context window.

Two things cut the bill. Prompt caching, when the client keeps the tool list identical and early in the prompt: on
Sonnet 5.5's list price a cached read costs a twentieth. And loading definitions on demand: the model sees tool names
first and gets a schema when it is about to call that tool.

## What this does not measure

- **One tokenizer.** Models tokenize differently, so other models' counts differ, typically by tens of percent;
  the shape (a median near 1,300 tokens, a long tail) is what carries over.
- **Providers' own text.** A model API adds its own tool-use instructions around the definitions; not counted.
- **What clients actually send.** Some drop fields or shorten long descriptions; some also send the output schema.
  Counted as listed in full, a package's list has a median of 1,390 tokens and a 90th percentile of 9,960.
- **Which servers people use.** Every listed server counts once. A popular server and an abandoned one weigh the
  same.

## Where the tokens are, and what a release changed

A total hides which part of a list grew. A reader (Raju Dandigam) asked for the parts apart and for a check
that runs on every release, so HISTOR now splits each count into the tool names, the descriptions, the input
schemas, and the keys and punctuation around them. The four add up to the total; output schemas and the list as
listed are reported beside it. `@playwright/mcp` as HISTOR last observed it: 3,762 tokens, of which input schemas
3,123 (83%), descriptions 394, names 120.

In the crawl of the night of 22 to 23 October, 109 changes had a list on both sides. 95 made the list heavier, 30 of
them by more than a tenth; with a 10% limit on the total, the descriptions and the input schemas each, 40 would have
failed a check. In 56 of the 95 the input schemas grew more than the descriptions. One package added a `raw` flag to
all 15 of its existing tools: 708 tokens per request, found by the per-property split below. A renamed tool counts as
one removed and one added.

**Every change HISTOR records carries its cost.** `/api/v1/changes/<id>/cost` gives each total before and after,
the tools added and removed, and each tool whose count moved, with its description, its input schema and each
top-level property of that schema apart. `/api/v1/servers/<id>/cost` gives the current list per tool and the totals
of its last changes. For a package a change is a version HISTOR ran that lists different tools, so this is a
per-release history of every package in the log, with nothing to install ([api.md](api.md#changes)).

**Before the release, in your own CI**, `histor tool-cost` counts your server the same way and fails the job when
the total, the descriptions or the input schemas grow past your limit:

```bash
pip install "aimarket-histor[cost]"
# once, and again whenever you accept a growth: commit the snapshot
histor tool-cost snapshot --stdio "node dist/index.js" -o tool-cost.json
# in CI: compare, write the job summary, exit 1 on a regression
histor tool-cost check --baseline tool-cost.json --stdio "node dist/index.js" --max-increase 10%
```

The tools come from `--stdio COMMAND` (started, asked `initialize` and `tools/list`, stopped), `--url URL`
(streamable HTTP, local addresses allowed) or `--tools FILE` (a saved tools/list). Each total is checked on its
own, so a schema that doubles fails the check even when shorter descriptions hide it in the sum. A growth under 50
tokens never fails (`--noise`), `--budget N` caps the whole list, `--update` rewrites the baseline, and
`histor tool-cost diff A B` compares two snapshots or saved lists. In GitHub or Gitea Actions:

```yaml
- run: pipx run --spec "aimarket-histor[cost]" histor tool-cost check --baseline tool-cost.json --stdio "node dist/index.js"
```

The table lands in the job summary.

A [complete example using textokit's two observed lists](../examples/tool-cost/README.md) reproduces a
5,565 → 3,776-token reduction and shows a passing check and a deliberately reversed failing check.
It uses immutable public snapshots and executes no textokit tools.

The [self-contained public tutorial](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) includes the complete fetch script, pinned versions and passing/failing commands, with no repository checkout required.

## Reproduce

[`scripts/tool_list_tokens.py`](../scripts/tool_list_tokens.py) reads an export of the current tool lists (the
query is in its docstring) and writes the summary. Ours, without server names:
[`tool-list-tokens-2026-10-19.json`](tool-list-tokens-2026-10-19.json).
