#!/usr/bin/env python3
"""Check that a HISTOR label is in the log: RFC 9162 inclusion proof against the signed tree head.

  python3 verify_label_inclusion.py <label-id>

Stdlib only. The leaf is the label document in RFC 8785 (JCS) form; labels carry strings, integers,
booleans and nested objects, for which sorted keys + compact separators + UTF-8 is the JCS form.
Then check the STH's Ed25519 signature with the key from /api/v1/issuer (docs/log.md, step 1) and
compare the head with https://histor.modelmarket.dev/checkpoint or a witness's cosigned copy.
"""
import hashlib
import json
import sys
import urllib.parse
import urllib.request

H = "https://histor.modelmarket.dev/api/v1/labels/"

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "histor-verify-example/1"}), timeout=30).read()

def jcs(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def root_from_path(index, size, leaf_hash, path):
    """RFC 9162 §2.1.3.2: recompute the root from an inclusion path."""
    fn, sn, r = index, size - 1, leaf_hash
    for p in path:
        if sn == 0:
            return None
        if fn % 2 == 1 or fn == sn:
            r = hashlib.sha256(b"\x01" + p + r).digest()
            if fn % 2 == 0:
                while fn % 2 == 0 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = hashlib.sha256(b"\x01" + r + p).digest()
        fn >>= 1
        sn >>= 1
    return r if sn == 0 else None

label_id = sys.argv[1]
q = urllib.parse.quote(label_id, safe=":")
label = json.loads(get(H + q))
proof = json.loads(get(H + q + "/proof"))
leaf = hashlib.sha256(b"\x00" + jcs(label)).digest()
sth = proof["sth"]
root = root_from_path(proof["leafIndex"], sth["treeSize"], leaf, [bytes.fromhex(h) for h in proof["inclusionProof"]])
ok = root is not None and root.hex() == sth["rootHash"]
print(json.dumps({"label": label_id, "leafIndex": proof["leafIndex"], "treeSize": sth["treeSize"],
                  "rootHash": sth["rootHash"], "included": ok}, indent=1))
sys.exit(0 if ok else 1)
