"""Data from strangers: JSON parsed with a depth limit, and text made safe to store.

Everything a registry entry, an MCP server or a /check caller sends is attacker-controlled.
Two kinds of input used to escape the per-target handling and abort a whole crawl:

* JSON nested thousands of levels deep, which raises ``RecursionError`` in the parser or, if it
  parses, later in the canonicalizer;
* strings holding U+0000 or an unpaired surrogate, which Postgres refuses to store (NUL) and
  UTF-8 cannot encode (surrogates) — one such server name failed the SQL insert.

The deepest tool set in the first production crawl nests 36 levels (p99.9: 24), so a limit of
128 refuses nothing real and stays far below the interpreter's recursion limit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

MAX_DEPTH = 128


class TooDeep(ValueError):
    """The JSON value nests deeper than we are willing to walk."""


def depth_exceeds(value: Any, limit: int = MAX_DEPTH) -> bool:
    """Iterative, so a hostile value cannot blow the stack while being measured."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return False


def loads_limited(text: str | bytes, *, object_pairs_hook: Callable[[list[tuple[str, Any]]], Any] | None = None,
                  max_depth: int = MAX_DEPTH) -> Any:
    """``json.loads`` that raises :class:`TooDeep` instead of ``RecursionError`` or a later crash."""
    try:
        value = json.loads(text, object_pairs_hook=object_pairs_hook)
    except RecursionError as exc:
        raise TooDeep(f"JSON nests deeper than {max_depth} levels") from exc
    if depth_exceeds(value, max_depth):
        raise TooDeep(f"JSON nests deeper than {max_depth} levels")
    return value


def clean_text(value: Any, limit: int | None = None) -> str:
    """A string any database will store: NUL and unpaired surrogates become U+FFFD."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    if "\x00" in value:
        value = value.replace("\x00", "�")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        value = value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return value[:limit] if limit is not None else value
