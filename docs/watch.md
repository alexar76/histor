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

## For teams: changes pushed to your systems — $29 for 30 days

HISTOR Watch for teams sends each change of the servers on your list to your HTTPS endpoint as
it is recorded: a Slack incoming webhook, a ticketing system, or your own service.

| | |
|---|---|
| Price | **$29 for 30 days**, by card through Stripe Checkout. No automatic renewal: pay again to extend, from the current end. |
| Servers | 1 to 100 per subscription; change the list at any time. |
| Destination | One HTTPS endpoint, chosen at checkout. It must resolve to public addresses only, and is checked again at every send. |
| Account | None. A manage link is the key to the subscription; HISTOR keeps only its hash, and no e-mail. |
| Refund | Ask, and the period that payment bought ends; a card dispute ends the subscription. |

**Status on histor.modelmarket.dev: demo.** The plan and the price are real, and the whole path
— checkout, Stripe webhook, delivery, refund — is built and tested against the Stripe sandbox
(below), but card payment is switched off on the public instance for now
(`HISTOR_WATCH_DEMO=1`). To start a subscription before it opens, open an issue at
<https://github.com/alexar76/histor/issues> with the server list; the operator sets it up by hand
(see *Operators* below).

### Buying and managing

1. On <https://histor.modelmarket.dev/watch>, paste the server ids (from each server's page or
   `GET /api/v1/servers?q=<name>`) and your endpoint, and pay by card on Stripe's page.
2. Back on `/watch`, save the manage link it shows (`/watch#<id>.<token>`). The token is in the
   part of the URL after `#`, which browsers never send to a server; this browser keeps it too.
3. The manage page shows the period, the signing secret, delivery state (failed attempts and
   the next retry), the server list to edit, a **Send a test event** button, and the payments.

Changes recorded before the payment are not sent: a new period, and one that resumes after a
gap, starts at the newest change, so a backlog never floods your channel.

### What arrives

- one `POST` per change, in order, at least once, with a stable `X-Histor-Event-ID` to
  deduplicate;
- signed: `X-Histor-Signature: sha256=` HMAC-SHA256 of `X-Histor-Timestamp + "." + body`
  with your secret;
- the body is `histor.change/v1`: server id and name, endpoint, time, tools added, removed and
  modified, new outside addresses, the signed label's id, links to the diff and the page, and a
  one-line `text`, the field a Slack incoming webhook posts (names in it are escaped, so a
  server cannot ping your channel or plant a link);
- `histor.test/v1` when you press **Send a test event**;
- retried with backoff up to a day when your endpoint is down; a destination that resolves
  to a non-public address is never called.

Not offered: e-mail. HISTOR keeps no addresses. If you want mail, point a feed-to-mail service
or your own mail relay at the free Atom feed, or at the webhook.

### API

The page uses these; a script can too. `Authorization: Bearer <token>` on every call but the first.

```text
GET  /api/v1/watch/plan                          price, days, limits, and whether card payment is open
POST /api/v1/watch/subscriptions                 {"watch": [ids], "url": "https://…"} → id, manageToken, checkoutUrl
GET  /api/v1/watch/subscriptions/<id>            state, activeUntil, endpoint, secret, servers, delivery, payments
PUT  /api/v1/watch/subscriptions/<id>/servers    {"watch": [ids]}
POST /api/v1/watch/subscriptions/<id>/renew      → checkoutUrl (an open one is returned instead of a second)
POST /api/v1/watch/subscriptions/<id>/test       → endpointStatus
```

A wrong token and an unknown id answer the same 404. Nothing under `/api/v1/watch/` is readable
from another origin.

### How a payment becomes a period

Every amount HISTOR checks comes from the payment row it wrote before asking Stripe, never from
the event: the Stripe webhook (`checkout.session.completed`), the team's return to `/watch`, and
a minute-by-minute sweep all read the session back and settle it once. The amount must be the
price in USD cents, the session must be the one opened for that payment, and a live instance
accepts only live-mode payments. A refund (`charge.refunded`, in full) takes back the 30 days
that payment bought; a dispute (`charge.dispute.created`) ends the subscription. A Stripe test
key is refused on a production instance and a live key everywhere else.

**Tested 2026-11-01** against the Stripe sandbox, on Postgres, with
`artifacts/histor-watch-paid-2026-11-01/sandbox_e2e.py`: a subscription opened through the
API, paid with test card 4242 through the checkout link on `/watch`; the
`checkout.session.completed` webhook (through `stripe listen`) made it active for 30 days; Stripe
returned the browser to `/watch`, which showed it active; a recorded change went out over HTTPS
to a public endpoint and the test event got 200; a refund through Stripe came back as
`charge.refunded` and ended the period, after which nothing was sent.

## Operators

Running your own HISTOR: set `HISTOR_STRIPE_SECRET_KEY` (a restricted key with *Checkout
Sessions: write* is enough) and `HISTOR_STRIPE_WEBHOOK_SECRET`, and point a Stripe webhook at
`https://<your host>/api/v1/watch/stripe` with `checkout.session.completed`,
`checkout.session.expired`, `charge.refunded` and `charge.dispute.created`.
`HISTOR_WATCH_PRICE_CENTS` (2900) and `HISTOR_WATCH_DAYS` (30) set the plan, and
`HISTOR_WATCH_DEMO=1` shows it with payment closed. Paid subscriptions are delivered by the
service itself, every 20 seconds.

Subscriptions an operator sets by hand (no payment) go in a file, delivered by
`histor webhooks /etc/histor/subscriptions.json` on a timer:

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
