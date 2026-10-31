"""heldfast catalogue header (protocol, args, measured_at) for given endpoints."""

import gzip
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

here = pathlib.Path(__file__).parent
c = (here / "heldfast" / "feed-commit.txt").read_text().strip()
want = set(sys.argv[1:])
found = {}
for f in os.listdir(here / "heldfast/feed/state"):
    s = json.load(open(here / "heldfast/feed/state" / f))
    key = s.get("url") or "npm:" + s["package"]
    if key in want and s.get("tools"):
        found[key] = (f[:-5], s["version"])
for key, (n, v) in found.items():
    lst = json.loads(
        subprocess.run(
            ["gh", "api", f"repos/rufat325/heldfast-feed/contents/catalogues/{n}?ref={c}"],
            capture_output=True,
        ).stdout
    )
    names = [e["name"] for e in lst if isinstance(e, dict) and e["name"].startswith(v)]
    if not names:
        print(key, "no catalogue for", v)
        continue
    raw = urllib.request.urlopen(
        f"https://raw.githubusercontent.com/rufat325/heldfast-feed/{c}/catalogues/{n}/{urllib.request.quote(names[0])}"
    ).read()
    if names[0].endswith(".gz"):
        raw = gzip.decompress(raw)
    cat = json.loads(raw)
    print(key, {k: cat[k] for k in cat if k not in ("tools",)})
