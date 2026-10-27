"""Locale affects report prose while evidence and upgrade decisions stay stable."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from histor.upgrade_brief import InvalidInput, build_report, markdown
from histor.upgrade_brief_cli import main
from tests.test_upgrade_brief import observation, tool, write_bundle

LANGUAGES = ("ru", "en", "es", "fr", "zh")
EXAMPLE = Path(__file__).resolve().parents[1] / "examples/upgrade-brief/watchlist.json"
REMOVED_CHECK = {
    "ru": "Проверить замену или закрепить прежний пакет; не запускать сценарий вслепую.",
    "en": "Check a replacement or pin the previous package; do not run the workflow without review.",
    "es": "Compruebe una alternativa o fije el paquete anterior; revise el flujo antes de ejecutarlo.",
    "fr": "Vérifiez un remplacement ou épinglez le paquet précédent ; examinez le flux avant de l’exécuter.",
    "zh": "检查替代工具或固定旧版软件包；先审查流程，再执行。",
}
FIRST_LIMIT = {
    "ru": "Сравниваются наблюдавшиеся определения, не фактическое поведение сервера.",
    "en": "Only observed definitions are compared, not the server’s runtime behavior.",
    "es": "Solo se comparan las definiciones observadas, no el comportamiento del servidor en ejecución.",
    "fr": "Seules les définitions observées sont comparées, pas le comportement du serveur à l’exécution.",
    "zh": "仅比较观测到的定义，不验证服务器的运行时行为。",
}


def generated_prose(report):
    yield from report["limits"]
    for dependency in report["dependencies"]:
        for selected in dependency["selectedTools"]:
            yield from selected["retest"]
        for workflow in dependency["workflows"]:
            yield from (item["check"] for item in workflow["retest"])


def decisions(report):
    result = copy.deepcopy(report)
    result.pop("language", None)
    result.pop("limits")
    for dependency in result["dependencies"]:
        for selected in dependency["selectedTools"]:
            selected["retest"] = [None] * len(selected["retest"])
        for workflow in dependency["workflows"]:
            for item in workflow["retest"]:
                item["check"] = None
    return result


def english_bundle(tmp_path, **kwargs):
    path = write_bundle(tmp_path, **kwargs)
    watchlist = json.loads(path.read_text())
    watchlist["title"] = "Our selected workflows"
    for dependency in watchlist["dependencies"]:
        for workflow in dependency["workflows"]:
            workflow["description"] = "Read records"
    path.write_text(json.dumps(watchlist), encoding="utf-8")
    return path


@pytest.mark.parametrize("lang", LANGUAGES)
def test_example_localizes_json_and_markdown_checklists(lang):
    report = build_report(EXAMPLE, lang=lang)
    assert report["language"] == lang
    assert report["limits"][0] == FIRST_LIMIT[lang]
    research = next(row for row in report["dependencies"] if row["id"] == "demo:research")
    removed = next(row for row in research["selectedTools"] if row["name"] == "legacy_fetch")
    archive = next(row for row in research["workflows"] if row["id"] == "archive-import")
    assert removed["retest"] == [REMOVED_CHECK[lang]]
    assert archive["retest"] == [{"tool": "legacy_fetch", "check": REMOVED_CHECK[lang]}]
    text = markdown(report)
    assert FIRST_LIMIT[lang].rstrip(".。") in text
    assert REMOVED_CHECK[lang].rstrip(".。") in text
    if lang != "ru":
        assert not any(re.search("[А-Яа-яЁё]", value) for value in generated_prose(report))
    assert decisions(report) == decisions(build_report(EXAMPLE, lang="ru"))


def test_omitting_language_uses_english_default_and_legacy_renderer():
    default = build_report(EXAMPLE)
    assert default == build_report(EXAMPLE, lang="en")
    assert default["language"] == "en"
    assert default["limits"][0] == FIRST_LIMIT["en"]
    legacy = copy.deepcopy(default)
    legacy.pop("language")
    assert markdown(legacy) == markdown(default)


def test_legacy_report_without_language_uses_english_labels_and_preserves_embedded_prose():
    legacy = build_report(EXAMPLE, lang="ru")
    legacy.pop("language")
    text = markdown(legacy)
    assert "| Dependency | Result | Workflows to check |" in text
    assert "Retest needed." in text
    assert REMOVED_CHECK["ru"].rstrip(".") in text
    assert legacy["limits"][0] == FIRST_LIMIT["ru"]


@pytest.mark.parametrize("environment_locale", ["ru_RU.UTF-8", "zh_CN.UTF-8"])
def test_omitted_cli_language_is_english_regardless_of_environment(tmp_path, monkeypatch, capsys, environment_locale):
    for name in ("LANG", "LC_ALL", "LANGUAGE"):
        monkeypatch.setenv(name, environment_locale)
    path = english_bundle(tmp_path, after=observation([], observedAt="2026-10-27T10:00:00Z"))
    output = tmp_path / "report"
    assert main(["--watchlist", str(path), "--out-dir", str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["language"] == "en"
    assert report["limits"][0] == FIRST_LIMIT["en"]
    assert report["dependencies"][0]["selectedTools"][0]["retest"] == [REMOVED_CHECK["en"]]
    assert report == build_report(path, lang="en")
    text = (output / "report.md").read_text()
    assert "| Dependency | Result | Workflows to check |" in text
    assert "Retest needed." in text
    assert not re.search("[А-Яа-яЁё]", text)
    capsys.readouterr()
    assert main(["--help"]) == 0
    help_text = capsys.readouterr().out
    assert "default: en" in help_text
    assert "Personal offline review" in help_text


def test_missing_title_defaults_to_english_without_language_flag(tmp_path):
    path = english_bundle(tmp_path)
    watchlist = json.loads(path.read_text())
    watchlist.pop("title")
    path.write_text(json.dumps(watchlist), encoding="utf-8")
    report = build_report(path)
    assert report["title"] == "Personal MCP upgrade brief"
    assert report["language"] == "en"


@pytest.mark.parametrize("lang", ["de", "EN", "en-US", "", None, 42, {}])
def test_invalid_api_language_is_rejected_before_input_read(tmp_path, lang):
    with pytest.raises(InvalidInput, match="lang|language"):
        build_report(tmp_path / "not-present.json", lang=lang)


@pytest.mark.parametrize("lang", ["de", "EN", "en-US", ""])
def test_invalid_cli_language_does_not_create_output(tmp_path, capsys, lang):
    output = tmp_path / "new-output"
    assert main(["--watchlist", str(EXAMPLE), "--out-dir", str(output), "--lang", lang]) == 2
    assert not output.exists()
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("lang", LANGUAGES)
def test_cli_language_controls_both_saved_reports(tmp_path, lang):
    output = tmp_path / lang
    assert main(["--watchlist", str(EXAMPLE), "--out-dir", str(output), "--lang", lang]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["language"] == lang
    assert report["limits"][0] == FIRST_LIMIT[lang]
    assert FIRST_LIMIT[lang].rstrip(".。") in (output / "report.md").read_text()


@pytest.mark.parametrize("lang", LANGUAGES)
def test_locale_preserves_source_text_and_escapes_user_labels(tmp_path, lang):
    name = "search_[原文]"
    attack = '<img src="https://source.invalid/pixel">\n![x](https://source.invalid/x)\n```html\n\x1b\u202eРусский source'
    path = write_bundle(
        tmp_path,
        before=observation([tool(name, description="")]),
        after=observation([tool(name, description=attack)], observedAt="2026-10-27T10:00:00Z"),
        selected=[name],
    )
    watchlist = json.loads(path.read_text())
    watchlist["title"] = 'Original <script>title</script> Русский'
    watchlist["dependencies"][0]["workflows"][0]["description"] = "Original ![team](https://source.invalid/team) Русский"
    path.write_text(json.dumps(watchlist), encoding="utf-8")
    report = build_report(path, lang=lang)
    dependency = report["dependencies"][0]
    assert report["title"] == watchlist["title"]
    assert dependency["workflows"][0]["description"] == watchlist["dependencies"][0]["workflows"][0]["description"]
    selected = dependency["selectedTools"][0]
    assert selected["name"] == name
    assert "".join(part["text"] for part in selected["descriptionDiff"] if part["operation"] != "-") == attack
    text = markdown(report)
    assert "<img" not in text and "<script>" not in text and "```" not in text
    assert "![x](" not in text and "![team](" not in text
    assert "\x1b" not in text and "\u202e" not in text
    assert "&lt;img" in text and "Русский source" in text
    assert "001b" in text and "202e" in text


@pytest.mark.parametrize("lang", LANGUAGES)
def test_added_and_absent_tools_and_missing_provenance_have_localized_advice(tmp_path, lang):
    path = english_bundle(
        tmp_path,
        before=observation([tool()], serverVersion=None),
        after=observation([tool(), tool("new_tool")], observedAt=None, packageVersion=None,
                          serverVersion=None, protocol=None, launch=None),
        selected=["search", "new_tool", "ghost"],
    )
    report = build_report(path, lang=lang)
    dependency = report["dependencies"][0]
    selected = {row["name"]: row for row in dependency["selectedTools"]}
    assert selected["new_tool"]["status"] == "added" and selected["new_tool"]["retest"]
    assert selected["ghost"]["status"] == "unknown" and selected["ghost"]["retest"]
    assert dependency["status"] == "unknown"
    assert dependency["provenanceGaps"] and dependency["limitations"]
    text = markdown(report)
    for diagnostic in dependency["limitations"] + dependency["provenanceGaps"]:
        assert diagnostic not in text
    if lang != "ru":
        assert not re.search("[А-Яа-яЁё]", text)
    assert decisions(report) == decisions(build_report(path, lang="ru"))


@pytest.mark.parametrize("lang", LANGUAGES)
def test_partial_extras_and_unchanged_workflow_render_in_selected_language(tmp_path, lang):
    partial = tmp_path / "partial"
    partial.mkdir()
    path = english_bundle(partial, before=observation(extrasCoverage="unknown"))
    report = build_report(path, lang=lang)
    dependency = report["dependencies"][0]
    assert dependency["fieldCoverage"] == "core-only"
    assert dependency["status"] == dependency["workflows"][0]["status"] == "unknown"
    assert dependency["selectedTools"][0]["extrasCompared"] is False
    assert dependency["selectedTools"][0]["retest"]
    unchanged = tmp_path / "unchanged"
    unchanged.mkdir()
    unchanged_report = build_report(english_bundle(unchanged), lang=lang)
    assert unchanged_report["dependencies"][0]["workflows"][0]["status"] == "no_observed_change"
    assert unchanged_report["dependencies"][0]["workflows"][0]["retest"] == []
    text = markdown(report) + markdown(unchanged_report)
    assert "coverage unknown; only core definitions compared" not in text
    if lang != "ru":
        assert not re.search("[А-Яа-яЁё]", text)


def test_missing_title_uses_five_distinct_localized_defaults(tmp_path):
    path = english_bundle(tmp_path)
    watchlist = json.loads(path.read_text())
    watchlist.pop("title")
    path.write_text(json.dumps(watchlist), encoding="utf-8")
    titles = {lang: build_report(path, lang=lang)["title"] for lang in LANGUAGES}
    assert len(set(titles.values())) == 5
    assert "MCP" in titles["en"] and "MCP" in titles["ru"]
    assert re.search("[А-Яа-яЁё]", titles["ru"])
    assert re.search("[\u4e00-\u9fff]", titles["zh"])
