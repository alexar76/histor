# HISTOR as an MCP subregistry

The MCP documentation ([Registry Aggregators](https://modelcontextprotocol.io/registry/registry-aggregators), "Acting as
a Subregistry") describes a subregistry: an aggregator that implements the registry's OpenAPI and adds its own data
under a namespaced `_meta` key. It also says host applications should read aggregators or private registries, not the
official registry directly.

HISTOR can be one. It serves the registry's entries unchanged and adds what it observed of each server: the digest of
the tool definitions it last read, since when they have not changed, and how many times they did.

Status: in the source since 2026-11-19, off by default. Routes and fields: [api.md](api.md#subregistry-the-mcp-registrys-v01-api).

## What a host gets

```bash
curl -s 'https://<instance>/v0.1/servers/io.github.acme%2Fbilling/versions/latest'
```

```json
{
  "server": { "name": "io.github.acme/billing", "version": "1.4.0", "remotes": [{ "type": "streamable-http", "url": "https://billing.acme.example/mcp" }] },
  "_meta": {
    "io.modelcontextprotocol.registry/official": { "status": "active", "isLatest": true, "publishedAt": "…", "updatedAt": "…" },
    "dev.modelmarket.histor/observed": {
      "type": "histor.observed/v1",
      "log": "https://<instance>",
      "targets": [{
        "id": "e1eb27d49e525f9f", "endpoint": "https://billing.acme.example/mcp", "page": "https://<instance>/s/e1eb27d49e525f9f",
        "notAttempted": null, "lastStatus": "ok", "lastObserved": "2026-11-19T04:12:08Z",
        "toolCount": 7, "toolSetDigest": "sha256-…", "toolExtrasDigest": "sha256-…",
        "firstPinned": "2026-10-02T03:55:41Z", "unchangedSince": "2026-11-03T04:01:12Z", "changes": 2
      }]
    }
  }
}
```

The `server` object is what the upstream sent. The observation is of an unauthenticated read from HISTOR's address: a
server behind sign-in has no digest, and a server can answer another client differently.

## A registry for one organisation

A host that restricts MCP servers to a registry (GitHub Copilot's organisation or enterprise registry is one: it asks
for these three routes and the CORS headers HISTOR sends) needs a registry that lists only the approved servers. An
instance with an allow list is that:

```text
# allow.txt: one server name or namespace/* per line
io.github.acme/billing
io.github.acme-tools/*
com.stripe/mcp
```

```bash
docker run -d --name mcp-registry -p 9490:9490 -v mcp-registry:/data -v "$PWD/allow.txt:/etc/histor/allow.txt:ro" \
  -e HISTOR_DATA_DIR=/data -e HISTOR_PUBLIC_BASE=https://mcp-registry.acme.example \
  -e HISTOR_SUBREGISTRY=1 -e HISTOR_SUBREGISTRY_ALLOW=/etc/histor/allow.txt \
  -e HISTOR_SUBREGISTRY_UPSTREAM=https://registry.modelcontextprotocol.io \
  -e HISTOR_CRAWL_INTERVAL_S=0 -e HISTOR_REMOTE_INTERVAL_S=0 -e HISTOR_CRAWL_ON_START=0 \
  <histor image>
```

It reads the listed servers from the upstream every hour and serves them at `https://mcp-registry.acme.example`: the
address to give the host (without `/v0.1/servers`). A server removed from the file leaves the copy at the next read; a
server the upstream marks deleted stops being listed. With the crawl off the instance observes nothing itself. Point
`HISTOR_SUBREGISTRY_UPSTREAM` at a HISTOR subregistry that does observe and its `dev.modelmarket.histor/observed`
blocks are passed on.

Not checked: this container command has not been run against a host application yet; the routes were checked with
HTTP clients and against the official registry's answers (see the changelog entry's evidence).

## Settings

| Variable | Default | |
|---|---|---|
| `HISTOR_SUBREGISTRY` | `0` | `1` adds the routes and the sync |
| `HISTOR_SUBREGISTRY_UPSTREAM` | `HISTOR_REGISTRY_URL` | the registry or subregistry to copy; https in production |
| `HISTOR_SUBREGISTRY_ALLOW` | none | a file of server names and `namespace/*` lines |
| `HISTOR_SUBREGISTRY_SYNC_S` | `3600` | seconds between reads; `0` = only `python -m histor.subregistry sync` |
| `HISTOR_SUBREGISTRY_FULL_S` | `604800` | seconds between walks of every server (without an allow list) |

`python -m histor.subregistry sync [--full]` reads the upstream once; `python -m histor.subregistry status` and
`GET /api/v1/subregistry` say what the copy holds.

## How the copy is kept

As the registry's documentation asks of an aggregator: entries are copied to HISTOR's own store, never fetched per
request. The first read walks `version=latest`; later reads ask `updated_since` from a checkpoint that moves only after
every page was applied, with a ten-minute overlap, and write by name and version, so a page applied twice changes
nothing. Once a week every server is walked again; a server that walk no longer returns is marked deleted, unless the
walk returned less than half of what the copy holds, which is an upstream outage and marks nothing.

Limits: a version superseded before the first sync is not in the copy; a status change of a version that is no longer
the latest is not picked up; an entry larger than 64 KB, or one that is not valid JSON (NaN), is skipped and counted.
