# HISTOR Transparency Log — format specification

**Version:** `histor-tlog/2` · **Status:** Draft, implemented; seeking a second implementation and outside witnesses · `histor-tlog/1` is this document without §4.4 and §8.1, and every `/1` artefact stays valid under `/2` · **License:** this text and the vectors in [`test-vectors/`](../../test-vectors/) are Apache-2.0 (the HISTOR repository license)

> This is the normative description of the log that runs at `https://histor.modelmarket.dev`. The guide for auditors is [`docs/log.md`](../log.md); the API reference is [`docs/api.md`](../api.md). Where this document and the running code disagree, one of them is a bug: file an issue. Where this document and the vectors disagree, the vectors win.
>
> Non-normative summaries: [Русский](README.ru.md) · [Español](README.es.md) · [Français](README.fr.md) · [中文](README.zh.md).

The key words MUST, MUST NOT, SHOULD and MAY are to be interpreted as in RFC 2119 and RFC 8174.

## 1. What the log is

An append-only Merkle tree whose leaves are **signed statements about the tool definitions an MCP server advertised at a stated time**, with a signed tree head after every batch of appends, and inclusion and consistency proofs exactly as RFC 9162 (Certificate Transparency version 2) defines them.

The log records **observations**: what a server served the log's own reader. It does not record publisher signatures, and nothing in this document establishes who operates a server or whether its tools are safe. What it makes possible is narrower and checkable: a reader can prove that a statement was in the log at a given tree size, that the log only grew between two heads it saw, and that the tool set it was served is, or is not, one the log recorded for that server.

Three documents are defined, and each carries its `type` inside the signed bytes so one can never be passed off as another:

| Document | `type` | Signed by | Section |
|---|---|---|---|
| Label (a leaf) | a W3C Verifiable Credential, profile `MTL/1` | the log's `did:key` (Data Integrity proof) | §3 |
| Signed tree head | `histor.sth/v1` | the log's `did:key` | §4 |
| Check answer | `histor.check/v1` | the log's `did:key` | §7 |

## 2. Identity, canonicalisation and signatures

### 2.1 The log's key

A log has exactly one Ed25519 key, identified by a `did:key` (multicodec `0xed01` followed by the 32 raw public-key bytes, base58btc, `z` prefix; `did:key:z6Mk…`). Every label, tree head and check answer the log issues MUST be signed by this key. A log that changes its key is a new log; a verifier that pinned the old key MUST treat heads signed by another key as a failure, never as a rotation.

The key is published at `GET /api/v1/issuer` as `did` and `publicKeyHex`. A verifier SHOULD pin it on first contact and MUST verify every later document against the pinned key, not against the key a document names.

### 2.2 Canonical bytes

Wherever this document says "the canonical bytes of" a JSON value, it means the JSON Canonicalization Scheme, **RFC 8785**, applied to that value, encoded as UTF-8. There is no other serialisation anywhere in this format.

A document this format signs MUST NOT contain a JSON number other than an integer within ±(2^53−1) (AWR/2 SPEC §4.3). A tool set is not signed: its digest follows MTL/1.1 §5.4, which reads every number as an IEEE 754 double and reports `MTL-NUM-001` only for a number that has none. Until 2026-10-31 (MTL/1.0) a tool set with any non-integer number got no digest.

### 2.3 Signature rule for `histor.*` documents

For a tree head (§4) and a check answer (§7), the signature is Ed25519 (RFC 8032) over the canonical bytes of the document **with the `signature` member removed and every other member present, `type` included**. The signature object is

```json
{"alg": "Ed25519", "verificationMethod": "<did>#<multibase part of did>", "value": "<base64url, no padding>"}
```

A verifier MUST:

1. check that `type` equals the type it expects for the endpoint it called, before anything else;
2. reject the document if `verificationMethod` does not name the pinned key;
3. reject the signature before evaluating the group equation when the public key or the point R encodes a point of small order, or when the scalar S is not strictly less than the group order L (the permissive rule many Ed25519 libraries implement accepts a forgeable signature under the identity key; the strict rule and the forbidden encodings are listed in AIMarket Protocol spec §7.3.7 and implemented in `histor.signing.strict_ed25519`).

Labels (§3) are signed differently: as W3C Verifiable Credentials 2.0 with a Data Integrity proof, cryptosuite `eddsa-jcs-2022`, which also canonicalises with RFC 8785. Verification of a label is AWR/2 §13; this document only fixes how a label becomes a leaf.

## 3. Leaves

### 3.1 What a leaf is

A leaf is one **label**: an AWR/2 `VerificationVerdict` under the MCP Trust Label profile MTL/1, issued by the log's key, whose subject is an MCP Server Descriptor (server identity + tool-set digest) and whose `method` is one of the four MTL/1 methods (`tool-set-observation`, `tool-set-continuity`, `pattern-match`, `name-threat-match`). The label document is served, immutable, at `GET /api/v1/labels/<id>` with media type `application/vc`.

The log MUST append every label it issues, in issue order, and MUST NOT append anything that is not a label it issued. Leaf index 0 is the first label ever issued.

### 3.2 Leaf input and leaf hash

The **leaf input** is the canonical bytes (§2.2) of the complete label document as served, proof included. The **leaf hash** is

```
leaf_hash = SHA-256( 0x00 || leaf_input )
```

as RFC 9162 §2.1.1 prescribes for leaves. Because the input is the served document itself, anyone holding a label can recompute its leaf hash without asking the log.

### 3.3 The label digest

Each label also has a **digest**, `canonical_sri(label)`: the Subresource-Integrity encoding (`sha256-<base64>`) of SHA-256 over the same canonical bytes. The log indexes labels by it (`GET /api/v1/log/entries` returns it next to `leaf_index`). The leaf hash and the digest are two encodings of a hash over the same bytes, differing only in the `0x00` domain-separation prefix the tree uses; a verifier MUST NOT confuse the two.

## 4. The tree and its signed head

### 4.1 Tree

Interior node hash:

```
node_hash = SHA-256( 0x01 || left || right )
```

The Merkle tree hash of `n` leaves, MTH(D[n]), is defined in RFC 9162 §2.1.1 (split at the largest power of two strictly less than `n`). The hash of the empty tree is SHA-256 of the empty string.

### 4.2 Signed tree head

After appending, the log signs a **tree head**:

```json
{
  "type": "histor.sth/v1",
  "log": "did:key:z6Mk…",
  "treeSize": 1281619,
  "rootHash": "eb31e45e0ffa9ef7cac86b1b83eee3f76e98fac6c273f0e81cb67fe457e47495",
  "timestamp": "2026-10-30T05:16:17Z",
  "signature": { "alg": "Ed25519", "verificationMethod": "did:key:z6Mk…#z6Mk…", "value": "…" }
}
```

| Member | Rule |
|---|---|
| `type` | MUST be `histor.sth/v1` |
| `log` | the log's `did:key`; MUST equal the pinned key |
| `treeSize` | integer ≥ 1; the number of leaves the head covers |
| `rootHash` | lowercase hex of MTH(D[treeSize]) |
| `timestamp` | RFC 3339 UTC with `Z`, second precision; the log's assertion of when it signed, not evidence of when anything happened |
| `signature` | §2.3 |

The log MUST sign at most one head per tree size and MUST serve, for every size it ever signed, that same head (`GET /api/v1/log/sth/<size>`). The latest head is at `GET /api/v1/log/sth`. Two heads for one size with different roots are proof of misbehaviour, and a verifier that holds both SHOULD publish them.

### 4.3 The head as a C2SP checkpoint (`histor-tlog/2`)

The same head is also served as a **checkpoint** in the formats of [c2sp.org/tlog-checkpoint](https://c2sp.org/tlog-checkpoint) and [c2sp.org/signed-note](https://c2sp.org/signed-note), at `GET /checkpoint` (latest) and `GET /api/v1/log/checkpoint/<size>`, media type `text/plain; charset=utf-8`:

```
<origin>
<tree size>
<base64 root hash>

— <origin> <base64(key id || Ed25519 signature)>
[— <witness name> <base64(key id || 8-byte timestamp || Ed25519 signature)> …]
```

- **Origin**: a schema-less URL that never changes once published; the public instance's is `histor.modelmarket.dev/log`. It is also the log signature's key name.
- **Root hash**: the same bytes as `rootHash` in `histor.sth/v1`, base64 (RFC 4648, padded).
- **Log signature**: Ed25519 over the note text (the three lines, each newline-terminated) with key type `0x01`; key id = `SHA-256(origin || 0x0A || 0x01 || public key)[:4]`. The verifier key is published as a `vkey` string (`<origin>+<hex key id>+<base64(0x01 || key)>`) at `GET /api/v1/issuer` under `checkpoint.vkey`.
- **Cosignatures**: lines a witness returned ([c2sp.org/tlog-cosignature](https://c2sp.org/tlog-cosignature), `cosignature/v1`, key type `0x04`, message `cosignature/v1\ntime <seconds>\n` followed by the note text). The log stores them per size (`POST /api/v1/log/cosignatures`, operator-only) and appends them to the checkpoint it serves. A reader trusts a cosignature only under a witness key it configured itself; the log vouches for none of them.

The checkpoint and `histor.sth/v1` are two encodings of one head: same size, same root, same key. Nothing about the tree, the leaves or the proofs changes between `/1` and `/2`.

### 4.4 Proofs

`GET /api/v1/log/proof/inclusion?leaf_index=&tree_size=` returns the audit path of RFC 9162 §2.1.3.1 for that leaf against the head at `tree_size`, as an array of lowercase hex hashes, leaf-to-root order. `GET /api/v1/labels/<id>/proof` returns the same for a label by id, together with its `leafIndex` and the head it is proven against.

`GET /api/v1/log/proof/consistency?first=&second=` returns the consistency proof of RFC 9162 §2.1.4.1 between the heads at `first` and `second` (`0 < first ≤ second`), as an array of lowercase hex hashes. For `first == second` the proof is empty.

Verification is RFC 9162 §2.1.3.2 and §2.1.4.2 without change. A verifier MUST verify an inclusion proof against a head whose signature it verified under the pinned key, never against a root the proof response itself carries.

## 5. What a verifier checks

A **monitor** (a reader that wants to know the log only grew) keeps the last head it verified and, on each visit:

1. fetches the latest head and verifies it (§2.3, §4.2);
2. fetches the consistency proof from its stored size to the new size and verifies it against both roots;
3. refuses, and records the two heads, if the new size is smaller, the root at a size it already holds differs, or the key differs;
4. stores the new head.

An **auditor of one label** additionally fetches the label, recomputes its leaf hash (§3.2), fetches the inclusion proof against a verified head, and verifies it. The reference monitor is `python -m histor audit <log url> --state <file>` (exit status 0 = every check passed); the browser desk at `/log` does the same with WebCrypto.

Neither check proves that the log shows everyone the same tree. That property needs readers who compare heads with each other (gossip) or a second party that co-signs heads (a witness). §8 says where this format stands on that.

## 6. Entries endpoint

`GET /api/v1/log/entries?start=&end=` returns leaves `[start, end)` in index order, at most 1000 per call, each as `{leaf_index, id, digest, method, verdict, target_id, issued_at}`. It is a convenience for monitors that index the log; the leaf bytes themselves come from `GET /api/v1/labels/<id>`.

## 7. The check answer

`POST /api/v1/check` takes the tool definitions a client received from a server (plus the endpoint or package it got them from) and answers whether that tool set is one the log recorded for that server. The answer is a signed document of `type` `histor.check/v1` under the rule of §2.3. It is **a statement that the log holds this verdict for this tool set at this time**, attributable to the log's key; it is not a proof of anything about the server, and a client MUST treat it as a lead, not a verdict. The members of the answer are documented in [`docs/api.md`](../api.md#check); their names are not frozen by this version of the format, only the type, the signature rule and the meaning above.

## 8. Witnessing (`histor-tlog/2`)

### 8.1 HISTOR as a witness

An instance is also a witness in the sense of [c2sp.org/tlog-witness](https://c2sp.org/tlog-witness), for the logs its operator lists (origin + log `vkey`, in the file named by `HISTOR_WITNESS_LOGS`). Submission prefix `/witness/v1`:

- `POST /witness/v1/add-checkpoint` with body `old <size>\n`, up to 63 base64 consistency-proof lines, an empty line, then the checkpoint note. The witness verifies a signature by a key it trusts for the origin (404 unknown origin, 403 none or a failing one), that the old size is not above the new (400) and equals the size it last co-signed (409, body = that size, `text/x.tlog.size`), that the consistency proof — or, for an unchanged size, the root — checks out (422), persists the new checkpoint, and answers 200 with its cosignature line.
- `GET /witness/v1/logs/<hex SHA-256 of origin>/checkpoint`: the newest checkpoint it co-signed, log signature and its own cosignature on it.

The witness signs with a key of its own (`witness.key`), never the log key: the cosignature message is not bound to the cosigner's name, so one key must not serve two roles. Its `vkey` and name are at `GET /api/v1/issuer` under `witness`.

### 8.2 Getting this log witnessed

`python -m histor witness-submit --log <log> --witness <submission prefix> [--submit-to-log]` takes a log's checkpoint and consistency proof to any witness that speaks the protocol, and stores the returned line in the log so that `/checkpoint` carries it. With `--witnesses-file` (a JSON list of `{name, vkey, url}`) it pushes to several witnesses and keeps a line only after verifying it under that witness's `vkey`; the log's own `POST /api/v1/log/cosignatures` checks shape only, so this verification is the one that matters.

The public instance pushes to the staging witnesses of [witness-network.org](https://witness-network.org) once it is listed there: `deploy/witness_push.sh` with `histor-witness-push.{service,timer}` (every 10 minutes, 144 requests per witness per day — the `qpd` of the listing), witnesses in `deploy/witnesses.staging.json`. A monitor (§5) that also keeps the cosigned checkpoints of two independent witnesses has, for the first time, evidence that the log shows them the same tree.

## 9. What is not specified, and why

- **Publisher signatures.** Not part of this format. The publisher side is a declaration (MTL/1 tool sets named in the registry `server.json` or in a file on the publisher's domain), which identifies a publisher by namespace, not by key.
- **Who the witnesses are.** The format lets any C2SP witness co-sign a head; it does not say which witnesses a reader should trust or how many. That is the reader's policy.
- **Time.** `timestamp` and every label's `observedAt` are the log's assertions, made unchangeable, not made true.
- **Completeness.** A server the log did not dial is not in the log.

## 10. Test vectors

[`test-vectors/vectors.json`](../../test-vectors/vectors.json), generated by [`test-vectors/generate.py`](../../test-vectors/generate.py) from a fixed seed, is normative for this version. It carries: eight leaf documents with their canonical bytes, leaf hashes and digests; the root for every tree size 1–8; every inclusion proof for every (index, size); every consistency proof for every (first, second); signed heads at sizes 5 and 8 under the fixed test key; the size-8 head as a checkpoint note with the log signature and a `cosignature/v1` by a witness test key (`checkpoints`, verified against the reference Go implementations `golang.org/x/mod/sumdb/note` and `github.com/transparency-dev/formats/note`); and negative cases that a verifier MUST reject (a tampered leaf, a head whose `type` was changed after signing, a head whose signature was moved to another size). `tests/test_spec_vectors.py` checks the running code against the file, and an implementation that disagrees with the file is non-conformant whatever its own tests say.

The test key's seed is in the generator and is for vectors only. A log MUST NOT use it.
