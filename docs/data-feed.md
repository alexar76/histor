# HISTOR as a data source — for registries, scanners and rating services

Registries list MCP servers, scanners vet them, rating services score them. All of them need
the same thing they mostly cannot afford to build: **what each server advertised over time,
and when it changed**. HISTOR records that for every server in the official MCP registry and
every new npm/PyPI version of MCP packages, signs each observation into a public Merkle log,
and serves it three ways.

## 1. Incremental API (free, public)

```bash
# every change, oldest first, from a cursor; resume with nextCursor
curl -s "https://histor.modelmarket.dev/api/v1/changes?after=0&limit=200"
curl -s "https://histor.modelmarket.dev/api/v1/changes?after=<nextCursor>&limit=200"
# only the servers you list (up to 100 ids per request)
curl -s "https://histor.modelmarket.dev/api/v1/changes?after=0&watch=<id>,<id>"
# one change, field by field, and what it costs in tokens
curl -s "https://histor.modelmarket.dev/api/v1/changes/<id>"
curl -s "https://histor.modelmarket.dev/api/v1/changes/<id>/cost"
```

`limit` is 1–200 (50 when omitted). `nextCursor` is the last id returned; on an empty page it
is the `after` you sent, so a caught-up reader keeps polling from the same place. A cursor
must be an id this feed gave you: one past the newest change stays empty until ids reach it.
`watch` here is strict (any malformed id, or more than 100, is a 400), while `/feed.xml`
drops the malformed ones and keeps the first 100, because a feed reader cannot show an error.

A change row carries the server, both tool-set digests, the summary (tools added, removed,
modified; new outside addresses), the server's own version on both sides and the registry's
version (since 2026-10-28), and the id of the signed label in the log. `/check` answers "is
what my client received what HISTOR observed?" for a single list.

## 2. Weekly snapshots (free, CC BY 4.0, with a DOI)

Every server, every tool set it showed, every change, the WARDEN scans, as JSON Lines with a
signed manifest: <https://histor.modelmarket.dev/datasets/>, mirrored on Hugging Face
(`alexar76/histor-mcp-tool-definitions`) and archived on Zenodo
(concept DOI [10.5281/zenodo.23512759](https://doi.org/10.5281/zenodo.23512759), which opens the
newest snapshot). Cite the snapshot you used by its own version DOI, listed on the datasets page;
every label in it can be proven against the live log.

## 3. Pushed changes (for partners)

A `changes` webhook ([watch.md](watch.md)) pushes each change of the servers on your list to
your endpoint, signed, in order, at least once — the way to keep a catalogue's "last changed"
column, a scanner's re-scan queue or a rating's stability score current without polling.
Set up by the operator on request.

## Terms

- **Data:** CC BY 4.0. Attribute "HISTOR (histor.modelmarket.dev)" and, for a snapshot, its DOI.
- **API:** public and unauthenticated; please send a `User-Agent` that names you and keep to
  one request per second for bulk reads. For more, or for pushed changes, ask.
- **What it is not:** HISTOR records what servers advertise; it never calls a server safe.
  Interpretations — ratings, verdicts — are yours, and so is the responsibility for them.

## Verify instead of trust

`pip install aimarket-histor && histor audit https://histor.modelmarket.dev` checks the log's
signed tree heads and consistency proofs from your side ([log.md](log.md)). The instance is
also a C2SP witness-capable log (`GET /checkpoint`).

Contact: issues at <https://github.com/alexar76/histor/issues>.
