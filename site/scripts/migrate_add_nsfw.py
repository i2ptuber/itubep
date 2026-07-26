"""
migrate_add_nsfw.py — добавляет колонку videos.nsfw (обязательная авторская
отметка NSFW-контента, самоуправление вместо модерации держателем сайта —
см. models.py:Video.nsfw и main.py:publish_video).

См. migrate_add_studio.py — тот же принцип: create_all() не добавляет
колонки в уже существующие таблицы, нужен явный ALTER TABLE один раз.

DEFAULT false — видео, опубликованные ДО этой миграции (когда поле ещё не
существовало и не могло быть заполнено), остаются видимыми как раньше, а
не внезапно скрываются из выдачи задним числом.

Запуск:
    cd site && python3 -m scripts.migrate_add_nsfw
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app.database import engine  # noqa: E402


STATEMENTS = [
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS nsfw BOOLEAN NOT NULL DEFAULT false",
]


async def run():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"> {stmt}")
            await conn.execute(text(stmt))
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
