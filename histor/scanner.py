"""Python side of the WARDEN sidecar (``scanner/scan.mjs``).

The pattern set and the record set are described by the sidecar itself, at startup, from the
installed package — so the digest a label names is the digest of the rules that actually ran.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from awr import canonical_sri

SCAN_TIMEOUT_S = 600
BATCH = 64


class ScannerError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuleSets:
    warden_package: str
    pattern_set: dict[str, Any]
    pattern_set_digest: str
    record_set: dict[str, Any]
    record_set_digest: str

    @property
    def pattern_set_id(self) -> str:
        return self.pattern_set["id"]

    @property
    def record_set_id(self) -> str:
        return self.record_set["id"]


class Scanner:
    def __init__(self, scanner_dir: Path, node_bin: str = "node") -> None:
        self.script = Path(scanner_dir) / "scan.mjs"
        self.node_bin = node_bin
        self._rulesets: RuleSets | None = None

    def _run(self, mode: str, stdin: str = "", timeout: float = SCAN_TIMEOUT_S) -> str:
        if not self.script.is_file():
            raise ScannerError(f"scanner script missing at {self.script}")
        try:
            done = subprocess.run(
                [self.node_bin, str(self.script), mode],
                input=stdin, capture_output=True, text=True, timeout=timeout,
                cwd=str(self.script.parent), check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScannerError(f"scanner could not run: {exc}") from exc
        if done.returncode != 0:
            raise ScannerError(f"scanner exited {done.returncode}: {done.stderr.strip()[:400]}")
        return done.stdout

    def rulesets(self) -> RuleSets:
        if self._rulesets is None:
            raw = json.loads(self._run("describe"))
            self._rulesets = RuleSets(
                warden_package=raw["warden"]["package"],
                pattern_set=raw["patternSet"],
                pattern_set_digest=canonical_sri(raw["patternSet"]),
                record_set=raw["recordSet"],
                record_set_digest=canonical_sri(raw["recordSet"]),
            )
        return self._rulesets

    def scan(self, jobs: list[dict[str, Any]], timeout: float = SCAN_TIMEOUT_S) -> dict[str, dict[str, Any]]:
        """``{job id: {"patternMatches": [...], "recordMatches": [...]}}``; raises on a per-job error.

        A job the gate could not evaluate is an error, not an empty match list: an empty list is
        what a ``pass`` label is issued over, so a silent failure would sign a clean result.
        """
        results: dict[str, dict[str, Any]] = {}
        for start in range(0, len(jobs), BATCH):
            chunk = jobs[start:start + BATCH]
            out = self._run("scan", "".join(json.dumps(j) + "\n" for j in chunk), timeout=timeout)
            for line in out.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("error"):
                    raise ScannerError(f"scan of {item.get('id')} failed: {item['error']}")
                results[item["id"]] = item
            missing = [j["id"] for j in chunk if j["id"] not in results]
            if missing:
                raise ScannerError(f"scanner returned nothing for {missing[:3]}")
        return results
