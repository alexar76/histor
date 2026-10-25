# A tool-cost CI check on a real change

**Public, self-contained version:** [A copyable MCP tool-cost check for CI, with passing and failing examples](https://dev.to/alexar76/a-copyable-mcp-tool-cost-check-for-ci-with-passing-and-failing-examples-3hl5). It includes the complete fetch script and commands; no repository checkout or login is required.

HISTOR [change 28848](https://histor.modelmarket.dev/api/v1/changes/28848/cost) records a change to
textokit's remote MCP endpoint on 2026-10-24 at 19:52 UTC. The set still has 20 tools; 17 definitions changed.
This example uses those two stored lists. It executes no textokit code or paid tool calls.

We maintain HISTOR. This example was prepared with AI assistance from public observations. The observations
describe a remote definition change; they do not establish which npm release deployed it or imply an endorsement.

| Count, `o200k_base` | Before | After | Change |
|---|---:|---:|---:|
| Full definitions in the counted model-input form | 5,565 | 3,776 | −1,789 / −32.1% |
| Descriptions | 2,158 | 783 | −1,375 |
| Input schemas | 3,165 | 2,751 | −414 |
| Names | 120 | 120 | 0 |
| Keys and punctuation | 122 | 122 | 0 |

## Reproduce

Tested on a Linux server with Python 3.11.2 and tiktoken 0.14.0. Published HISTOR 0.3.1 contains the same
`toolcost.py` and `toolcost_cli.py` bytes used for verification. The commands below pin that published version.

```sh
python -m pip install 'aimarket-histor[cost]==0.3.1' 'tiktoken==0.14.0'
python fetch_textokit.py --out /tmp/textokit-cost
histor tool-cost snapshot --tools /tmp/textokit-cost/before-tools.json -o /tmp/textokit-cost/baseline.json
histor tool-cost check --baseline /tmp/textokit-cost/baseline.json --tools /tmp/textokit-cost/after-tools.json --max-increase 10%
```

The fetch script verifies each list's content digest. The check exits **0** because the total,
descriptions and schemas all became smaller. Each count is checked separately: shorter descriptions cannot
hide a large schema regression.

For a failure case, deliberately compare in the opposite direction:

```sh
histor tool-cost snapshot --tools /tmp/textokit-cost/after-tools.json -o /tmp/textokit-cost/reduced-baseline.json
histor tool-cost check --baseline /tmp/textokit-cost/reduced-baseline.json --tools /tmp/textokit-cost/before-tools.json --max-increase 10%
```

This exits **1**, flagging all three counts. It demonstrates a hypothetical return to the older, larger
definitions; it does not mean a later textokit release made that regression. Exit **2** means the input could
not be counted. Growth below the default 50-token noise floor is ignored.

## Use it for your own server

Create and commit a baseline once, then check the built server in your existing CI job:

```sh
histor tool-cost snapshot --stdio 'node dist/index.js' -o tool-cost.json
# In CI, after your project's build:
histor tool-cost check --baseline tool-cost.json --stdio 'node dist/index.js' --max-increase 10%
```

Use your own launch command or `--tools current-tools.json` for a captured `tools/list`. Review baseline
changes when accepting growth; do not regenerate the baseline automatically before every check. The Markdown
result also goes to the job summary when the runner supplies `GITHUB_STEP_SUMMARY`.

The count covers complete `{name, description, input_schema}` definitions. A client loading tools on demand
may send a subset. Prompt-cache writes and reads have different billing rules; provider wrappers add tokens.
Actual invoices require the client's usage records.

[Verification metadata](verification.json) records exit codes, the tokenizer and matching CLI module hashes.
