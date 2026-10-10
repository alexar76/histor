"""Package names made to be mistaken for popular ones.

A typosquat does not need to change a single tool description: it only needs the user to type
``@modelcontextprotocol/server-githb`` or ``mcp-server-fetch`` under someone else's scope, and
it is usually published the day it is used — so HISTOR checks the NAME, for any package /check
is asked about, listed or not.

The popular side is the curated list's download counts (``weekly``). A name is reduced to a
skeleton — no scope, no separators, ``0``→``o`` and ``1``→``l`` — and compared with the skeletons
of popular packages: the same skeleton under another scope or with other separators, or one
edit away (a symmetric-delete index, so 40 000 names cost a dictionary lookup each). It is a
look-alike only when the popular package is downloaded at least ``RATIO`` times more than the
asked one, so two popular siblings (``mysql`` / ``mssql``) never accuse each other.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from histor.registry import CURATED_PATH

POPULAR_WEEKLY = 1000   # downloads a week to count as a name worth imitating
RATIO = 100             # the original must be this much more downloaded than the look-alike
GENERIC_SCOPES = 3      # an unscoped name this many scopes use (mcp, mcp-server, cli) is generic, not a brand
MIN_EDIT_LENGTH = 7     # one-character neighbours only for skeletons at least this long


def split_key(key: str) -> tuple[str, str, str]:
    """``npm:@scope/name`` → (``npm``, ``@scope``, ``name``)."""
    registry, _, name = key.partition(":")
    scope, _, rest = name.rpartition("/") if name.startswith("@") else ("", "", name)
    return registry, scope, rest


def skeleton(name: str) -> str:
    return re.sub(r"[-_.]", "", name.lower()).replace("0", "o").replace("1", "l")


def _deletes(s: str) -> set[str]:
    return {s[:i] + s[i + 1:] for i in range(len(s))}


class LookalikeIndex:
    def __init__(self, entries: list[tuple[str, int]]) -> None:
        self.weekly: dict[str, int] = {}
        self.by_skeleton: dict[tuple[str, str], list[str]] = {}
        self.by_delete: dict[tuple[str, str], list[str]] = {}
        self.scopes_per_name: dict[tuple[str, str], int] = {}
        for key, weekly in entries:
            self.weekly[key] = max(weekly, self.weekly.get(key, 0))
        for key in self.weekly:
            registry, _, name = split_key(key)
            self.scopes_per_name[(registry, name.lower())] = self.scopes_per_name.get((registry, name.lower()), 0) + 1
        # Generic names (mcp, mcp-server, cli) and their one-letter variants (mcp-servers) are nobody's
        # brand: a name one edit away from one is not taken as imitating anything.
        self.generic: set[tuple[str, str]] = {(reg, skeleton(name)) for (reg, name), n in self.scopes_per_name.items()
                                              if n >= GENERIC_SCOPES}
        generic_deletes: dict[tuple[str, str], list[str]] = {}
        for reg, sk in self.generic:
            for d in _deletes(sk) | {sk}:
                generic_deletes.setdefault((reg, d), []).append(sk)
        def near_generic(reg: str, sk: str) -> bool:
            return any(_one_edit(sk, g) or sk == g for d in _deletes(sk) | {sk} for g in generic_deletes.get((reg, d), []))
        self.near_generic = near_generic
        for key, weekly in self.weekly.items():
            if weekly < POPULAR_WEEKLY:
                continue
            registry, _, name = split_key(key)
            if near_generic(registry, skeleton(name)):
                continue  # a generic name is nobody's to imitate
            sk = skeleton(name)
            self.by_skeleton.setdefault((registry, sk), []).append(key)
            if len(sk) >= MIN_EDIT_LENGTH:
                for d in _deletes(sk) | {sk}:
                    self.by_delete.setdefault((registry, d), []).append(key)

    def check(self, key: str) -> dict[str, Any] | None:
        """The popular package ``key`` imitates, how, and its downloads — or None."""
        registry, scope, name = split_key(key)
        own = self.weekly.get(key, 0)
        sk = skeleton(name)
        found: list[tuple[str, str]] = []
        for other in self.by_skeleton.get((registry, sk), []):
            if other == key:
                continue
            _, other_scope, other_name = split_key(other)
            if other_name.lower() == name.lower():
                # Only a distinctive name is worth stealing; dozens of projects are honestly called mcp-server.
                if other_scope == scope:
                    continue
                found.append((other, "the same name under another scope"))
            else:
                found.append((other, "the same name with other separators or look-alike characters"))
        if not found and len(sk) >= MIN_EDIT_LENGTH and not self.near_generic(registry, sk):
            near: set[str] = set()
            for d in _deletes(sk) | {sk}:
                near.update(self.by_delete.get((registry, d), []))
            for other in near:
                if other != key and _one_edit(sk, skeleton(split_key(other)[2])):
                    found.append((other, "one character away"))
        best = max(found, key=lambda f: self.weekly[f[0]], default=None)
        if best is None or self.weekly[best[0]] < RATIO * max(own, 1):
            return None
        return {"of": best[0], "weekly": self.weekly[best[0]], "how": best[1], "ownWeekly": own}


def _one_edit(a: str, b: str) -> bool:
    """Damerau–Levenshtein distance exactly 1 (insert, delete, substitute or swap two neighbours)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


@lru_cache(maxsize=1)
def default_index(path: Path = CURATED_PATH) -> LookalikeIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = [(e["endpoint"], int(e.get("weekly") or 0)) for e in data.get("servers") or []
               if isinstance(e.get("endpoint"), str) and e["endpoint"].startswith(("npm:", "pypi:"))]
    return LookalikeIndex(entries)
