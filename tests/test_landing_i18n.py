"""The desk ships five languages, and none of them may be missing a string or claim safety.

English lives in the markup (desk.js snapshots it); JS-only English lives in JS_EN; every other
language file must carry every markup key and every JS key. The same idea as THEMIS's landing
test, adapted to one file per language.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LANDING = Path(__file__).resolve().parents[1] / "docs" / "landing"
LANGS = ("ru", "es", "fr", "zh")


def markup_keys() -> set[str]:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    return set(re.findall(r'data-i(?:-ph)?="([a-z0-9_]+)"', html))


def js_keys() -> set[str]:
    src = (LANDING / "assets" / "i18n.js").read_text(encoding="utf-8")
    block = src[src.index("export const JS_EN") : src.index("export const DICT")]
    return set(re.findall(r'^\s*"?(js_[a-z0-9_\-]+)"?\s*:', block, re.M))


def lang_file(lang: str) -> str:
    return (LANDING / "assets" / "i18n" / f"{lang}.js").read_text(encoding="utf-8")


def keys_of(src: str) -> set[str]:
    return set(re.findall(r'^\s*"?([a-z0-9_\-]+)"?\s*:', src, re.M))


def test_desk_js_only_asks_for_keys_that_exist():
    desk = (LANDING / "assets" / "desk.js").read_text(encoding="utf-8")
    asked = set(re.findall(r'\bt\("(js_[a-z0-9_\-]+)"', desk))
    missing = asked - js_keys()
    assert not missing, f"desk.js asks for keys JS_EN lacks: {sorted(missing)}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_language_has_every_key(lang):
    have = keys_of(lang_file(lang))
    need = markup_keys() | js_keys()
    assert not need - have, f"{lang} misses {sorted(need - have)}"
    assert not have - need, f"{lang} has stale keys {sorted(have - need)}"


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_survive_translation(lang):
    src = lang_file(lang)
    en = (LANDING / "assets" / "i18n.js").read_text(encoding="utf-8")
    for key in js_keys():
        m_en = re.search(rf'^\s*"?{re.escape(key)}"?\s*:\s*"(.*)",?\s*$', en, re.M)
        m_tr = re.search(rf'^\s*"?{re.escape(key)}"?\s*:\s*"(.*)",?\s*$', src, re.M)
        if not m_en or not m_tr:
            continue
        assert set(re.findall(r"\{[a-z]+\}", m_en.group(1))) == set(re.findall(r"\{[a-z]+\}", m_tr.group(1))), (lang, key)


def test_the_english_copy_never_calls_anything_safe():
    """MTL/1 section 9.2, applied to our own page: the banned words appear only where we name them."""
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", html)
    for word in ("secure", "audited", "certified", "trusted"):
        hits = [m.start() for m in re.finditer(rf"\b{word}\b", text, re.I)]
        for pos in hits:
            window = text[max(0, pos - 160): pos + 60].lower()
            assert "forbids" in window or "never" in window, f"'{word}' used as a claim near: {window!r}"
