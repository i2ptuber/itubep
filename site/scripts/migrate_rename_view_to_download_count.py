"""
migrate_rename_view_to_download_count.py — переименовывает videos.view_count
в videos.download_count для УЖЕ развёрнутого сайта.

Нужно один раз после обновления кода: Base.metadata.create_all (см.
app/database.py) создаёт только отсутствующие ТАБЛИЦЫ, но не переименовывает
и не добавляет колонки в уже существующих — без этой миграции старая
колонка view_count останется в БД нетронутой, а новый код будет пытаться
читать/писать несуществующую download_count и падать с ошибкой.

Заодно замечание по сути изменения: view_count раньше нигде не
инкрементировался (счётчик просмотров был не реализован), так что
переименование ничего не портит по данным — просто приводит колонку к
одноимённой с полем, которое теперь реально используется (счётчик
скачиваний .torrent, см. app/main.py:get_torrent).

Запуск:
    cd site && python3 -m scripts.migrate_rename_view_to_download_count
"""

from __future__ import annotations

import sys
import asyncio
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import engine  # noqa: E402


async def run() -> None:
    async with engine.begin() as conn:
        # information_schema-проверка — чтобы миграция была безопасно
        # перезапускаема (idempotent), если её случайно выполнят дважды.
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'videos' AND column_name IN ('view_count', 'download_count')"
        ))
        existing = {row[0] for row in result.fetchall()}

        if "download_count" in existing:
            print("download_count уже существует — миграция уже применена, ничего не делаю.")
            return
        if "view_count" not in existing:
            print("Ни view_count, ни download_count не найдены в videos — "
                  "похоже, это свежая установка (create_all уже создал колонку в правильном имени). Ничего не делаю.")
            return

        await conn.execute(text("ALTER TABLE videos RENAME COLUMN view_count TO download_count"))
        print("Готово: videos.view_count -> videos.download_count")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
