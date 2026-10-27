# Package capture conditions

A package version alone does not identify the tool list a server will advertise. Start arguments,
environment, installed dependencies and network reachability can change that list. HISTOR's
package sandbox has no route to the internet. Its observations describe those conditions.

## Recorded provenance

Each observation keeps the version the sandbox reports, independently of `serverInfo.version`,
plus its observer, image, runtime, entry and supplied configuration. The API timeline and
`package_observations.jsonl.gz` expose this record. Earlier rows stay unknown; a version requested
by the crawler is not substituted when the sandbox does not report which version it resolved.

## Reviewed launch defaults

These defaults apply only to the exact npm identities below. They are implemented in
`histor/launch_profiles.py`; explicit registry arguments and previously discovered arguments take
precedence. Variable names are merged with registry requirements. No actual account data is used.

| Package | Arguments | Placeholder variable names |
|---|---|---|
| `@modelcontextprotocol/server-filesystem` | `/work/app` | none |
| `@azure-devops/mcp` | `histor-sandbox --authentication envvar` | `ADO_MCP_AUTH_TOKEN` |
| `@launchdarkly/mcp-server` | `start --transport=stdio --api-key histor-placeholder-token` | none |
| `figma-developer-mcp` | `--stdio` | `FIGMA_API_KEY` |

`/work/app` is the existing working directory inside the disposable container, with its synthetic
fixture data. Arbitrary absolute paths and traversal are refused. The standalone observer replaces
the exact argument `histor-placeholder-token` with its normal `histor-envkey-<run>` placeholder,
using an argv list, without a shell. The published supplied arguments retain the symbolic token;
it is not a literal credential or the value sent to a provider.

A previously failed observation can run again when the profile adds configuration, even if the
package and observer versions did not change. A successful, complete observation is retained;
profiles do not rewrite earlier captures or repeatedly run a completed version.

## Verification and rollout

Version 0.4.2 includes the reviewed launch defaults and observed-version correction. Tests use
synthetic package responses and a stubbed container boundary; they do not establish that the four
current upstream packages now answer in production. Version changes can alter their arguments.
For rollout, update every standalone sandbox observer before the HISTOR service: an older observer
refuses the exact absolute directory argument and does not expand the new symbolic token. PyPI
updates alone do not replace the standalone observer script on its hosts.

On a production host enrolled in the native WARDEN quality cycle, the deployment script retains
the accepted live scanner and its complete Node dependency tree. The signed exact-build gate
still runs before replacement; the new HISTOR image is then rebound to that accepted build.
Rollback saves the actual running image, even if an earlier rejected build changed `:latest`.

Supabase and other servers that need an authenticated network session can remain unobserved.
No profile opens internet access, supplies a real key or turns a publisher's declaration into a
runtime capture. Missing coverage remains missing. Resolved dependency inventories are not added
by this patch; reproducing a capture can still require the installed dependency tree.

The launch forms were checked against the upstream sources on 27 October 2026:

- [Filesystem directory requirements](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
- [Azure DevOps auth modes](https://github.com/microsoft/azure-devops-mcp/blob/main/src/auth.ts)
- [LaunchDarkly start command](https://github.com/launchdarkly/mcp-server/blob/main/src/mcp-server/cli/start/command.ts)
- [Figma stdio setup](https://github.com/GLips/Figma-Context-MCP#readme)
