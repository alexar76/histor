"""README badge. The derivation rule is published (MTL/1 section 9.3) and is HISTOR's own claim.

    pinned YYYY-MM-DD        first observation, no continuity yet
    unchanged Nd · MM-DD     continuity passes; N days since the current set was first seen
    changed YYYY-MM-DD       the latest change; neutral amber, never red — a change is not an accusation
    not observed             listed, never read (auth, unreachable, templated URL…)
    not listed               unknown to HISTOR

Every state carries a date, and the badge links to the server page naming the issuer (section
9.1: never a bare glyph). None of the words say safe, secure, verified or trusted (section 9.2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

COLORS = {"unchanged": "#2f9e77", "pinned": "#3a7bd5", "changed": "#c98a1b", "unknown": "#6b7280"}


def _days_since(ts: str | None) -> int | None:
    if not ts:
        return None
    then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - then).days)


def badge_state(target: dict[str, Any] | None) -> tuple[str, str]:
    if target is None:
        return "not listed", "unknown"
    if not target.get("current_toolset"):
        # A names-only record (an undigestible set, or NUM-001) cannot back "unchanged": the badge
        # only ever vouches for what a digest can prove.
        return ("not digestible" if target.get("current_subject") else "not observed"), "unknown"
    last_change = target.get("unchanged_since") if (target.get("changes") or 0) > 0 else None
    if last_change and _days_since(last_change) is not None and _days_since(last_change) < 7:
        return f"changed {last_change[:10]}", "changed"
    if (target.get("ok_observations") or 0) <= 1:
        return f"pinned {target.get('first_pinned', '')[:10]}", "pinned"
    days = _days_since(target.get("unchanged_since")) or 0
    return f"unchanged {days}d · {str(target.get('last_ok') or '')[5:10]}", "unchanged"


def _width(text: str) -> int:
    # Verdana 11px average advance, the shields.io approximation; generous for digits.
    return int(len(text) * 6.4) + 12


def render(target: dict[str, Any] | None, label: str = "MCP tool defs · histor") -> str:
    message, state = badge_state(target)
    lw, mw = _width(label), _width(message)
    total = lw + mw
    color = COLORS[state]
    title = escape(f"{label}: {message}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{title}">'
        f"<title>{title}</title>"
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#1f2530"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw / 2:.1f}" y="14">{escape(label)}</text>'
        f'<text x="{lw + mw / 2:.1f}" y="14">{escape(message)}</text></g></svg>'
    )
