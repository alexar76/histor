"""Offline, personal review of two local MCP observations; never executes a server.

Watchlist: {format: "histor.upgrade-watchlist/v1", title?: str, dependencies: [{id: str,
before: relative_path|null, after: relative_path|null, tools: [nonempty unique names],
workflows: [{id: str, description: str, tools: [nonempty subset of selected names]}]}]}.

Observation: {format: "histor.upgrade-observation/v1", targetId: str, status: "ok"|"failed"|
"unknown", observedAt?: RFC3339, packageVersion?: str|null, serverName?: str|null,
serverVersion?: str|null, protocol?: str|null, launch?: object|null, tools?: [MCP tools],
extrasCoverage?: "observed"|"unknown" (default "observed")}.
Successful observations require tools. Other provenance fields may be missing, but their absence
bounds comparison. Launch conditions are compared by content hash and never copied to the report:
commands, arguments and environment values can contain credentials. Inputs remain untrusted data.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from histor.diffing import schema_diff, tool_set_diff
from histor.extras import DIGESTED
from histor.toolcost import sent_form
from histor.upgrade_brief_i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, diagnostic, message

WATCHLIST_FORMAT = "histor.upgrade-watchlist/v1"
OBSERVATION_FORMAT = "histor.upgrade-observation/v1"
REPORT_FORMAT = "histor.upgrade-brief/v1"
MAX_WATCHLIST_BYTES = 256_000
MAX_SNAPSHOT_BYTES = 2_000_000
MAX_DEPTH = 48
MAX_NODES = 100_000
MAX_DEPENDENCIES = 30
MAX_SELECTED_TOOLS = 128
MAX_TOOLS = 2_000
_OBSERVATION_KEYS = {"format", "targetId", "status", "observedAt", "packageVersion", "serverName",
                     "serverVersion", "protocol", "launch", "tools", "extrasCoverage"}
_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


class InvalidInput(ValueError):
    """A malformed or out-of-bundle input; messages do not include file contents."""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput("duplicate JSON object key")
        result[key] = value
    return result


def _bounded_json(path: Path, cap: int) -> tuple[dict[str, Any], str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InvalidInput("input must be a regular JSON file")
            raw = stream.read(cap + 1)
        if len(raw) > cap:
            raise InvalidInput("JSON input exceeds size limit")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(InvalidInput("nonfinite JSON number")))
    except InvalidInput:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InvalidInput("input is not valid bounded UTF-8 JSON") from exc
    except OSError as exc:
        raise InvalidInput("input file could not be read") from exc
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            raise InvalidInput("JSON input exceeds structural limit")
        if isinstance(item, str):
            if len(item) > 200_000 or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise InvalidInput("JSON string exceeds limit or contains an unpaired surrogate")
        elif isinstance(item, dict):
            stack.extend((k, depth + 1) for k in item)
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise InvalidInput("nonfinite JSON number")
    if not isinstance(value, dict):
        raise InvalidInput("JSON document must be an object")
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(value: Any, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise InvalidInput(f"invalid {label} fields")
    return value


def _text(value: Any, label: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise InvalidInput(f"invalid {label}")
    return value


def _names(value: Any, label: str, cap: int = MAX_SELECTED_TOOLS) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= cap:
        raise InvalidInput(f"{label} must be a nonempty bounded array")
    names = [_text(v, label, 128) for v in value]
    if len(set(names)) != len(names):
        raise InvalidInput(f"duplicate {label}")
    return sorted(names)


def _snapshot_path(base: Path, value: Any) -> Path | None:
    if value is None:
        return None
    _text(value, "snapshot path", 1024)
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        raise InvalidInput("snapshot paths must stay inside the watchlist bundle")
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise InvalidInput("snapshot symlink escapes the watchlist bundle")
    return path


def _validate_watchlist(value: dict[str, Any], base: Path) -> list[dict[str, Any]]:
    _object(value, {"format", "title", "dependencies"}, {"format", "dependencies"}, "watchlist")
    if value["format"] != WATCHLIST_FORMAT:
        raise InvalidInput("unsupported watchlist format")
    if "title" in value:
        _text(value["title"], "title", 200)
    deps = value["dependencies"]
    if not isinstance(deps, list) or not 1 <= len(deps) <= MAX_DEPENDENCIES:
        raise InvalidInput("dependencies must be a nonempty bounded array")
    ids: set[str] = set()
    for dep in deps:
        _object(dep, {"id", "before", "after", "tools", "workflows"},
                {"id", "before", "after", "tools", "workflows"}, "dependency")
        name = _text(dep["id"], "dependency id")
        if name in ids:
            raise InvalidInput("duplicate dependency id")
        ids.add(name)
        dep["tools"] = _names(dep["tools"], "selected tools")
        _snapshot_path(base, dep["before"])
        _snapshot_path(base, dep["after"])
        workflows = dep["workflows"]
        if not isinstance(workflows, list) or not 1 <= len(workflows) <= 64:
            raise InvalidInput("workflows must be a nonempty bounded array")
        workflow_ids: set[str] = set()
        for flow in workflows:
            _object(flow, {"id", "description", "tools"}, {"id", "description", "tools"}, "workflow")
            fid = _text(flow["id"], "workflow id")
            _text(flow["description"], "workflow description", 2000)
            if fid in workflow_ids:
                raise InvalidInput("duplicate workflow id")
            workflow_ids.add(fid)
            flow["tools"] = _names(flow["tools"], "workflow tools")
            if not set(flow["tools"]) <= set(dep["tools"]):
                raise InvalidInput("workflow tools must be selected dependency tools")
    return sorted(deps, key=lambda d: d["id"])


def _validate_snapshot(value: dict[str, Any]) -> None:
    _object(value, _OBSERVATION_KEYS, {"format", "targetId", "status"}, "observation")
    if value["format"] != OBSERVATION_FORMAT or value["status"] not in ("ok", "failed", "unknown"):
        raise InvalidInput("unsupported observation format or status")
    if value.get("extrasCoverage", "observed") not in ("observed", "unknown"):
        raise InvalidInput("extrasCoverage must be observed or unknown")
    _text(value["targetId"], "observation target id")
    for key in ("packageVersion", "serverName", "serverVersion", "protocol"):
        if value.get(key) is not None:
            _text(value[key], key)
    if value.get("observedAt") is not None:
        _text(value["observedAt"], "observedAt", 64)
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value["observedAt"]):
                raise ValueError("RFC3339 syntax required")
            timestamp = datetime.fromisoformat(value["observedAt"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timezone missing")
        except ValueError as exc:
            raise InvalidInput("observedAt requires a timestamp with timezone") from exc
    if value.get("launch") is not None and not isinstance(value["launch"], dict):
        raise InvalidInput("launch must be an object or null")
    if "tools" not in value and value["status"] != "ok":
        return
    tools = value.get("tools")
    if not isinstance(tools, list) or len(tools) > MAX_TOOLS:
        raise InvalidInput("successful observations require a bounded full tools array")
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise InvalidInput("tool must be an object")
        name = _text(tool.get("name"), "tool name", 128)
        if name in names:
            raise InvalidInput("duplicate observed tool name")
        names.add(name)
        if "description" in tool and not isinstance(tool["description"], str):
            raise InvalidInput("tool description must be text")
        for key in ("inputSchema", "outputSchema", "annotations"):
            if key in tool and not isinstance(tool[key], dict):
                raise InvalidInput(f"{key} must be an object")
        if "inputSchema" not in tool:
            raise InvalidInput("full tool definitions require inputSchema")


def _load_snapshot(base: Path, relative: str | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path = _snapshot_path(base, relative)
    if path is None:
        return None, {"path": None, "status": "missing", "sha256": None, "extrasCoverage": "unknown"}
    if not path.exists():
        return None, {"path": relative, "status": "missing", "sha256": None, "extrasCoverage": "unknown"}
    value, digest = _bounded_json(path, MAX_SNAPSHOT_BYTES)
    _validate_snapshot(value)
    provenance = {key: value.get(key) for key in ("targetId", "status", "observedAt", "packageVersion",
                                                "serverName", "serverVersion", "protocol")}
    provenance.update(path=relative, sha256=digest, extrasCoverage=value.get("extrasCoverage", "observed"),
                      toolsSha256=_hash(value["tools"]) if "tools" in value else None,
                      launchSha256=_hash(value["launch"]) if value.get("launch") else None)
    transport = (value.get("launch") or {}).get("transport")
    if transport in ("stdio", "http", "sse", "streamable-http"):
        provenance["transport"] = transport
    return value, provenance


def _schema_types(value: Any, prefix: str = "$") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        if "type" in value:
            out[prefix] = value["type"]
        for key, child in sorted(value.items()):
            if isinstance(child, (dict, list)):
                out.update(_schema_types(child, prefix + "/" + key.replace("~", "~0").replace("/", "~1")))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            out.update(_schema_types(child, prefix + "/" + str(i)))
    return out


def _selected_change(name: str, old: dict[str, Any] | None, new: dict[str, Any] | None,
                     *, compare_extras: bool = True, lang: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "status": "unknown", "changes": [], "retest": []}
    if old is None and new is None:
        row["retest"] = [message(lang, "check_tool_missing")]
        return row
    if old is None or new is None:
        row["status"] = "added" if old is None else "removed"
        row["changes"] = [row["status"]]
        row["retest"] = [message(lang, "check_tool_added" if old is None else "check_tool_removed")]
        return row
    diff = tool_set_diff([old], [new], [name], [name])
    modified = diff["modified"]
    fields = modified[0]["fields"] if modified else {}
    if ("description" in old) != ("description" in new) and "description" not in fields:
        fields["description"] = []
        row["descriptionPresenceChanged"] = True
    row["changes"] = sorted(fields)
    if "description" in fields:
        # Small excerpts are data only, never instructions for this reducer.
        row["descriptionDiff"] = [{"operation": op, "text": text[:800], "truncated": len(text) > 800}
                                  for op, text in fields["description"][:32]]
        row["descriptionDiffTruncated"] = len(fields["description"]) > 32
        row["retest"].append(message(lang, "check_description"))
    for schema in ("inputSchema", "outputSchema"):
        if schema in fields:
            # Paths only: schema defaults, examples and vendor data may carry secrets.
            changes = schema_diff(old.get(schema), new.get(schema))
            row[schema + "Paths"] = [item["path"] for item in changes]
            row["retest"].append(message(lang, "check_input" if schema == "inputSchema" else "check_output"))
    if "inputSchema" in fields:
        a, b = old["inputSchema"], new["inputSchema"]
        ar, br = a.get("required", []), b.get("required", [])
        if isinstance(ar, list) and isinstance(br, list) and all(isinstance(x, str) for x in ar + br):
            row["requiredAdded"] = sorted(set(br) - set(ar))
            row["requiredRemoved"] = sorted(set(ar) - set(br))
        at, bt = _schema_types(a), _schema_types(b)
        row["inputTypeChangedPaths"] = sorted(p for p in set(at) | set(bt)
                                             if (p in at) != (p in bt) or _dumps(at.get(p)) != _dumps(bt.get(p)))
    old_extra = {k: v for k, v in old.items() if k not in DIGESTED}
    new_extra = {k: v for k, v in new.items() if k not in DIGESTED}
    if compare_extras and _dumps(old_extra) != _dumps(new_extra):
        row["changes"].append("extras")
        # Compare typed JSON: bool/number and absent/null changes must reach the
        # affected workflow even though Python equality can collapse those values.
        members = {key: "added" if key not in old_extra else "removed" if key not in new_extra else "changed"
                   for key in sorted((old_extra.keys() | new_extra.keys()) - {"annotations"})
                   if key not in old_extra or key not in new_extra or _dumps(old_extra[key]) != _dumps(new_extra[key])}
        annotations_before, annotations_after = old.get("annotations", {}), new.get("annotations", {})
        annotation_fields = sorted(key for key in annotations_before.keys() | annotations_after.keys()
                                   if key not in annotations_before or key not in annotations_after
                                   or _dumps(annotations_before[key]) != _dumps(annotations_after[key]))
        hints = {key: [annotations_before.get(key), annotations_after.get(key)]
                 for key in annotation_fields if key in _HINTS}
        escalation = ((annotations_before.get("readOnlyHint") is True and annotations_after.get("readOnlyHint") is not True)
                      or (annotations_after.get("destructiveHint") is True and annotations_before.get("destructiveHint") is not True))
        row["extras"] = {"members": members, "annotationsPresenceChanged": ("annotations" in old) != ("annotations" in new),
                         "annotationFieldsChanged": annotation_fields,
                         "annotationHints": {k: values for k, values in hints.items()
                                             if all(v is None or type(v) is bool for v in values)},
                         "escalation": escalation}
        row["retest"].append(message(lang, "check_extras"))
    if not compare_extras:
        row["extrasCompared"] = False
        row["retest"].append(message(lang, "check_extras_missing"))
    row["status"] = "changed" if row["changes"] else "unchanged" if compare_extras else "unknown"
    return row


def _byte_counts(tools: list[dict[str, Any]], selected: set[str]) -> dict[str, int]:
    # Reuse the model-facing projection, but count bytes. No tokenizer import or cache download.
    ordered = sorted(tools, key=lambda t: t["name"])
    forms = [sent_form(t) for t in ordered]
    chosen = [sent_form(t) for t in ordered if t["name"] in selected]
    return {"fullList": len(_dumps(forms).encode("utf-8")),
            "selectedTools": len(_dumps(chosen).encode("utf-8"))}


def _dependency(dep: dict[str, Any], base: Path, *, lang: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    old, old_source = _load_snapshot(base, dep["before"])
    new, new_source = _load_snapshot(base, dep["after"])
    compare_extras = old_source["extrasCoverage"] == new_source["extrasCoverage"] == "observed"
    row: dict[str, Any] = {"id": dep["id"], "status": "unknown", "comparability": "unknown",
                           "sources": {"before": old_source, "after": new_source},
                           "fieldCoverage": "full" if compare_extras else "core-only",
                           "limitations": [], "provenanceGaps": [], "contextChanges": [], "observedChanges": None,
                           "selectedTools": [], "workflows": [], "serializedUtf8Bytes": None}
    usable = True
    for side, observation in (("before", old), ("after", new)):
        if observation is None or observation["status"] != "ok":
            row["limitations"].append(side + ": successful full observation missing")
            usable = False
        elif observation["targetId"] != dep["id"]:
            row["limitations"].append(side + ": observation target differs from dependency")
            usable = False
    if usable:
        assert old is not None and new is not None
        if not compare_extras:
            row["limitations"].append("extras: coverage unknown; only core definitions compared")
        for key in ("protocol", "launch"):
            if not old.get(key) or not new.get(key):
                row["limitations"].append(key + ": comparison context missing")
            elif _dumps(old[key]) != _dumps(new[key]):
                row["contextChanges"].append(key)
        for side, observation in (("before", old), ("after", new)):
            for key in ("observedAt", "packageVersion", "serverVersion"):
                if not observation.get(key):
                    if key == "observedAt":
                        row["limitations"].append(side + ": observedAt missing")
                    elif key == "packageVersion" and (observation.get("launch") or {}).get("transport") == "stdio":
                        row["limitations"].append(side + ": packageVersion missing for stdio observation")
                    else:
                        row["provenanceGaps"].append(side + ": " + key + " missing; not inferred")
        if old.get("observedAt") and new.get("observedAt"):
            a = datetime.fromisoformat(old["observedAt"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(new["observedAt"].replace("Z", "+00:00"))
            if b <= a:
                row["limitations"].append("observation order is not strictly chronological")
        by_old = {t["name"]: t for t in old["tools"]}
        by_new = {t["name"]: t for t in new["tools"]}
        row["selectedTools"] = [_selected_change(name, by_old.get(name), by_new.get(name), compare_extras=compare_extras, lang=lang)
                                for name in dep["tools"]]
        compared_old, compared_new = by_old, by_new
        if not compare_extras:
            compared_old = {name: {k: v for k, v in tool.items() if k in DIGESTED} for name, tool in by_old.items()}
            compared_new = {name: {k: v for k, v in tool.items() if k in DIGESTED} for name, tool in by_new.items()}
        row["observedChanges"] = _dumps(compared_old) != _dumps(compared_new)
        for tool in row["selectedTools"]:
            if tool["status"] == "unknown" and tool["name"] not in by_old and tool["name"] not in by_new:
                row["limitations"].append("selected tool absent in both observations: " + tool["name"])
        row["toolSet"] = {"beforeCount": len(by_old), "afterCount": len(by_new),
                          "added": sorted(by_new.keys() - by_old.keys()), "removed": sorted(by_old.keys() - by_new.keys())}
        counts = {"before": _byte_counts(old["tools"], set(dep["tools"])),
                  "after": _byte_counts(new["tools"], set(dep["tools"]))}
        counts["delta"] = {k: counts["after"][k] - counts["before"][k] for k in counts["before"]}
        row["serializedUtf8Bytes"] = counts
        if row["limitations"]:
            row["comparability"] = "unknown"
        elif row["contextChanges"]:
            row["comparability"] = "bounded"
            row["status"] = "context_changed"
        else:
            row["comparability"] = "comparable"
            row["status"] = "changed" if row["observedChanges"] else "unchanged"
    else:
        row["fieldCoverage"] = "none"
        row["selectedTools"] = [{"name": name, "status": "unknown", "changes": [],
                                 "retest": [message(lang, "check_observations_missing")]}
                                for name in dep["tools"]]
    by_tool = {t["name"]: t for t in row["selectedTools"]}
    for flow in sorted(dep["workflows"], key=lambda f: f["id"]):
        affected = [name for name in flow["tools"] if by_tool[name]["status"] != "unchanged"]
        uncertain = row["comparability"] != "comparable" or any(by_tool[n]["status"] == "unknown" for n in flow["tools"])
        checklist = [{"tool": name, "check": check} for name in affected for check in by_tool[name]["retest"]]
        if uncertain:
            checklist.insert(0, {"tool": None, "check": message(lang, "check_context")})
        row["workflows"].append({**flow, "status": "unknown" if uncertain else "retest" if affected else "no_observed_change",
                                 "affectedTools": affected, "retest": checklist})
    return row


def build_report(watchlist_path: str | Path, *, lang: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Read only explicit files in the local bundle; no settings, database, LLM or network."""
    if lang not in SUPPORTED_LANGUAGES:
        raise InvalidInput("unsupported report language")
    path = Path(watchlist_path).resolve()
    watchlist, digest = _bounded_json(path, MAX_WATCHLIST_BYTES)
    deps = _validate_watchlist(watchlist, path.parent)
    rows = [_dependency(dep, path.parent, lang=lang) for dep in deps]
    return {"format": REPORT_FORMAT, "language": lang, "title": watchlist.get("title", message(lang, "default_title")),
            "watchlistSha256": digest, "analysis": "deterministic-offline", "dependencies": rows,
            "summary": {status: sum(row["status"] == status for row in rows)
                        for status in ("changed", "unchanged", "context_changed", "unknown")},
            "limits": [message(lang, key) for key in (
                "limit_definitions", "limit_safety", "limit_workflows", "limit_bytes", "limit_launch",
            )]}


def _md(value: Any) -> str:
    # Keep terminal controls and bidi overrides visible as inert text, including when a
    # Markdown report is inspected with cat/less instead of a renderer.
    raw = str(value)
    bidi = frozenset((0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)))
    text = "".join(" " if c in "\r\n\t" else f"\\u{ord(c):04x}"
                   if ord(c) < 32 or 127 <= ord(c) <= 159 or ord(c) in bidi else c for c in raw)
    text = html.escape(text, quote=True)
    text = " ".join(text.split())
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text)


def markdown(report: dict[str, Any]) -> str:
    """Human report: all externally supplied text is escaped as data, never rendered as HTML."""
    lang = report.get("language", DEFAULT_LANGUAGE)
    if lang not in SUPPORTED_LANGUAGES:
        raise InvalidInput("unsupported report language")
    period = "。" if lang == "zh" else "."
    labels = {status: message(lang, "status_" + status)
              for status in ("changed", "unchanged", "unknown", "context_changed")}
    workflow_labels = {status: message(lang, "workflow_" + status)
                       for status in ("unknown", "retest", "no_observed_change")}
    tool_labels = {status: message(lang, "tool_" + status)
                   for status in ("changed", "removed", "added", "unchanged")}
    tool_labels["unknown"] = labels["unknown"]
    priority = {"changed": 0, "context_changed": 1, "unknown": 2, "unchanged": 3}
    rows = sorted(report["dependencies"], key=lambda row: (priority[row["status"]], row["id"]))

    def result_label(row: dict[str, Any]) -> str:
        if row["status"] == "unknown" and row["observedChanges"] is True:
            return message(lang, "status_unknown_changes")
        return labels[row["status"]]

    out = ["# " + _md(report["title"]), "", message(lang, "intro"), "",
           message(lang, "summary_header"), "|---|---|---|", *[
               "| " + _md(row["id"]) + " | " + result_label(row) + " | "
               + str(sum(flow["status"] != "no_observed_change" for flow in row["workflows"])) + " |" for row in rows], ""]
    for row in rows:
        remote = row["id"].startswith(("https://", "http://"))
        out.extend(["## " + _md(row["id"]), "", "**" + result_label(row) + "**", ""])
        for side in ("before", "after"):
            src = row["sources"][side]
            package = src.get("packageVersion") or message(
                lang, "not_applicable" if remote or src.get("transport") in
                ("http", "sse", "streamable-http") else "version_unknown")
            out.append("- " + message(
                lang, "source_summary", side=message(lang, side),
                date=_md(src.get("observedAt") or message(lang, "date_unknown")),
                package=_md(package), server=_md(src.get("serverVersion") or message(lang, "version_unknown")),
                protocol=_md(src.get("protocol") or message(lang, "protocol_unknown"))))
        if row.get("fieldCoverage", "full") == "core-only":
            out.append(message(lang, "coverage_core"))
        elif row.get("fieldCoverage") == "none":
            out.append(message(lang, "coverage_none"))
        out.append("")
        for note in row["limitations"]:
            out.append("- " + message(lang, "limitation") + ": " + _md(diagnostic(lang, note)))
        for note in row["provenanceGaps"]:
            side = note.split(":", 1)[0]
            if "packageVersion missing" in note and (remote or row["sources"].get(side, {}).get("transport") in ("http", "sse", "streamable-http")):
                continue  # A remote endpoint has no observed installed package version.
            out.append("- " + message(lang, "metadata_missing") + ": " + _md(diagnostic(lang, note)))
        if row["contextChanges"]:
            out.append("- " + message(lang, "context_changed", fields=_md(", ".join(row["contextChanges"]))))
        out.append("")
        for flow in row["workflows"]:
            out.extend(["### " + _md(flow["description"]), "", workflow_labels[flow["status"]] + period, ""])
            for item in flow["retest"]:
                out.append("- [ ] " + (_md(item["tool"]) + ": " if item["tool"] else "") + _md(item["check"]))
            if not flow["retest"]:
                out.append(message(lang, "control_run"))
            out.append("")
        out.extend(["### " + message(lang, "selected_changes"), ""])
        shown = False
        for tool in row["selectedTools"]:
            if tool["status"] == "unchanged":
                continue
            shown = True
            out.extend(["**" + _md(tool["name"]) + ": " + tool_labels[tool["status"]] + "**", ""])
            if "requiredAdded" in tool:
                out.append("- " + message(lang, "required_added") + ": " + _md(", ".join(tool["requiredAdded"]) or "—"))
            if tool.get("inputTypeChangedPaths"):
                out.append("- " + message(lang, "type_changes") + ": " + _md(", ".join(tool["inputTypeChangedPaths"])))
            for member in ("inputSchema", "outputSchema"):
                if tool.get(member + "Paths"):
                    out.append("- " + member + ": " + _md(", ".join(tool[member + "Paths"])))
            for item in tool.get("descriptionDiff", []):
                if item["operation"] != "=":
                    out.append("- " + message(lang, "description_added" if item["operation"] == "+" else "description_removed")
                               + ": “" + _md(item["text"]) + (" … [" + message(lang, "truncated") + "]" if item["truncated"] else "") + "”")
            if "extras" in tool:
                hints = tool["extras"].get("annotationHints", {})
                for name, values in sorted(hints.items()):
                    value_labels = [message(lang, "not_declared") if value is None else "true" if value else "false" for value in values]
                    out.append("- " + _md(name) + ": " + " → ".join(value_labels) + period + " " + message(lang, "server_claim"))
                members = sorted(tool["extras"].get("members", {}))
                if members:
                    out.append("- " + message(lang, "other_fields") + ": " + _md(", ".join(members)) + period)
                if not hints and not members:
                    out.append("- " + message(lang, "extras_details"))
            out.append("")
        if not shown:
            out.extend([message(lang, "selected_unchanged"), ""])
        counts = row["serializedUtf8Bytes"]
        if counts:
            out.extend([message(
                lang, "byte_counts", before_full=counts["before"]["fullList"], after_full=counts["after"]["fullList"],
                before_selected=counts["before"]["selectedTools"], after_selected=counts["after"]["selectedTools"]),
                message(lang, "bytes_note"), ""])
    out.extend(["## " + message(lang, "limits_heading"), ""])
    out.extend("- " + _md(note) for note in report["limits"])
    out.extend(["", "[" + message(lang, "json_link") + "](report.json)" + period, ""])
    return "\n".join(out)
