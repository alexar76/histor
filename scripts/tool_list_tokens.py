"""Tokens in every server's tool list, as a client sends it to a model and as listed (docs/tool-list-cost.md).

usage: tool_list_tokens.py EXPORT.b64.gz OUT.json        (needs tiktoken; we run it in a container)

EXPORT is the current tool list of every server in the log, one base64 line each (PostgreSQL wraps base64 with
literal \\n), from:

  copy (with cur as (
      select distinct on (t.endpoint) t.endpoint,
             case when t.transport = 'stdio' then 'package' else 'remote' end as skind, t.current_toolset, t.current_count
      from targets t where t.delisted = 0 and t.current_toolset is not null and t.current_count > 0
      order by t.endpoint, t.last_ok desc nulls last)
    select encode(convert_to(json_build_object('endpoint', cur.endpoint, 'kind', cur.skind, 'count', cur.current_count,
                                               'tools', b.body::json)::text, 'UTF8'), 'base64')
    from cur join blobs b on b.digest = cur.current_toolset) to stdout;

A plain SELECT of the same rows makes psql hold every list in memory and exit with nothing; COPY streams.
"""
import base64, gzip, json, statistics, sys
import tiktoken

enc = tiktoken.get_encoding("o200k_base")
src, out = sys.argv[1], sys.argv[2]


def sent(tool):
    # What a client puts in a model request for one tool: name, description, input schema.
    d = {"name": tool.get("name"), "description": tool.get("description") or ""}
    d["input_schema"] = tool.get("inputSchema") or {"type": "object"}
    return d


def tokens(obj):
    return len(enc.encode(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), disallowed_special=()))


rows = {"package": [], "remote": []}
per_tool = {"package": [], "remote": []}
bad = 0
with gzip.open(src, "rt") as f:
    for line in f:
        line = line.strip().replace("\\n", "")
        if not line:
            continue
        try:
            r = json.loads(base64.b64decode(line))
        except Exception:
            bad += 1
            continue
        tools = r["tools"] if isinstance(r["tools"], list) else []
        if not tools:
            continue
        s = [tokens(sent(t)) for t in tools if isinstance(t, dict)]
        full = tokens(tools)
        rows[r["kind"]].append({"endpoint": r["endpoint"], "tools": len(tools), "sent": sum(s), "full": full})
        per_tool[r["kind"]].extend(s)


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))]


summary = {"tokenizer": "tiktoken o200k_base", "unreadable_lines": bad, "kinds": {}}
for k, rs in rows.items():
    sent_t = [r["sent"] for r in rs]
    full_t = [r["full"] for r in rs]
    tools = [r["tools"] for r in rs]
    summary["kinds"][k] = {
        "servers": len(rs), "definitions": sum(tools),
        "tools_per_server": {"mean": round(statistics.mean(tools), 1), "median": q(tools, 0.5), "p90": q(tools, 0.9), "max": max(tools)},
        "tokens_sent_per_server": {"mean": round(statistics.mean(sent_t)), "median": q(sent_t, 0.5), "p75": q(sent_t, 0.75), "p90": q(sent_t, 0.9), "p99": q(sent_t, 0.99), "max": max(sent_t)},
        "tokens_full_per_server": {"median": q(full_t, 0.5), "p90": q(full_t, 0.9)},
        "tokens_per_tool": {"mean": round(statistics.mean(per_tool[k]), 1), "median": q(per_tool[k], 0.5), "p90": q(per_tool[k], 0.9)},
        "servers_over": {f">{n}": sum(1 for x in sent_t if x > n) for n in (1000, 5000, 10000, 50000, 100000)},
        "largest": sorted(({"endpoint": r["endpoint"], "tools": r["tools"], "sent": r["sent"]} for r in rs), key=lambda r: -r["sent"])[:10],
    }
json.dump(summary, open(out, "w"), indent=1)
print(json.dumps({k: {kk: v[kk] for kk in ("servers", "definitions", "tools_per_server", "tokens_sent_per_server", "tokens_per_tool")} for k, v in summary["kinds"].items()}, indent=1))
