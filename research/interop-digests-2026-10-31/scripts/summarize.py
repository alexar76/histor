"""One reason per non-identical server (first rule that applies), from the full field diff
(out/fullfield.jsonl via toolclass.py), HISTOR's own page counts (out/pag131.json) and heldfast's
catalogue history. Writes out/results.json and out/reasons.jsonl."""

import collections
import json
import os
import pathlib

here = pathlib.Path(__file__).parent
rows = [json.loads(line) for line in open(here / "out" / "heldfast-pairs.jsonl")]
classed = {
    (r["server"], r["endpoint"]): r for r in json.loads((here / "out" / "fullfield-classed.json").read_text())
}
pages = {o["endpoint"]: o["pages"] for o in json.loads((here / "out" / "pag131.json").read_text())}
state = {}
for name in os.listdir(here / "heldfast" / "feed" / "state"):
    d = json.loads((here / "heldfast" / "feed" / "state" / name).read_text())
    if d.get("tools"):
        state[d["url"] if d["package"].startswith("remote/") else "npm:" + d["package"]] = d

# heldfast 477131b (2026-09-29T07:42:47Z): npm servers measured offline from then on; before, with network.
OFFLINE_SINCE = "2026-09-29T07:42:47"
PROTOCOL = {"outputSchema/title absent at heldfast"}
ENV = {"launch environment in text", "date/uuid default"}


def reason(r):
    hf = state[r["endpoint"]]
    classes = (
        collections.Counter(classed[(r["server"], r["endpoint"])]["classes"])
        if r["verdict"] != "identical"
        else {}
    )
    att = hf.get("attempted") or {}
    if att and att.get("at", "") > (hf.get("checked_at") or hf.get("published") or ""):
        return "heldfast's later reads failed, its copy is older"
    if r["kind"] == "npm" and r["historVersion"] and r["historVersion"] != r["heldfastVersion"]:
        return "different npm versions"
    subset = not r["onlyTheirs"] and r["ourTools"] > r["theirTools"] and r["common"] == r["theirTools"]
    if subset and (pages.get(r["endpoint"]) or 1) > 1 and set(classes) <= PROTOCOL:
        return "heldfast read the first tools/list page only"
    if classes and set(classes) <= PROTOCOL and not r["onlyOurs"] and not r["onlyTheirs"]:
        return "server sent no outputSchema/title to heldfast's request (old handshake or no version header)"
    if set(classes) & ENV or any(
        "envkey" in n or "placeholder" in n for n in r["onlyOurs"] + r["onlyTheirs"]
    ):
        return "definition contains the reader's launch environment"
    if r["kind"] == "npm" and (r["heldfastRead"] or "") < OFFLINE_SINCE:
        return "npm, same version: heldfast ran it with network (before 29 Sep), HISTOR without"
    if r["ourSeenByHeldfast"] == r["ourTools"]:
        return "same definitions, read at different moments"
    if r["kind"] == "remote":
        return "hosted server edited between the two reads"
    return "npm, same version, offline on both sides: unexplained"


known = [r for r in rows if r["extrasKnown"]]
out = []
for r in known:
    if r["verdict"] != "identical":
        out.append({"server": r["server"], "endpoint": r["endpoint"], "reason": reason(r)})
with open(here / "out" / "reasons.jsonl", "w") as fh:
    for o in out:
        fh.write(json.dumps(o, ensure_ascii=False) + "\n")
reasons = collections.Counter(o["reason"] for o in out)
multi = [
    r
    for r in rows
    if (pages.get(r["endpoint"]) or 1) > 1 and not r["onlyTheirs"] and r["ourTools"] > r["theirTools"]
]
tool_classes = collections.Counter()
for c in classed.values():
    tool_classes.update(c["classes"])
results = {
    "joined_servers": len(rows),
    "joined_npm": sum(r["kind"] == "npm" for r in rows),
    "joined_remote": sum(r["kind"] == "remote" for r in rows),
    "servers_all_fields_known": len(known),
    "servers_identical": sum(r["verdict"] == "identical" for r in known),
    "tools_compared": sum(r["common"] for r in known),
    "tools_identical": sum(r["identical"] for r in known),
    "reasons_non_identical": dict(reasons.most_common()),
    "differing_tools_by_class": dict(tool_classes.most_common()),
    "paginated_servers_heldfast_first_page": len(multi),
    "paginated_tools_missing_at_heldfast": sum(r["ourTools"] - r["theirTools"] for r in multi),
}
(here / "out" / "results.json").write_text(json.dumps(results, indent=1))
print(json.dumps(results, indent=1))
