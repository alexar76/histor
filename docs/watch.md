# HISTOR Watch — know when an MCP server you use changes what it tells your model

You approved an MCP server once. Since then its tool descriptions, schemas or tool list may
have changed — in a four-week window, 45% of remote servers in the official registry did, and
most of those changes are description text, the words your model reads. HISTOR reads every
listed server on a schedule and records each change. Watch is how you get told.

## Free: a feed of the servers you follow

Find each server's id on its page (`https://histor.modelmarket.dev/s/<id>`), in the answer of
`/check`, or with `GET /api/v1/servers?q=<name>`. Then, with up to 100 ids:

```text
https://histor.modelmarket.dev/feed.xml?watch=<id>,<id>,<id>
```

An Atom feed of the last 50 changes of those servers: any feed reader, Slack's or Teams' RSS
app, or a CI job. Each entry names what was added, removed or modified, any new outside
address the definitions mention, and links to the server's page with the field-by-field diff.

For a program, the same list with a cursor, so it reads each change once and in order:

```bash
# first run: everything since the start, oldest first
curl -s "https://histor.modelmarket.dev/api/v1/changes?watch=<id>,<id>&after=0&limit=200"
# then: only what came after the last change you saw
curl -s "https://histor.modelmarket.dev/api/v1/changes?watch=<id>,<id>&after=<nextCursor>&limit=200"
```

`limit` is 1–200 (50 when omitted), and an empty page returns the cursor you sent, so keep it.

Nothing is stored on HISTOR's side: the list is in your URL.

## For teams: changes pushed to your systems

A `changes` webhook delivers each change of the servers on your list to your HTTPS endpoint as
it is recorded — Slack (incoming webhook), a ticketing system, or your own service:

- one `POST` per change, in order, at least once, with a stable `X-Histor-Event-ID` to
  deduplicate;
- signed: `X-Histor-Signature: sha256=` HMAC-SHA256 of `X-Histor-Timestamp + "." + body`
  with your secret (32+ characters);
- the body is `histor.change/v1`: server id and name, endpoint, time, tools added, removed and
  modified, new outside addresses, the signed label's id, and links to the diff and the page;
- retried with backoff up to a day when your endpoint is down; a destination that resolves
  to a non-public address is never called.

Destinations are configured by the operator, never through a public API, so nobody can point
HISTOR at someone else's endpoint. To set one up for your team, open an issue at
<https://github.com/alexar76/histor/issues> with the server list (or ask on
<https://histor.modelmarket.dev/about>), and say where the secret should be sent.

Operators running their own HISTOR: add an entry to the subscriptions file and schedule
`histor webhooks /etc/histor/subscriptions.json`:

```json
[{"id": "team-a", "url": "https://hooks.example.com/histor", "secret": "<32+ characters>",
  "source": "changes", "watch": ["<id>", "<id>"]}]
```

`source` defaults to `security-events` (the signed package-evidence feed, every target).

## What it does not tell you

A change is a fact about what a server advertised, not a verdict. HISTOR never calls a server
safe or unsafe; a description that changed may be a typo fix or a new instruction to your
model, and the diff is there so a person can tell which. Servers behind a login, and what a
server does when its tools are called, are outside what HISTOR reads
([measurement limits](measurement-limits.md)).
