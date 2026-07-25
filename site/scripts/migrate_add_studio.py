"""
migrate_add_studio.py — добавляет колонки "студии" канала (site_display_name,
site_description, pinned_video_id, studio_updated_at) и доступа видео
(access_level) в уже существующие таблицы channels/videos.

См. migrate_add_moderation.py — тот же принцип: create_all() не добавляет
колонки в уже существующие таблицы, нужен явный ALTER TABLE один раз.

Запуск:
    cd site && python3 -m scripts.migrate_add_studio
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app.database import engine  # noqa: E402


STATEMENTS = [
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS site_display_name VARCHAR(200) NOT NULL DEFAULT ''",
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS site_description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS pinned_video_id VARCHAR(64) NULL",
    "ALTER TABLE channels ADD COLUMN IF NOT EXISTS studio_updated_at VARCHAR(40) NOT NULL DEFAULT ''",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS access_level VARCHAR(20) NOT NULL DEFAULT 'public'",
]


async def run():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"> {stmt}")
            await conn.execute(text(stmt))
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
