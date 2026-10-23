"""HISTOR as its own process, for test_restart.py to kill with SIGKILL.

The service is the real one (settings from HISTOR_* variables, migrations, the scheduler, the API
under uvicorn); only the world is fiction: the registry and MCP servers are tests.integration.world,
and the package sandbox is FileSandbox below, which lives inside this process and dies with it.
It writes every version lookup and every package run to a log file, so the test can see what a
restart repeats.

    RUNNER_STATE=<dir>  versions.json ({"npm:p0": "1.0.0", ...}) and sandbox.log
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402

from histor.app import create_app  # noqa: E402
from histor.config import load_settings  # noqa: E402
from histor.crawler import Crawler  # noqa: E402
from histor.mcpclient import Observation  # noqa: E402
from histor.registry import harvest  # noqa: E402
from histor.service import build  # noqa: E402
from tests.test_packages import package_target  # noqa: E402

STATE = Path(os.environ["RUNNER_STATE"])
DELAY = float(os.environ.get("RUNNER_PACKAGE_DELAY", "0.5"))


class FileSandbox:
    capacity = 1

    def _log(self, line: str) -> None:
        with open(STATE / "sandbox.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _versions(self) -> dict[str, str]:
        return json.loads((STATE / "versions.json").read_text())

    def latest_version(self, registry, name):
        self._log(f"lookup {registry}:{name}")
        return self._versions().get(f"{registry}:{name}")

    def version_facts(self, registry, name, version):
        return None

    def observe(self, registry, name, version):
        version = version or self._versions().get(f"{registry}:{name}")
        time.sleep(DELAY)
        self._log(f"run {registry}:{name} {version}")
        return Observation(status="ok", pages=1, package_version=version, package_observer="4",
                           tools=[{"name": f"{name}_tool", "description": f"{name} at {version}.",
                                   "inputSchema": {"type": "object"}}],
                           behaviour={"complete": True, "startup": {}, "calls": {"tools": 1}})


def main() -> None:
    Crawler.CHECKPOINT_S = 0.2
    settings = load_settings()
    packages = [package_target(f"io.p/{key}", key) for key in sorted(json.loads((STATE / "versions.json").read_text()))]

    def targets(url):
        return harvest(url, allow_cleartext=True) + packages

    services = build(settings, serving=True, crawler_kwargs={"harvest_fn": targets, "packages": FileSandbox()})
    uvicorn.run(create_app(services), host="127.0.0.1", port=settings.port, log_level="error")


if __name__ == "__main__":
    main()
