# Independent remote polling

Interpretation essentials in five languages: [English](measurement-limits.md),
[Русский](measurement-limits.ru.md), [Español](measurement-limits.es.md),
[Français](measurement-limits.fr.md), [中文](measurement-limits.zh.md). This queue separates remote
checks from package work; it cannot guarantee capture of every intermediate tool state.

Registry discovery and npm/PyPI planning keep the daily `HISTOR_CRAWL_INTERVAL_S` schedule.
Remote HTTP targets use a separate persistent queue and worker; a long package plan or sandbox
run does not delay the next remote check. Manual `histor crawl` still observes both kinds.

| Setting | Default | Meaning |
| --- | --- | --- |
| `HISTOR_REMOTE_INTERVAL_S` | 21600 | Normal target interval, six hours. Zero restores remotes to the daily crawl. |
| `HISTOR_REMOTE_CHANGED_INTERVAL_S` | 1800 | Target interval after at least two observed changes in the window; capped at the normal interval. |
| `HISTOR_REMOTE_CHANGE_WINDOW_S` | 604800 | Seven-day window for deciding which endpoints change frequently. |
| `HISTOR_CRAWL_WORKERS` | 12 | Remote observation concurrency. |
| `HISTOR_CRAWL_PER_HOST` | 2 | Host concurrency and maximum targets from one host in a short batch. |

Set **both** crawl and remote intervals to zero to disable automatic observation. Registry discovery
is still needed to add/remove targets; turning off its schedule does not refresh the registry.
The first successful discovery after upgrading seeds the remote queue. Its first pass may have
a backlog; neither the configured cadence nor a worker count establishes achieved throughput.

The queue prioritizes endpoints with recent repeated changes, then the oldest due time. State
survives restarts. Unchanged successful checks use the normal/frequent interval. Failures back
off exponentially, to at most a day; unauthenticated/forbidden responses wait at least a day.
HTTP 429 starts an exponential backoff of at least the normal remote interval (six hours by
default, and at least one hour for shorter configurations) and pauses new batches for the entire hostname.
The cap is one day, or the configured normal interval when that is longer than a day.
Requests already in flight may finish. The reader still enforces its existing request timeout
and private-address restrictions. Opted-out, unsupported and delisted targets are excluded.

Observations are scanned and committed as they finish. Results already ready together share a
WARDEN scan; an unfinished endpoint does not hold the completed results. Outstanding work is
bounded to twice the remote worker count, including during a full manual crawl.

All observations use the existing canonicalization, WARDEN scans, label chain and Merkle log.
Remote runs have a separate `runs.kind`: they do not replace the public daily `lastRun` or cause
an interrupted package cycle to skip work. Endpoint batches are serialized through commit, so
an admin-triggered crawl in the same service cannot overwrite a newer observation with an older one. Dataset
exports read one consistent database snapshot while remote polling continues.

With independent polling enabled, `HISTOR_CLASSIFIER_MAX_PER_CRAWL` also caps the combined paid
classifier attempts per UTC day across remote and package runs. Reservations are stored before
each call, including failed calls, and survive restarts. Cached verdicts remain free. With
independent polling disabled the previous per-cycle budget behavior is retained.

`GET /api/v1/stats` adds `remotePolling`: configuration, running time, last error/run, scheduled
and overdue counts, number in the frequent group, and the mean/maximum of each endpoint's last
measured observation gap. These gaps include downtime and capacity delays; they are not a
historical latency percentile. Compare overdue work and actual gaps before tightening intervals.

Polling records observed transitions, not every transition a server makes. A→B→A between two
checks remains invisible, regardless of the interval. More frequent polling reduces this gap;
it cannot eliminate it or prove that an unchanged server stayed unchanged between observations.

The deploy script resumes interrupted discovery/package work with `include_remote=false` when
independent polling is enabled. An operator can still request a full manual crawl (the API default).
