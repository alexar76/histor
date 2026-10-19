# What a tool list costs

> 🌐 **English** · [Русский](tool-list-cost.ru.md) · [Español](tool-list-cost.es.md) · [Français](tool-list-cost.fr.md) · [中文](tool-list-cost.zh.md)

Every MCP server a client connects lists its tools: a name, a description and an input schema for each. The client
puts that list into the prompt of every model request, whatever the user asks. A reader of our survey of 12,915
packages (Paul Irolla) asked for this in tokens. HISTOR keeps the current tool list of every server it observes, so
we counted.

## The numbers

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

| One server's list | Tokens | $0.10 per million (Claude Haiku 5.5) | $2 per million (Claude Sonnet 5.5) | Sonnet 5.5, read from cache ($0.10 per million) |
|---|---|---|---|---|
| median package | 1,272 | $0.13 | $2.54 | $0.13 |
| mean package | 3,812 | $0.38 | $7.62 | $0.38 |
| 90th percentile | 8,361 | $0.84 | $16.72 | $0.84 |
| 99th percentile | 39,612 | $3.96 | $79.22 | $3.96 |

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

## Reproduce

[`scripts/tool_list_tokens.py`](../scripts/tool_list_tokens.py) reads an export of the current tool lists (the
query is in its docstring) and writes the summary. Ours, without server names:
[`tool-list-tokens-2026-10-19.json`](tool-list-tokens-2026-10-19.json).
