"""What a tool list costs a model in tokens, part by part, and what a change did to that cost.

Counted the way docs/tool-list-cost.md counted 34,374 servers on 2026-10-19: for each tool, what a
client puts into a model request, ``{name, description, input_schema}`` as compact JSON, in
``o200k_base``. That total (``sent``) is split into the JSON-encoded names, descriptions and input
schemas of the tools, plus ``structure``: the keys and punctuation around them, and the few tokens
a part shares with its neighbours at its edges. The four parts add up to ``sent``.

Two totals sit beside it and are not part of it: ``outputSchemas`` (most clients do not send an
output schema to the model, some do) and ``listed`` (the tool objects exactly as given; for
HISTOR's own record, the four members MTL/1 keeps).

The tokenizer is tiktoken, the optional ``cost`` extra. Nothing imports it until a count is asked
for; without it a count raises :class:`CostUnavailable` and the service answers 503.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

FORMAT = "histor.tool-cost/v1"
DIFF_FORMAT = "histor.tool-cost-diff/v1"
TOKENIZER = "o200k_base"
COUNTED_AS = "per tool {name, description, input_schema} as compact JSON: what a client sends a model"
PARTS = ("names", "descriptions", "inputSchemas", "structure")    # add up to "sent"
TOTALS = ("sent", *PARTS, "outputSchemas", "listed")
LABELS = {"sent": "sent to the model", "names": "names", "descriptions": "descriptions",
          "inputSchemas": "input schemas", "structure": "keys and punctuation",
          "outputSchemas": "output schemas (not in the total)", "listed": "as listed (not in the total)"}
# What a regression check looks at by default: the bill, and the two parts a release usually moves.
GATED = ("sent", "descriptions", "inputSchemas")

Count = Callable[[str], int]


class CostUnavailable(RuntimeError):
    """No tokenizer: the ``cost`` extra is not installed."""


class CostBusy(RuntimeError):
    """Every counting slot stayed taken for the whole wait."""


_encoder: Count | None = None
_encoder_lock = threading.Lock()


def encoder() -> Count:
    """The o200k_base counter, built once per process (the table takes a second or two)."""
    global _encoder
    with _encoder_lock:
        if _encoder is None:
            try:
                import tiktoken
            except ImportError as exc:
                raise CostUnavailable("counting needs tiktoken: pip install 'aimarket-histor[cost]'") from exc
            enc = tiktoken.get_encoding(TOKENIZER)
            # Ordinary text throughout: a tool description that spells a special token is text.
            _encoder = lambda text: len(enc.encode_ordinary(text))  # noqa: E731
    return _encoder


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sent_form(tool: dict[str, Any]) -> dict[str, Any]:
    """What a client puts in a model request for one tool (scripts/tool_list_tokens.py)."""
    return {"name": tool.get("name"), "description": tool.get("description") or "",
            "input_schema": tool.get("inputSchema") or {"type": "object"}}


def tool_cost(tool: dict[str, Any], count: Count) -> dict[str, Any]:
    s = sent_form(tool)
    sent = count(_json(s))
    name, description, schema = count(_json(s["name"])), count(_json(s["description"])), count(_json(s["input_schema"]))
    output = tool.get("outputSchema")
    row = {
        "name": str(s["name"]),
        "sent": sent,
        "parts": {"name": name, "description": description, "inputSchema": schema,
                  "structure": sent - name - description - schema},
        "outputSchema": count(_json(output)) if isinstance(output, dict) else 0,
    }
    # Each top-level input property as it is written in the schema, `"name":{...}`: which field grew.
    props = s["input_schema"].get("properties") if isinstance(s["input_schema"], dict) else None
    if isinstance(props, dict) and props:
        row["properties"] = {str(k): count(_json(str(k)) + ":" + _json(v)) for k, v in props.items()}
    return row


def cost(tools: list[Any], count: Count | None = None) -> dict[str, Any]:
    """The cost of one tool list: totals, then every tool by name."""
    count = count or encoder()
    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        row = tool_cost(tool, count)
        # A raw tools/list may repeat a name (MTL/1 refuses such a set); each copy still costs.
        n = seen[row["name"]] = seen.get(row["name"], 0) + 1
        if n > 1:
            row["name"] = f"{row['name']}#{n}"
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    totals = {
        "sent": sum(r["sent"] for r in rows),
        "names": sum(r["parts"]["name"] for r in rows),
        "descriptions": sum(r["parts"]["description"] for r in rows),
        "inputSchemas": sum(r["parts"]["inputSchema"] for r in rows),
        "structure": sum(r["parts"]["structure"] for r in rows),
        "outputSchemas": sum(r["outputSchema"] for r in rows),
        "listed": count(_json(tools)),
    }
    return {"format": FORMAT, "tokenizer": TOKENIZER, "countedAs": COUNTED_AS, "toolCount": len(rows),
            "totals": totals, "tools": rows}


def empty() -> dict[str, Any]:
    """The cost of no tool list: the side of a diff where a server had none."""
    return {"format": FORMAT, "tokenizer": TOKENIZER, "countedAs": COUNTED_AS, "toolCount": 0,
            "totals": dict.fromkeys(TOTALS, 0), "tools": []}


def _delta(before: int, after: int) -> dict[str, int]:
    return {"before": before, "after": after, "delta": after - before}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What moved between two costs: every total, tools added and removed, and each tool whose count moved.

    A tool whose text changed but whose counts did not is in ``unchanged``: this is a diff of cost.
    """
    if before.get("tokenizer") != after.get("tokenizer"):
        raise ValueError(f"counted with different tokenizers: {before.get('tokenizer')} and {after.get('tokenizer')}")
    b = {r["name"]: r for r in before["tools"]}
    a = {r["name"]: r for r in after["tools"]}
    changed = []
    for name in sorted(a.keys() & b.keys()):
        x, y = b[name], a[name]
        if x["sent"] == y["sent"] and x["parts"] == y["parts"] and x["outputSchema"] == y["outputSchema"]:
            continue
        bp, ap = x.get("properties", {}), y.get("properties", {})
        moved = sorted(({"name": k, **_delta(bp.get(k, 0), ap.get(k, 0))} for k in bp.keys() | ap.keys()
                        if bp.get(k, 0) != ap.get(k, 0)), key=lambda p: (-abs(p["delta"]), p["name"]))
        changed.append({"name": name, "sent": _delta(x["sent"], y["sent"]),
                        "description": _delta(x["parts"]["description"], y["parts"]["description"]),
                        "inputSchema": _delta(x["parts"]["inputSchema"], y["parts"]["inputSchema"]),
                        "outputSchema": _delta(x["outputSchema"], y["outputSchema"]),
                        "properties": moved})
    changed.sort(key=lambda c: (-abs(c["sent"]["delta"]), c["name"]))
    by_cost = lambda rows: sorted(rows, key=lambda r: (-r["sent"], r["name"]))  # noqa: E731
    return {
        "format": DIFF_FORMAT,
        "tokenizer": after["tokenizer"],
        "countedAs": after.get("countedAs", COUNTED_AS),
        "toolCount": _delta(before["toolCount"], after["toolCount"]),
        "totals": {k: _delta(before["totals"].get(k, 0), after["totals"].get(k, 0)) for k in TOTALS},
        "added": by_cost({"name": n, "sent": a[n]["sent"]} for n in a.keys() - b.keys()),
        "removed": by_cost({"name": n, "sent": b[n]["sent"]} for n in b.keys() - a.keys()),
        "changed": changed,
        "unchanged": len(a.keys() & b.keys()) - len(changed),
    }


def parse_limit(text: str) -> tuple[str, float]:
    """``"10%"`` → ``("percent", 10.0)``; ``"500"`` → ``("tokens", 500.0)``."""
    text = text.strip()
    try:
        if text.endswith("%"):
            value, kind = float(text[:-1]), "percent"
        else:
            value, kind = float(int(text)), "tokens"
    except ValueError:
        raise ValueError(f"not a limit: {text!r} (write 10% or 500)") from None
    if value < 0:
        raise ValueError(f"a limit cannot be negative: {text!r}")
    return kind, value


def regressions(d: dict[str, Any], *, max_increase: str | None = "10%", budget: int | None = None,
                noise: int = 50, gated: tuple[str, ...] = GATED) -> list[str]:
    """Why a release should not ship as it is; empty when it may.

    Each gated total is checked on its own, so a schema that doubles is named even when shorter
    descriptions hide it in the sum. A growth of fewer than ``noise`` tokens never counts.
    """
    found: list[str] = []
    if max_increase is not None:
        kind, limit = parse_limit(max_increase)
        for key in gated:
            t = d["totals"][key]
            grew = t["delta"]
            if grew <= 0 or grew < noise:
                continue
            pct = grew / t["before"] * 100 if t["before"] else float("inf")
            over = pct > limit if kind == "percent" else grew > limit
            if over:
                found.append(f"{LABELS[key]} +{grew:,} tokens ({_pct(t)}), over the {max_increase} allowed")
    if budget is not None and d["totals"]["sent"]["after"] > budget:
        found.append(f"the tool list is {d['totals']['sent']['after']:,} tokens, over the budget of {budget:,}")
    return found


def _pct(t: dict[str, int]) -> str:
    if not t["before"]:
        return "new" if t["after"] else "0%"
    return f"{t['delta'] / t['before'] * 100:+.1f}%"


def _signed(n: int) -> str:
    return f"{n:+,}" if n else "0"


def markdown(d: dict[str, Any], problems: list[str] | None = None, *, top: int = 15) -> str:
    """A diff as a CI job summary or a PR comment."""
    t = d["totals"]["sent"]
    moved = f"{_signed(t['delta'])}, {_pct(t)}" if t["delta"] else "unchanged"
    out = [f"### Tool list cost: {t['before']:,} → {t['after']:,} tokens ({moved})", ""]
    out += ["| | before | after | change |", "|---|---:|---:|---:|"]
    for key in TOTALS:
        row = d["totals"][key]
        label = f"**{LABELS[key]}**" if key == "sent" else LABELS[key]
        change = _signed(row["delta"]) + (f" ({_pct(row)})" if row["delta"] and row["before"] else "")
        out.append(f"| {label} | {row['before']:,} | {row['after']:,} | {change} |")
    tc = d["toolCount"]
    out += ["", f"Tools: {tc['before']} → {tc['after']} ({len(d['added'])} added, {len(d['removed'])} removed, "
                f"{len(d['changed'])} changed in cost, {d['unchanged']} unchanged)"]
    rows = ([(r["name"], "added", r["sent"], None, None) for r in d["added"]]
            + [(r["name"], "removed", -r["sent"], None, None) for r in d["removed"]]
            + [(c["name"], "", c["sent"]["delta"], c["description"]["delta"], c["inputSchema"]["delta"])
               for c in d["changed"]])
    rows.sort(key=lambda r: (-abs(r[2]), r[0]))
    if rows:
        out += ["", "| tool | | sent | description | input schema |", "|---|---|---:|---:|---:|"]
        for name, what, sent, desc, schema in rows[:top]:
            cell = lambda v: "" if v is None else _signed(v)  # noqa: E731
            out.append(f"| `{_cell(name)}` | {what} | {_signed(sent)} | {cell(desc)} | {cell(schema)} |")
        if len(rows) > top:
            out.append(f"| … {len(rows) - top} more | | | | |")
    fields = [c for c in d["changed"] if c.get("properties")][:8]
    if fields:
        out += ["", "Where in the input schemas:", ""]
        for c in fields:
            props = ", ".join(f"`{_cell(p['name'])}` {_signed(p['delta'])}"
                              + (" (new)" if not p["before"] else " (removed)" if not p["after"] else "")
                              for p in c["properties"][:5])
            more = f", … {len(c['properties']) - 5} more" if len(c["properties"]) > 5 else ""
            out.append(f"- `{_cell(c['name'])}`: {props}{more}")
    if problems is not None:
        out.append("")
        out += [f"- ❌ {p}" for p in problems] if problems else ["✅ within the limits"]
    out += ["", f"<sub>{d['tokenizer']}; {d['countedAs']}. Counted by `histor tool-cost`.</sub>", ""]
    return "\n".join(out)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("`", "'").replace("\n", " ")[:120]


class CostCache:
    """Costs of stored tool sets by digest. A set never changes under its digest, so a cost is kept;
    at most ``slots`` sets are counted at once, and a caller waits ``wait`` seconds for a slot."""

    def __init__(self, size: int = 256, slots: int = 2, wait: float = 20.0, count: Count | None = None) -> None:
        self._size, self._wait, self._count = size, wait, count
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(slots)

    def get(self, digest: str, body: Callable[[], str | None]) -> dict[str, Any] | None:
        """The cost of the set stored under *digest*; None when there is no such set."""
        with self._lock:
            hit = self._cache.get(digest)
            if hit is not None:
                self._cache.move_to_end(digest)
                return hit
        count = self._count or encoder()
        if not self._slots.acquire(timeout=self._wait):
            raise CostBusy("every counting slot is taken")
        try:
            text = body()
            if text is None:
                return None
            tools = json.loads(text)
            value = cost(tools if isinstance(tools, list) else [], count)
        finally:
            self._slots.release()
        with self._lock:
            self._cache[digest] = value
            while len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return value
