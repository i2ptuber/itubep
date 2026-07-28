"""
migrate_add_admin_search.py — добавляет pg_trgm-индексы для быстрого
ILIKE-поиска в веб-админке модерации (admin/app.py) по названию видео и
имени канала.

Зачем: ILIKE '%подстрока%' не может использовать обычный B-tree индекс —
Postgres не знает, с какого символа начнётся совпадение, и вынужден
читать таблицу целиком (seq scan) при каждом поиске. Незаметно при
сотнях строк, но при росте каталога (десятки тысяч видео) каждый поиск в
админке будет занимать заметное время. pg_trgm разбивает строки на
триграммы (тройки символов) и строит по ним GIN-индекс — Postgres
начинает использовать его для ILIKE/similarity автоматически, без
изменений в коде запросов.

Требует прав на CREATE EXTENSION (обычно есть у владельца БД/суперюзера).
Ничего не меняет в данных — только добавляет индексы, безопасно
перезапускать.

Запуск:
    cd site && python3 -m scripts.migrate_add_admin_search
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app.database import engine  # noqa: E402


STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_videos_title_trgm "
    "ON videos USING gin (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_channels_display_name_trgm "
    "ON channels USING gin (display_name gin_trgm_ops)",
]


async def run():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"> {stmt}")
            await conn.execute(text(stmt))
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
