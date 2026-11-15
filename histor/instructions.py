"""The server-level ``instructions`` text an MCP server sends with ``initialize`` (or ``server/discover``).

The specification describes it as a hint a client MAY add to the system prompt, so it reaches the model with
more weight than a tool description. HISTOR records it on every observation, outside the tool-set digest
(it describes the server, not the tool list), the same way it records the extras:

* ``observations.instructions`` is NULL when the observation could not have read it (a failed read, a package
  observer that does not report it, every row from before migration 024);
* ``""`` when the server sent none (no member, not a string, or only whitespace);
* otherwise the digest of the text, with the text itself in ``blobs`` (kind ``instructions``), stored once.

The digest is ``sha256-`` + base64 of SHA-256 over the UTF-8 of the stored form: the text as sent, except that
NUL and unpaired surrogates become U+FFFD (``repaired: true`` in the blob), which no database can store as sent.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from histor.untrusted import clean_text

KIND = "instructions"
#: Longer texts keep their digest over the whole text; the blob holds the first MAX_CHARS (``truncated``).
MAX_CHARS = 262_144


def stored(value: Any) -> tuple[str, dict[str, Any] | None]:
    """(digest or "", blob body or None) for one observed value of ``instructions``."""
    if not isinstance(value, str) or not value.strip():
        return "", None
    text = clean_text(value)
    digest = "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
    body: dict[str, Any] = {"text": text[:MAX_CHARS], "length": len(text)}
    if len(text) > MAX_CHARS:
        body["truncated"] = True
    if text != value:
        body["repaired"] = True
    return digest, body
