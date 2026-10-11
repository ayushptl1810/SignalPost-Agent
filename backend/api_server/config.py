from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from . import BACKEND_ROOT

load_dotenv(BACKEND_ROOT / ".env")


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_token: str
    cors_origins: tuple[str, ...]
    database_pool_size: int
    refresh_timeout_seconds: int
    registry_snapshot: Path
    cache_path: Path | None
    nav_index: Path | None

    @classmethod
    def from_env(cls) -> "Settings":
        raw_origins = os.environ.get("SIGNALPOST_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
        origins = tuple(origin.strip() for origin in raw_origins.split(",") if origin.strip())
        raw_cache = os.environ.get("SIGNALPOST_CACHE_PATH", "")
        raw_nav = os.environ.get("SIGNALPOST_NAV_INDEX", "data/nav-employer-index.jsonl")
        database_url = os.environ.get("DATABASE_URL") or os.environ.get("NEON_CONNECTION_STRING", "")
        return cls(
            database_url=database_url.strip(),
            admin_token=os.environ.get("SIGNALPOST_ADMIN_TOKEN", ""),
            cors_origins=origins,
            database_pool_size=_int_env("SIGNALPOST_DATABASE_POOL_SIZE", 5),
            refresh_timeout_seconds=_int_env("SIGNALPOST_REFRESH_TIMEOUT_SECONDS", 1800, minimum=30),
            registry_snapshot=Path(os.environ.get("SIGNALPOST_REGISTRY_SNAPSHOT", "data/brreg-enheter.csv")),
            cache_path=Path(raw_cache) if raw_cache else None,
            nav_index=Path(raw_nav) if raw_nav and Path(raw_nav).exists() else None,
        )
