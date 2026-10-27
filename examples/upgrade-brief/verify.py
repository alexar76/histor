"""Run the fixed synthetic example in five languages and verify its documented results.

Standard library only. Runs the local report CLI, never capture or an MCP server.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

BUNDLE = Path(__file__).resolve().parent
SOURCE = BUNDLE.parents[1]
WATCHLISTS = {
    "en": "watchlist.json",
    "ru": "watchlist.ru.json",
    "es": "watchlist.es.json",
    "fr": "watchlist.fr.json",
    "zh": "watchlist.zh.json",
}
EXPECTED_LOCALE_TEXT = {
    "ru": ("Изменения выбранных инструментов",
           "Проверить замену или закрепить прежний пакет; не запускать сценарий вслепую."),
    "en": ("Selected tool changes",
           "Check a replacement or pin the previous package; do not run the workflow without review."),
    "es": ("Cambios en las herramientas seleccionadas",
           "Compruebe una alternativa o fije el paquete anterior; revise el flujo antes de ejecutarlo."),
    "fr": ("Changements des outils sélectionnés",
           "Vérifiez un remplacement ou épinglez le paquet précédent ; examinez le flux avant de l’exécuter."),
    "zh": ("所选工具的变化", "检查替代工具或固定旧版软件包；先审查流程，再执行。"),
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def without_localized_text(value: dict[str, Any]) -> dict[str, Any]:
    """Retain all analysis, input evidence and checklist associations across locales."""
    result = json.loads(json.dumps(value))
    result.pop("title", None)
    result.pop("watchlistSha256", None)
    result.pop("language", None)
    if "limits" in result:
        result["limits"] = [None for _ in result["limits"]]
    for dep in result["dependencies"]:
        for tool in dep.get("selectedTools", []):
            tool["retest"] = [None for _ in tool["retest"]]
        for flow in dep["workflows"]:
            flow.pop("description", None)
            for check in flow.get("retest", []):
                check["check"] = None
    return result


def project(report: dict[str, Any]) -> dict[str, Any]:
    """Only documented findings; prose and locale labels do not define expectations."""
    return {
        "format": report["format"],
        "analysis": report["analysis"],
        "summary": report["summary"],
        "dependencies": {
            dep["id"]: {
                **{key: dep[key] for key in (
                    "status", "comparability", "fieldCoverage", "contextChanges", "observedChanges",
                )},
                "toolSet": dep.get("toolSet"),
                "selectedTools": {
                    tool["name"]: {
                        "status": tool["status"],
                        "changes": tool["changes"],
                        "requiredAdded": tool.get("requiredAdded", []),
                        "inputSchemaPaths": tool.get("inputSchemaPaths", []),
                        "outputSchemaPaths": tool.get("outputSchemaPaths", []),
                        "inputTypeChangedPaths": tool.get("inputTypeChangedPaths", []),
                        "annotationHints": tool.get("extras", {}).get("annotationHints"),
                        "annotationEscalation": tool.get("extras", {}).get("escalation"),
                    }
                    for tool in dep["selectedTools"]
                },
                "workflows": {
                    flow["id"]: {
                        "status": flow["status"],
                        "affectedTools": flow["affectedTools"],
                        "retestCount": len(flow["retest"]),
                    }
                    for flow in dep["workflows"]
                },
            }
            for dep in report["dependencies"]
        },
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify(out: Path) -> None:
    # Generated files must remain outside the fixed fixture bundle and implementation.
    require(not out.is_relative_to(BUNDLE), "choose an output directory outside the example bundle")
    require(not out.is_relative_to(SOURCE / "histor"), "choose an output directory outside the implementation")
    expected = load(BUNDLE / "expected.json")
    inputs = [BUNDLE / name for name in WATCHLISTS.values()]
    inputs.append(BUNDLE / "watchlist.en.json")
    inputs += sorted((BUNDLE / "snapshots").glob("*.json"))
    inputs.append(BUNDLE / "expected.json")
    hashes = {path: digest(path) for path in inputs}
    require(load(BUNDLE / "watchlist.en.json") == load(BUNDLE / "watchlist.json"),
            "English alias differs from the canonical watchlist")
    baseline_watchlist = without_localized_text(load(BUNDLE / "watchlist.json"))
    baseline_report: dict[str, Any] | None = None
    english_output: dict[str, bytes] | None = None
    for language, filename in WATCHLISTS.items():
        watchlist_path = BUNDLE / filename
        watchlist = load(watchlist_path)
        require(without_localized_text(watchlist) == baseline_watchlist, f"{language}: watchlist changes more than labels")
        target = out / language
        command = [sys.executable, "-S", "-B", "-m", "histor", "upgrade-brief",
                   "--watchlist", str(watchlist_path), "--out-dir", str(target), "--lang", language]
        first: dict[str, bytes] | None = None
        for _ in range(2):
            run = subprocess.run(command, cwd=SOURCE, capture_output=True, text=True, timeout=30, check=False)
            require(run.returncode == 0, f"{language}: report command failed: {run.stderr.strip()}")
            generated = {name: (target / name).read_bytes() for name in ("report.md", "report.json")}
            if first is not None:
                require(generated == first, f"{language}: repeated output differs")
            first = generated
        report = load(target / "report.json")
        if language == "en":
            english_output = first
        require(project(report) == expected, f"{language}: findings differ from expected.json")
        require(report["language"] == language, f"{language}: report language differs")
        require(report["title"] == watchlist["title"], f"{language}: localized title lost")
        require(report["watchlistSha256"] == hashes[watchlist_path], f"{language}: watchlist hash differs")
        heading, checklist = EXPECTED_LOCALE_TEXT[language]
        text = (target / "report.md").read_text(encoding="utf-8")
        require("### " + heading in text, f"{language}: localized Markdown heading missing")
        research = next(dep for dep in report["dependencies"] if dep["id"] == "demo:research")
        archive = next(flow for flow in research["workflows"] if flow["id"] == "archive-import")
        require(archive["retest"][0]["check"] == checklist, f"{language}: localized JSON checklist differs")
        removed = next(tool for tool in research["selectedTools"] if tool["name"] == "legacy_fetch")
        require(removed["retest"] == [checklist], f"{language}: localized selected-tool checklist differs")
        if language != "ru":
            # These fixed inputs have no Cyrillic source snippets or locale labels outside ru.
            require(not re.search(r"[\u0400-\u04ff]", text), f"{language}: unexpected Russian text in Markdown")
        descriptions = {
            (dep["id"], flow["id"]): flow["description"]
            for dep in watchlist["dependencies"] for flow in dep["workflows"]
        }
        for dep in report["dependencies"]:
            for source in dep["sources"].values():
                require(source["sha256"] == hashes[BUNDLE / source["path"]], f"{language}: snapshot hash differs")
            for flow in dep["workflows"]:
                require(flow["description"] == descriptions[dep["id"], flow["id"]], f"{language}: workflow label lost")
        normalized = without_localized_text(report)
        if baseline_report is not None:
            require(normalized == baseline_report, f"{language}: language changes affect analysis")
        baseline_report = normalized
        print(f"PASS {language}: expected findings, repeatable output, localized report and labels")
    default_command = [sys.executable, "-S", "-B", "-m", "histor", "upgrade-brief",
                       "--watchlist", str(BUNDLE / WATCHLISTS["en"]), "--out-dir", str(out / "en")]
    for _ in range(2):
        run = subprocess.run(default_command, cwd=SOURCE, capture_output=True, text=True, timeout=30, check=False)
        require(run.returncode == 0, f"default: report command failed: {run.stderr.strip()}")
        generated = {name: (out / "en" / name).read_bytes() for name in ("report.md", "report.json")}
        require(generated == english_output, "omitting --lang differs from explicit --lang en")
    print("PASS default: omitting --lang gives identical English reports")
    require(all(digest(path) == value for path, value in hashes.items()), "an example input was modified")
    print("PASS: 5 languages, 12 offline CLI runs, English default, identical analysis, unchanged inputs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.out_dir.resolve())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
