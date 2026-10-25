# Reading HISTOR measurements

> 🌐 **English** · [Русский](measurement-limits.ru.md) · [Español](measurement-limits.es.md) · [Français](measurement-limits.fr.md) · [中文](measurement-limits.zh.md)

Reader-feedback clarification, 2026-10-25. These distinctions apply to observations and comparisons, not guarantees about every installation or live server. A local source change, deployed service and published Python distribution are separate states.

## Installation failure is an observation, not a diagnosis

`sdk-incompatible` is an exit-text heuristic. The old classifier also included missing extras and MCP 1/Pydantic failures when a traceback mentioned FastMCP; it did not prove that an unbounded dependency installed MCP 2. A diagnostic `mcp<2` rerun changes the environment and never replaces the primary fresh-install observation or its tool set.

The reconstructed 292-package cohort was rechecked on Linux in gVisor without our forced `mcp<2`: 12 newer releases listed tools, two unchanged versions worked with their declared MCP extra, 276 still failed, and two needed real authentication or a prepared project index. Eleven newer-release successes have a failing fresh old-version control; one old release was unavailable. This does not establish 292 MCP 2 failures, security or complete authenticated functionality. [Package follow-up and controls](https://dev.to/alexar76/we-rechecked-292-failing-mcp-packages-12-newer-releases-now-list-tools-p72).

## Token estimates and CI

Check total definitions, descriptions and input schemas separately: one shrinking part can hide growth elsewhere. Keep a reviewed baseline; do not regenerate it immediately before every check. The [copyable public example](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5) uses published CLI 0.3.1 and tokenizer 0.14.0, verifies stored snapshots, and executes no textokit tools. Its real change is 5,565 → 3,776 tokens; reversing the comparison intentionally exits **1**, not evidence of a later regression. The ordinary comparison exits **0**; invalid input exits **2**.

These count a specified compact representation with `o200k_base`. Client wrappers, selected tool subsets, on-demand loading, tokenizer choice and cache writes/reads affect real usage. Historical price tables are scenarios at their stated date, not invoices or current prices; an invoice needs the client's actual usage records. [Cost guide](tool-list-cost.md).

## Numeric changes are not automatically cosmetic

Whitespace-only and number-masked views help review; they do not replace the literal canonical MTL digest or continuity decision. A price changing from $0.002 to $0.02 is numeric and potentially material: [change 13185](https://histor.modelmarket.dev/api/v1/changes/13185). Do not infer harmlessness from a numeric-only category. Distinguish servers from transitions, descriptions from schemas/tool membership, overlapping flags from mutually exclusive categories, and missing raw states from unchanged ones. [Labels and digests](labels.md).

## Payments do not identify customers

The source-only `histor payer-audit` command separates settlement, behavioural signals and transaction-scoped source annotations. The PyPI check on 2026-10-25 returned **0.3.1**; the command was not in that published distribution. A version string in a local checkout does not establish publication. [Run the source command](payer-audit.md).

A matching Base USDC transfer does not prove service delivery, wallet ownership or buyer intent. A seller's ownership flag and claimed example-input match remain attributed claims unless independently checked. `unknown` is not a confirmed organic customer; `probe-like` is an uncalibrated indication, not fraud or a probability. A known research payment does not permanently classify its wallet. Keep every payment in the monetary totals, including probes and monitoring. [Five-payer example](https://dev.to/alexar76/five-wallets-paid-an-x402-seller-what-can-we-say-about-demand-5gek).

## Polling can miss intermediate states

Remote HTTP checks have an independent queue: normally six hours, targeting 30 minutes after at least two observed changes in seven days. Registry discovery and package planning keep their own daily schedule. These are target intervals, not achieved-latency promises: downtime, backlog, rate limits and backoff lengthen gaps. Inspect `remotePolling` in `/api/v1/stats` and actual observation gaps. A→B→A entirely between two checks remains invisible; more frequent sampling reduces the opportunity but never records every transition. [Operational details](remote-polling.md).

## Canary and launcher boundaries

A successful `initialize`/`tools/list` or one synthetic canary invocation does not test every input or establish that a service is free. A canary can select a free preview while omitted arguments activate a paid server default. Server-authored `readOnlyHint` does not attest billing; an error or timeout does not prove no effect occurred. Remote reads and package canary runs are different measurements.

For launcher comparisons preserve both full tool snapshots, exact command, runtime/runner version and test conditions. A package version, the running runner and self-reported `serverInfo.version` may differ. Reading public historical snapshots is not a fresh execution or a human usability study; annotations and package-version claims need their own provenance.
