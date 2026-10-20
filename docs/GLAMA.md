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
| CMD arguments | `[".venv/bin/python", "-m", "histor", "serve"]` |
| Environment variables | `{"type":"object","properties":{},"required":[]}` |
| Pinned commit SHA | empty |

No secret is required for a dev start. `HISTOR_PROFILE=prod` refuses to start until Postgres and the operator settings exist; leave those unset on this form.

HISTOR's process is the HTTP service on port 9490. It does not speak MCP on stdio, so Glama's `mcp-proxy` probe is not a substitute for `GET /health`.
