"""
migrate_add_thumbnail.py — добавляет колонки превью (thumbnail,
thumbnail_content_type) в уже существующую таблицу videos.

См. migrate_add_studio.py — тот же принцип: create_all() не добавляет
колонки в уже существующие таблицы, нужен явный ALTER TABLE один раз.

Запуск:
    cd site && python3 -m scripts.migrate_add_thumbnail
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app.database import engine  # noqa: E402


STATEMENTS = [
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS thumbnail BYTEA NULL",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS thumbnail_content_type VARCHAR(40) NOT NULL DEFAULT ''",
]


async def run():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"> {stmt}")
            await conn.execute(text(stmt))
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
