# Five tool-definition digests, one set of servers (31 Oct 2026)

Question: do the public records of MCP tool definitions agree with each other when they read the
same servers, and can one record answer in another's digest format?

Records and formats compared:

| | fields in the digest | canonical form | missing / empty |
|---|---|---|---|
| HISTOR MTL/1 tool-set digest | name, description, inputSchema, outputSchema (title, annotations, icons, `_meta` kept beside it, separately digested) | RFC 8785, array sorted by name | description → "", inputSchema → {}, outputSchema kept if an object |
| heldfast fingerprint (LOCK.md) | name, title, description, input_schema, annotations, output_schema, icons | RFC 8785, one tool | name/title/description → "", input_schema/annotations → {}, output_schema omitted when {} , icons omitted when [] |
| AgentAvow `agentavow.mcp-tool-definition.v1` | name, title, description, inputSchema, outputSchema, annotations | RFC 8785 of `{profile, tool}` | missing or null omitted; {} kept |
| rugsnare `hash` (PIN-SPEC §3) | name, description, inputSchema, icons; outputSchema and annotations compared separately | sorted keys, compact | icons null when absent |
| Vercel AI SDK `fingerprintTools` (ai@7) | description (tagged), inputSchema with `additionalProperties: false` forced by @ai-sdk/mcp, title (`title ?? annotations.title`); name is the map key | sorted keys, JSON.stringify, `undefined` written as a bare word | title absent → `undefined` |

Inputs are pinned in [inputs.json](inputs.json): HISTOR's signed weekly snapshot of 26 Oct
(tree head 1,074,932, 13:49:50Z) and heldfast's feed checkpoint of the same day (13:48:36Z).

Published at https://histor.modelmarket.dev/datasets/interop-digests-2026-10-31/ (this folder, with
`SHA256SUMS`); scripts also in the HISTOR repository under `research/interop-digests-2026-10-31/`.

## Results

**The digests can be computed from one another's data.** `scripts/peer_digests.py` implements the
heldfast, AgentAvow and Vercel rules from their published text. `scripts/check_vectors.py`:
heldfast golden tools 14/14, AgentAvow v1 served-definition digests 3/3, AgentAvow key encodings
13/13, and the AgentAvow control (one sentence appended) flips the digest. The Vercel
implementation agrees with `fingerprintTools` from `ai@7.0.159` on 6,882 of 6,882 tools sampled
from HISTOR (`scripts/vercel_fp.mjs`).

**An independent reading reproduces a signed attestation.** HISTOR read `https://mcp.deepwiki.com/mcp`
on 26 Oct. From that stored definition, the AgentAvow v1 digests of all three tools equal the ones
AgentAvow signed on 1 Oct (`sha256:171a53c9…`, `3582a810…`, `9bd57ff3…`). Earlier runs, including one
by a third party on 14 Oct, checked implementations against the pinned fixture; this is another
party reading the server itself.

**HISTOR vs heldfast, server by server** (`scripts/compare_heldfast.py`, `summarize.py`,
[results/results.json](results/results.json)):

- 23,190 servers in both records with tools (6,456 npm packages, 16,734 hosted endpoints).
- 21,215 of them with every field heldfast hashes known on HISTOR's side (tool extras are recorded
  since 22 Oct): 20,771 identical tool by tool (97.9%).
- 318,389 tools compared, 315,605 identical byte for byte under heldfast's rule (99.1%).

The 444 servers that differ, by the first reason that applies (`scripts/summarize.py`, after the
full field diff of all 2,784 differing tools in `scripts/fullfield.py` and `toolclass.py`):

| servers | reason |
|---:|---|
| 218 | the two records ran different npm versions |
| 65 | the same definitions, read at different moments (heldfast also recorded HISTOR's exact fingerprints) |
| 50 | heldfast's later reads failed (HTTP 400/401…), so its state is older |
| 33 | hosted server edited between the two reads |
| 24 | the definition echoes the launch environment: home directories (`/tmp/churn-…` vs `/home/histor`), placeholder credentials (`heldfast-churn-placeholder` vs `histor-envkey-…`), date and UUID defaults, random ids |
| 24 | same npm version: heldfast's read predates its switch to offline runs (`477131b`, 29 Sep 07:42Z), HISTOR runs offline |
| 15 | the server sent no outputSchema/title to heldfast's request |
| 10 | heldfast read the first `tools/list` page only |
| 5 | same npm version, offline on both sides: one negotiated 2026-07-28 against 2025-06-18; four list a different number of tools from run to run (HISTOR itself saw 17 and 10 for one of them on the same day) |

Checks behind the rows:

- **Edited (33).** Each server read live on 31 Oct (`scripts/live_compare.py`, `results/edited-live.json`):
  8 now serve heldfast's copy, 7 HISTOR's, 18 neither. In all 15 decisive cases today's definitions
  match the copy of the reader that came later.
- **Pagination.** HISTOR's own observations store the pages read (`results/pag131-obs.txt`). Of
  131 servers where heldfast's list is a strict subset of ours, HISTOR read more than one page on
  11; the other 120 differ by version or by tools added between reads. Live on 31 Oct, 10 of the
  11 serve a first page of exactly heldfast's count (ecomdly 15/55, framezone 50/62, ainumbers
  74/762…); 976 tools are missing at heldfast across the 11. `heldfast.probe` sends one
  `tools/list` with no cursor.
- **Protocol (15).** heldfast's catalogues record the negotiated version. Two mechanisms, both
  confirmed live (`scripts/probe_header.py`): servers that accept a 2024-11-05 `initialize` serve
  the old shape (mcp.apollographql.com: outputSchema on 0/48 tools, 48/48 under 2025-06-18), and
  servers that answer with a newer version but then receive `tools/list` without the
  `MCP-Protocol-Version` header assume 2025-03-26 (isitabizday.com 0/8 without the header, 8/8
  with it; cuevr.com 0/13 vs 3/13; cristiandesivo.com 0/6 vs 6/6). heldfast's `http_handshake`
  sends only the session header after a legacy-era `initialize`.
- **Network (24).** All 24 heldfast reads are dated before 2026-09-29T07:42:47Z, when heldfast
  moved npm measurement to offline containers (its watch.py: "each ran with the network on").
- 324 of the 2,784 differing tools have a fingerprint in heldfast's state but no stored definition
  in its `tools/` folder; they are classed by server only.

**HISTOR vs rugsnare** (`scripts/compare_rugsnare.py`): 18 package versions in both. 16 identical
in description and inputSchema for every tool. `mongodb-mcp-server@3.1.0` differs by launch
configuration (rugsnare's run had a preconfigured connection and read-only tools;
HISTOR's had none) and `@modelcontextprotocol/server-everything@2.0.1` lists four more tools at
rugsnare (client capabilities). 322 of 338 common tools identical.

**What each format would have missed** (HISTOR changes 24 Sep – 26 Oct, 188,947 modified tool
entries): 11,724 changed outputSchema only and 1,270 changed only the top-level
`additionalProperties` of the input schema; neither moves the Vercel fingerprint (6.9%).
Annotation-only changes (readOnlyHint and the other hints) move heldfast and AgentAvow digests but
not Vercel's or rugsnare's `hash` (rugsnare reports them as ANNOTATION separately).

## What this means for a shared digest

The rules already agree on RFC 8785 and on sorting; they differ in the field set and in how
"missing" and "empty" are written. Those are choices a profile can settle. What a profile cannot
settle by rule is capture: the same package version serves different definitions under a
different protocol version, page handling, network, environment variables, configuration or
client capabilities. Two digests are comparable only together with those conditions, so a shared
record needs a capture block beside the digest (negotiated protocol version, pages drained,
launch: network, env names supplied, args), the way HISTOR's `launch` and heldfast's catalogue
`protocol`/`args` already partly do.

## Reproduce

Put the inputs next to the scripts (paths are relative to the script directory):
`histor-2026-10-26/` (snapshot files from the URL in inputs.json), `heldfast/feed/state/` (sparse
checkout of the pinned feed commit), `heldfast/lookup-all.json.gz`, `heldfast/golden/` and
`agentavow/vectors.json` (pinned commits). Then, with `pip install rfc8785==0.1.4`:

    python check_vectors.py
    python compare_heldfast.py && python fullfield.py && python toolclass.py && python summarize.py
    python live_compare.py       # reads servers live; results change with time
    python compare_rugsnare.py

## Follow-up in the code

- `histor/histor/fingerprints.py` and `GET /api/v1/servers/<id>/fingerprints?format=vercel-ai|heldfast|agentavow-v1`
  serve these formats from HISTOR's stored definitions; `histor/tests/fingerprint-vectors.json` holds
  values produced by the reference implementations (not deployed as of 31 Oct).
- [capture-block.md](capture-block.md) (also `awr/adoption/mcp-trust-label/capture-block.md`): a proposed capture record beside any tool digest
  (negotiated protocol version and header, pages read, client capabilities, launch conditions).

## Licence and attribution

Results in `results/` are CC BY 4.0, Aleksandr Artamokhov (HISTOR). They are derived from two
CC BY 4.0 sources: HISTOR's snapshot of 26 Oct 2026 (histor.modelmarket.dev, also tagged
`snapshot-2026-10-26` on Hugging Face) and heldfast's feed at commit `273682b` (rufat325/heldfast-feed,
measurements and digests by heldfast). Tool definitions quoted in the diffs are the servers' own
text and remain their authors'. AgentAvow's vector file (Apache-2.0) is referenced by commit and not
redistributed. Scripts are MIT, like HISTOR.
