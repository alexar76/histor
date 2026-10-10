# Behaviour observer 3 and evidence policy

Remote MCP endpoints still receive only initialization and listing. Registry packages can run lifecycle scripts and up to 15 synthetic tool calls in gVisor. The new observer reports success, error, timeout and skipped counts separately. It cannot claim complete coverage when tracing, source attribution, supported schemas, protocol payloads or any phase are incomplete. A complete report means the bounded scenario completed, not every possible execution path was explored.

Lifecycle observation precedes the entry-point check. A package without a command can still run a harmful postinstall. Missing tracing never becomes an empty clean result. Failed opens are not successful credential opens. Relative paths and unsupported trace records explicitly leave coverage incomplete. `.env` is a decoy too. DNS and sink records are attributed to the container IP and time interval; missing IP never selects all containers.

`install.sh` installs a mandatory iptables policy independently of UFW. Only DNS and the HTTP/HTTPS/SMTP trap ports on the internal gateway are reachable. Forwarding from that bridge is dropped. A root service reapplies and verifies the rules every 30 seconds; a missing or expired root-owned health record disables behavioural execution. Containers without behavioural prerequisites retain `--network none`. The HTTPS trap uses a separate CA; only its public certificate enters containers. Traps never forward traffic. JSON recipient fields and SMTP envelope recipients expose hidden copies on the same destination. Unsupported protocols, chunked/opaque payloads, SMTP AUTH/STARTTLS and size limits are incomplete coverage, not clean evidence.

## Before production rollout

The local suite covers HTTP, HTTPS and SMTP with loopback-only synthetic data. It does **not** prove the Linux host firewall or gVisor trace format. Run the MOMUS behaviour campaign under the configured gVisor runtime before replacing production. The campaign refuses unisolated execution. The 2026-10-10 acceptance run uses the actual sandbox host, with the existing observer-1 API left running during preparation. The candidate alone owns the previously unused trap listeners. A catalogue-wide census is a separate operation.

```sh
python -m momus.engine.behaviour_campaign --seed 2026-10 --out /var/lib/histor-sandbox/campaigns/acceptance \
  --observer /usr/local/bin/histor-observe
```

Run that command as the sandbox account with permission to bind the trap ports, with no other listener occupying them. The source corpus and initial report are written before execution. Per-case infrastructure errors are saved as incomplete decisions and make the run fail. An incomplete inspection, miss or false positive exits nonzero. Saved source is restricted to the generated template vocabulary on replay. The monthly systemd templates under `momus/deploy/` target a **dedicated test host**, require free trap ports (and fail if the production listener owns them), and are not installed automatically. GitHub's monthly job generates the frozen fixtures and runs protocol regressions; it does not claim to execute gVisor on GitHub runners.

## Registry evidence and decisions

`provenance` is registry metadata presence (`true`/`false`/`null`); a 503 is unknown. `provenanceVerified` is separate and remains `null` until an actual verifier binds an attestation to the artifact. PyPI publisher identity is unknown, not invented. Metadata-only signals are review leads, not proof of account compromise. Dependency constraint changes are now surfaced even when the dependency name stays the same.

THEMIS policy `themis-mcp/2` verifies one signed `/check` answer against `THEMIS_HISTOR_ISSUER_DID`, or HTTPS key discovery at the operator-configured HISTOR origin. It never joins an unsigned second record. Missing/stale/mismatched scans and classifier coverage cannot approve. Package approval also requires complete bound behaviour and verified build evidence, so metadata presence by itself cannot yield an install approval. Current metadata-only observations therefore remain unknown/review unless stronger evidence exists.

## Events, subscriptions and census

Schema revision 8 appends signed security events in the same transaction as the observation, including changed package metadata/behaviour even if definitions did not change. Definition changes have their own events. Repeated identical evidence does not emit a new event. Existing `/feed.xml` remains the definition feed; `/security-feed.xml?watch=<target-id>,…` adds package evidence. Atom is bounded to recent events. Use `/api/v1/security-events?after=<cursor>&limit=200&watch=…` for lossless consumption and persist `nextCursor` only after processing.

`histor webhooks /etc/histor/subscriptions.json` sends at most one pending event per configured subscription per invocation. The operator-owned file is a JSON array of `{ "id": "consumer", "url": "https://consumer.example/histor", "secret": "<32+ secret characters>" }`. URLs never come from packages or a public subscription API. The worker disables redirects and environment proxies. Cursor, retry count and lease survive restarts. Delivery is at least once: receivers deduplicate `X-Histor-Event-ID`, validate timestamp freshness and compare `X-Histor-Signature` to HMAC-SHA256 of `timestamp + "." + exact request bytes`. Failed deliveries retain their cursor and back off up to one day. Keep the configuration file private; schedule the command in the deployment's worker system. No real webhook was sent or configured during this change.

`histor census > census.json` exports a signed latest-version manifest, distinct package/target denominators, complete/incomplete phase counts, tool-call coverage, evidence hashes and freshness. It cannot turn a catalogue size into a claim that all packages were tested. The actual 45k-package run and the public report require those observations first.

The scanner vendors the exact local WARDEN 0.13.0 npm tarball with a lock-file integrity hash. This avoids depending on an unpublished npm version. Deployment acceptance and installed component identities are recorded separately; npm publication is not required.

## Host integration corrections

The acceptance host also runs nginx and SMTP. Trap listeners therefore bind only the bridge gateway on high ports 18025/18080/18443/18587; root-owned NAT rules redirect the standard ports before Docker DNAT. The test checks HTTP/SMTP connectivity to traps and denied connections to host SSH, the observer API and an external documentation-range address. This does not change the host's public web/mail listeners.

The first real run exposed npm reading its own planted `.npmrc`, unparsed normal DNS/netlink syscalls, and a transient Docker `inspect` timeout. The observer separates npm configuration from decoys, parses socket families and explicit destinations, and retries address discovery within a fixed deadline. Regression tests retain direct UDP destinations, unresolved-socket failures and PID-1 secret reads.

## Production acceptance: 2026-10-10

The complete campaign ran under gVisor release-20261005.0 on the actual sandbox host: **72/72 completed, 54/54 malicious behaviours detected, 0/18 false positives, 0 incomplete inspections**. The corpus SHA-256 is `914351c747d353553619e24f6dddcb04bb9e1887b838362ebdbdba9d1553f7c4`. The installed observer differs from the measured file only by CI formatting; the focused observer/sink suite passed again. Both hashes are retained in the acceptance record.

Production now runs observer 3, HISTOR schema 8 with the measured WARDEN 0.13.0 tarball, THEMIS policy `themis-mcp/2`, and MOMUS generator 3. HISTOR's issuer and log continuity and THEMIS's signing identity were checked; persistent volumes and operator configuration were preserved. The interrupted HISTOR crawl was explicitly resumed. Its historical interruption marker remains visible until a completed crawl supersedes it.

Full tests: HISTOR 445 with SQLite and PostgreSQL; WARDEN 426; THEMIS 182; MOMUS 498 with one skip. Both HISTOR Ruff and container startup checks passed. Production API checks covered the signed THEMIS verdict, HISTOR event API/feed, and the pinned HISTOR-to-sandbox connection. The rollout keeps database/key backups and compatible rollback images/containers. See [`momus/docs/production-acceptance-v13.json`](../../momus/docs/production-acceptance-v13.json) for component identities and limits.
