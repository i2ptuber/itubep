"""
migrate_add_moderation.py — добавляет колонки модерации (removed/banned и
сопутствующие) в уже существующие таблицы channels/videos.

init_models() в database.py использует create_all(), который создаёт ТОЛЬКО
отсутствующие таблицы целиком — если channels/videos уже существуют (сайт
уже запускался раньше этого патча), новые колонки в models.py сами по себе
на диске не появятся. Нужен явный ALTER TABLE, один раз.

Запуск:
    cd site && python3 -m scripts.migrate_add_moderation
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app.database import engine  # noqa: E402


STATEMENTS = [
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS banned BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS banned_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS banned_at TIMESTAMP NULL",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS removed BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS removed_reason TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS removed_at TIMESTAMP NULL",
]


async def run():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"> {stmt}")
            await conn.execute(text(stmt))
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
