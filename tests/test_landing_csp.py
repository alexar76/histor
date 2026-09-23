"""The landing must run under the production CSP (script-src 'self', no 'unsafe-inline').

An import map is an inline script, so a page that relies on one loads fine on a dev server and
silently loses its 3D scene behind nginx. These checks keep every script external and every
module import a path the browser can resolve without an import map.
"""

from __future__ import annotations

import re
from pathlib import Path

LANDING = Path(__file__).resolve().parents[1] / "docs" / "landing"
# Inside the satellite, so the standalone repo (and its CI) carries it too.
NGINX = Path(__file__).resolve().parents[1] / "deploy" / "nginx" / "histor.modelmarket.dev.conf"
IMPORT = re.compile(r"""(?:^|[;}\s])(?:import|export)\b[^'";=]*?from\s*['"]([^'"]+)['"]|\bimport\(\s*['"]([^'"]+)['"]\s*\)""", re.M)
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def test_every_script_tag_is_external() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    tags = re.findall(r"<script\b[^>]*>", html)
    assert tags, "the desk needs at least its module script"
    for tag in tags:
        assert re.search(r'\ssrc="[^"]+"', tag), f"inline script is blocked by CSP: {tag}"
    assert "importmap" not in html


def test_no_inline_event_handlers() -> None:
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html)


def test_module_imports_are_relative_and_exist() -> None:
    for js in (LANDING / "assets").rglob("*.js"):
        src = COMMENT.sub("", js.read_text(encoding="utf-8"))  # JSDoc shows bare import examples
        for m in IMPORT.finditer(src):
            spec = m.group(1) or m.group(2)
            assert spec.startswith(("./", "../")), f"{js.relative_to(LANDING)} imports bare '{spec}'"
            assert (js.parent / spec).resolve().is_file(), f"{js.relative_to(LANDING)} -> {spec} missing"


def test_production_csp_forbids_inline_scripts() -> None:
    # The assertion above only matters while nginx keeps script-src strict; if someone loosens
    # it, this test says so instead of letting the two drift.
    conf = NGINX.read_text(encoding="utf-8")
    csp = re.search(r'Content-Security-Policy "([^"]+)"', conf).group(1)
    script_src = re.search(r"script-src ([^;]+)", csp).group(1).split()
    assert script_src == ["'self'"]


def test_no_asset_lives_under_a_dropped_directory_name() -> None:
    # The monorepo .gitignore and the mirror's rsync both drop every directory named build or
    # dist, wherever it sits: an asset there works in a working-tree deploy and 404s everywhere
    # the page is published from git.
    dropped = {"build", "dist", "node_modules", "__pycache__", ".venv", "venv"}
    for f in LANDING.rglob("*"):
        parts = set(f.relative_to(LANDING).parts[:-1])
        assert not parts & dropped, f"{f.relative_to(LANDING)} would be left out of git and the mirror"
