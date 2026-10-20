# Glama build — admin form

Listing: [glama.ai/mcp/servers/alexar76/histor](https://glama.ai/mcp/servers/alexar76/histor)

Admin: […/admin/dockerfile](https://glama.ai/mcp/servers/alexar76/histor/admin/dockerfile)

Glama generates the image from this form and does not build the repo [`Dockerfile`](../Dockerfile). That file is the production image: `node:22-bookworm-slim`, Debian Bookworm's Python 3.11, and `python -m histor serve`.

The form only offers `debian:bookworm-slim` and `debian:trixie-slim`. Use bookworm, Node 22 (the scanner sidecar) and Python 3.11 (the interpreter that image actually runs).

| Field | Value |
| --- | --- |
| Base image | `debian:bookworm-slim` |
| Node.js version | `22` |
| Python version | `3.11` |
| Build steps | `["npm ci --prefix scanner --omit=dev --no-audit --no-fund", "uv sync --frozen --no-dev --extra postgres --extra pqc"]` |
| CMD arguments | `[".venv/bin/python", "-m", "histor", "mcp"]` |
| Environment variables | `{"type":"object","properties":{},"required":[]}` |
| Pinned commit SHA | empty |

No secret is required for a dev start. `HISTOR_PROFILE=prod` refuses to start until Postgres and the operator settings exist; leave those unset on this form.

The `mcp` command serves `histor_changes`, `histor_check` and `histor_server` over
stdin/stdout using the local log and the existing signed federation handlers. It opens
no HTTP listener and starts no crawler. Diagnostics go to stderr; stdout contains only
MCP JSON-RPC. Ordinary `serve` remains the HTTP service on port 9490.

A fresh local log has no observations. Populate it separately with `histor crawl`, or
point `HISTOR_DATA_DIR` at the intended existing log (and preserve its issuer keys).
The `histor_check` tool may update digest contribution counts when explicitly requested
with `contribute: true`, just like the HTTP API. No remote service is substituted for
this local server. EOF closes the MCP session and the database.

After uploading the source, sync the Glama repository and replace the saved CMD with
the value above before clicking Build: the previous `serve` command is still HTTP.
