"""``histor serve`` · ``histor crawl`` · ``histor migrate [up|status]`` · ``histor issuer`` · ``histor audit URL``."""

from __future__ import annotations

import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "serve"
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if command == "migrate":
        from histor.migrations import main as migrate

        return migrate(args[1:])
    if command == "audit":
        # Needs no settings, no database and no keys: an auditor is a stranger to the log.
        from histor.audit import main as audit

        return audit(args[1:])

    from histor.config import load_settings
    from histor.service import build

    settings = load_settings()
    services = build(settings, serving=command == "serve")
    try:
        if command == "serve":
            import uvicorn

            from histor.app import create_app

            uvicorn.run(create_app(services), host=settings.host, port=settings.port, proxy_headers=False,
                        log_level="warning")
            return 0
        if command == "crawl":
            running = services.store.last_run(finished_only=False)
            if running and not running.get("finished_at") and "--force" not in args:
                # The lock is per process; the service may be crawling next door.
                print(f"a crawl started at {running['started_at']} has not finished (the service may be "
                      "running it); pass --force if it is dead", file=sys.stderr)
                return 1
            print(json.dumps(services.crawler.run(), indent=2))
            return 0
        if command == "issuer":
            print(services.key.did)
            return 0
        print("usage: histor [serve|crawl|migrate [up|status]|issuer|audit URL]", file=sys.stderr)
        return 2
    finally:
        services.close()


if __name__ == "__main__":
    raise SystemExit(main())
