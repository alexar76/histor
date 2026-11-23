"""Data from strangers: JSON parsed with a depth limit, and text made safe to store.

Everything a registry entry, an MCP server or a /check caller sends is attacker-controlled.
Two kinds of input used to escape the per-target handling and abort a whole crawl:

* JSON nested thousands of levels deep, which raises ``RecursionError`` in the parser or, if it
  parses, later in the canonicalizer;
* strings holding U+0000 or an unpaired surrogate, which Postgres refuses to store (NUL) and
  UTF-8 cannot encode (surrogates) — one such server name failed the SQL insert.

The deepest tool set in the first production crawl nests 36 levels (p99.9: 24), so a limit of
128 refuses nothing real and stays far below the interpreter's recursion limit.

What a server answers to ``tools/list`` and what a /check caller sends follow
``mcp-tool-set-digest/1`` section 12 instead (``loads_cut``): every level up to 512 is read, counted
from the tools array, and an array or object deeper than that is read as an empty one of the same
kind. Nothing that deep can reach a digest (``histor.subject`` gives ``MTL-SUBJ-005``), so the
answer is the one a reader of any depth would give, and the parser never sees more than 514 levels.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

MAX_DEPTH = 128
# mcp-tool-set-digest/1 section 12: the tools array is level 1, and a JSON-RPC response and its
# result can wrap it, so nothing deeper than 514 levels of the text can be inside an accepted level.
TOOL_SET_LEVELS = 512
WRAPPER_LEVELS = 2


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


# A string token (so brackets inside strings are skipped) or a bracket.
_TOKEN = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]', re.S)
_CLOSER = {"[": "]", "{": "}"}


def cut_deep(text: str, limit: int = TOOL_SET_LEVELS + WRAPPER_LEVELS) -> str:
    """The text with every array or object nested deeper than ``limit`` (the outermost value is 1)
    replaced by an empty one of the same kind; mismatched brackets raise ``ValueError``."""
    depth = 0
    pieces: list[str] = []
    last = 0
    tokens = _TOKEN.finditer(text)
    for token in tokens:
        mark = token.group()
        if mark[0] == '"':
            continue
        if mark in _CLOSER:
            depth += 1
            if depth <= limit:
                continue
            start, closers = token.start(), [_CLOSER[mark]]
            for inner in tokens:
                found = inner.group()
                if found[0] == '"':
                    continue
                if found in _CLOSER:
                    closers.append(_CLOSER[found])
                elif closers.pop() != found:
                    raise ValueError(f"brackets do not match at {inner.start()}")
                if not closers:
                    break
            else:
                raise ValueError(f"a nested value is not closed at {start}")
            pieces += [text[last:start], "[]" if mark == "[" else "{}"]
            last = inner.end()
            depth -= 1
        else:
            depth -= 1
    return text if not pieces else "".join(pieces) + text[last:]


def loads_cut(text: str | bytes, *, object_pairs_hook: Callable[[list[tuple[str, Any]]], Any] | None = None,
              limit: int = TOOL_SET_LEVELS + WRAPPER_LEVELS) -> Any:
    """``json.loads`` of a tool list as ``mcp-tool-set-digest/1`` section 12 reads one: never more
    than ``limit`` levels, anything deeper read as an empty array or object (``cut_deep``)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    try:
        return json.loads(cut_deep(text, limit), object_pairs_hook=object_pairs_hook)
    except RecursionError as exc:      # cannot happen below the interpreter's limit; belt and braces
        raise TooDeep(f"JSON nests deeper than {limit} levels") from exc


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
