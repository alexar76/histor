"""Classify each differing tool by all of its differing paths (out/fullfield.jsonl)."""

import collections
import json
import pathlib
import re
import sys

here = pathlib.Path(__file__).parent
ENV = re.compile(
    r"churn|placeholder|histor-envkey|histor_envkey|/home/histor|/tmp/|/root/|\\\\Users|/Users/|claude-[0-9a-f]{4}\b",
    re.I,
)
DATE = re.compile(r"20\d\d-\d\d-\d\d")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def text_of(d):
    return json.dumps(d, ensure_ascii=False)


def tool_class(t):
    if t.get("missing"):
        return "heldfast kept no copy"
    ds = t["diffs"]
    if all(
        (d["path"] == "$.output_schema" and d.get("only") == "histor")
        or (d["path"] == "$.title" and d.get("hunks") and d["hunks"][0][0] == "")
        for d in ds
    ):
        return "outputSchema/title absent at heldfast"
    blob = text_of(ds)
    if ENV.search(blob):
        return "launch environment in text"
    if all(".default" in d["path"] and (DATE.search(text_of(d)) or UUID.search(text_of(d))) for d in ds):
        return "date/uuid default"
    if all(d["path"].startswith("$.annotations") for d in ds):
        return "annotations only"
    if all(d["path"] == "$.icons" or d["path"].startswith("$.icons") for d in ds):
        return "icons only"
    if all(d["path"].startswith("$.output_schema") for d in ds):
        return "outputSchema content"
    return "text/schema edit"


rows = [json.loads(line) for line in open(here / "out" / "fullfield.jsonl")]
per_tool = collections.Counter()
for r in rows:
    r["classes"] = collections.Counter(tool_class(t) for t in r["tools"])
    per_tool.update(r["classes"])
print("differing tools by class:", dict(per_tool.most_common()))
(here / "out" / "fullfield-classed.json").write_text(
    json.dumps(
        [{k: r[k] for k in r if k != "tools"} | {"classes": dict(r["classes"])} for r in rows],
        ensure_ascii=False,
        indent=1,
    )
)

if len(sys.argv) > 1:
    for r in rows:
        if r["endpoint"] in sys.argv[1:]:
            print(json.dumps(r, ensure_ascii=False)[:3000])
