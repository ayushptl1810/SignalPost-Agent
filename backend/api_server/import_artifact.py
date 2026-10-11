from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .db import Database


def import_artifact(path: Path) -> int:
    settings = Settings.from_env()
    database = Database(settings.database_url, max_size=settings.database_pool_size)
    database.open()
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            database.upsert_envelope(json.loads(line))
            count += 1
    finally:
        database.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import SignalPost envelope JSONL into Neon/Postgres")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"Imported {import_artifact(args.path)} envelopes")


if __name__ == "__main__":
    main()

