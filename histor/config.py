"""Operator configuration. Secrets stay in env; nothing is baked into the image."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    public_base: str
    data_dir: Path
    database_url: str
    profile: str
    operator_token: str
    registry_url: str
    crawl_interval_s: int
    crawl_on_start: bool
    crawl_workers: int
    crawl_per_host: int
    crawl_timeout_s: int
    crawl_limit: int
    crawl_batch: int
    opt_out: str
    scanner_dir: Path
    node_bin: str
    landing_dir: Path
    pqc: bool
    trusted_proxies: tuple[str, ...]
    check_rate_per_min: int
    check_scan_rate_per_hour: int
    allow_private_targets: bool

    @property
    def is_prod(self) -> bool:
        return self.profile == "prod"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "histor.sqlite3"

    @property
    def issuer_key_path(self) -> Path:
        return self.data_dir / "issuer.key"

    @property
    def opt_out_path(self) -> Path:
        return self.data_dir / "opt-out.txt"

    @property
    def head_marker_path(self) -> Path:
        return self.data_dir / "sth-marker.json"

    @property
    def provider_key_path(self) -> Path:
        return self.data_dir / "provider.key"


def load_settings() -> Settings:
    profile = os.environ.get("HISTOR_PROFILE", "dev").strip().lower() or "dev"
    if profile not in {"dev", "prod"}:
        raise RuntimeError(f"HISTOR_PROFILE must be dev or prod, got {profile!r}")
    public_base = os.environ.get("HISTOR_PUBLIC_BASE", "http://127.0.0.1:9490").strip().rstrip("/")
    settings = Settings(
        host=os.environ.get("HISTOR_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int("HISTOR_PORT", 9490, minimum=1),
        public_base=public_base,
        data_dir=Path(os.environ.get("HISTOR_DATA_DIR", "./data")).expanduser(),
        database_url=os.environ.get("HISTOR_DATABASE_URL", "").strip(),
        profile=profile,
        operator_token=os.environ.get("HISTOR_OPERATOR_TOKEN", "").strip(),
        registry_url=os.environ.get("HISTOR_REGISTRY_URL", "https://registry.modelcontextprotocol.io").strip().rstrip("/"),
        crawl_interval_s=_int("HISTOR_CRAWL_INTERVAL_S", 86400, minimum=0),
        crawl_on_start=_truthy("HISTOR_CRAWL_ON_START", "1"),
        crawl_workers=_int("HISTOR_CRAWL_WORKERS", 12, minimum=1),
        crawl_per_host=_int("HISTOR_CRAWL_PER_HOST", 2, minimum=1),
        crawl_timeout_s=_int("HISTOR_CRAWL_TIMEOUT_S", 20, minimum=1),
        crawl_limit=_int("HISTOR_CRAWL_LIMIT", 0, minimum=0),
        crawl_batch=_int("HISTOR_CRAWL_BATCH", 400, minimum=1),
        opt_out=os.environ.get("HISTOR_OPT_OUT", ""),
        scanner_dir=Path(os.environ.get("HISTOR_SCANNER_DIR", str(PACKAGE_ROOT / "scanner"))),
        node_bin=os.environ.get("HISTOR_NODE_BIN", "node").strip() or "node",
        landing_dir=Path(os.environ.get("HISTOR_LANDING_DIR", str(PACKAGE_ROOT / "docs" / "landing"))),
        pqc=_truthy("HISTOR_PQC", "0"),
        trusted_proxies=tuple(
            p.strip() for p in os.environ.get("HISTOR_TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if p.strip()
        ),
        check_rate_per_min=_int("HISTOR_CHECK_RATE_PER_MIN", 30, minimum=1),
        check_scan_rate_per_hour=_int("HISTOR_CHECK_SCAN_RATE_PER_HOUR", 20, minimum=0),
        allow_private_targets=_truthy("HISTOR_ALLOW_PRIVATE_TARGETS", "0"),
    )
    if settings.is_prod:
        # Fail closed: a production instance with a guessable admin surface, a cleartext public
        # base or a crawler allowed onto private addresses is worse than one that does not start.
        if len(settings.operator_token) < 24:
            raise RuntimeError("HISTOR_PROFILE=prod needs HISTOR_OPERATOR_TOKEN of at least 24 characters")
        if not settings.public_base.startswith("https://"):
            raise RuntimeError("HISTOR_PROFILE=prod needs an https HISTOR_PUBLIC_BASE")
        if not settings.database_url.startswith(("postgresql://", "postgres://")):
            # SQLite is the development store. The production log publishes signed tree heads,
            # and a tree head over a file one volume mistake can replace is a promise we cannot keep.
            raise RuntimeError("HISTOR_PROFILE=prod needs a postgresql:// HISTOR_DATABASE_URL")
        if settings.allow_private_targets:
            raise RuntimeError("HISTOR_ALLOW_PRIVATE_TARGETS is refused under HISTOR_PROFILE=prod")
    return settings
