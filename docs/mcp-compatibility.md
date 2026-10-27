# MCP protocol compatibility

The local readers implement modern MCP revision `2026-07-28` alongside the existing legacy
initialization path. Unknown modern revisions are not assumed compatible. This change has not
been deployed by the local implementation task.

## Remote observations

`histor.mcpclient.McpReader` starts with `server/discover`, then drains `tools/list`. Modern
requests carry `MCP-Protocol-Version`, `Mcp-Method` and matching per-request `_meta` containing
the version and client capabilities. No modern `initialize` or initialized notification is sent.

A recognizable legacy rejection of discovery selects the existing `initialize` → initialized
notification → `tools/list` path. Authentication failures, rate limiting, redirects, recognized
modern errors and malformed modern results do not silently select a different protocol. Version
negotiation has a fixed attempt limit and uses only implemented revisions. The reader does not
switch eras halfway through a list.

The same checked address and Host/SNI binding, overall observation deadline, decompression limits,
tool/page caps and redirect refusal apply to discovery and fallback. The HTTP reader never calls
a tool. `input_required` is incomplete evidence; HISTOR does not answer elicitation or sampling
requests. Cache hints do not replace a fresh observation or extend its timestamp.

## Package observations and local CLI

The sandbox observer and `histor tool-cost --stdio` probe `server/discover` first. A bounded
silent probe or valid legacy error permits initialization. A recognized modern error keeps the
client in the modern era; required metadata is included on subsequent requests. Package canary
calls remain inside the existing isolated sandbox. Discovery adds no host package execution or
external network route.

Sandbox observer 5 records the new lifecycle. Current defaults accept previous observers 3 and 4
for already complete successful captures; their failures can be retried by observer 5. Deploy
the same observer code to sandbox hosts before switching their runtime configuration. An
explicit operator pin is preserved. Nothing rewrites historical observations.

## HISTOR as a local MCP server

`histor mcp` accepts modern requests independently, including `server/discover`, and includes
`resultType`, server identity metadata and list cache hints. It still accepts legacy clients
after their explicit initialization. Missing required modern metadata and unsupported versions
return errors; modern discovery does not silently authorize legacy calls without initialization.

The HTTP history API exposes `timeline[].protocolVersion` and does not collapse identical tool
lists across different observed protocols. The package dataset adds `protocolVersion` per
observation. Unknown historical versions remain null. These fields are provenance, not a new
MTL/1 digest member.

## Sources and verification

- [Versioning and compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)
- [Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
- [Protocol schema](https://modelcontextprotocol.io/specification/2026-07-28/schema)

Local regression suites cover modern, legacy and dual-era fixtures, malformed replies, no
downgrade on modern errors, pagination, incomplete results, shared deadlines and address pinning.
The modern server is also exercised over real local process pipes. These checks do not establish
compatibility with every public server or an external conformance certification.
