"""
migrate_add_reactions_comments.py — добавляет колонки-счётчики на videos
(like_count/dislike_count/comment_count) и создаёт таблицы video_reactions,
comments для уже существующих БД (create_all() не трогает уже
существующие таблицы, см. migrate_add_studio.py — тот же принцип).

Запуск:
    cd site && python3 -m scripts.migrate_add_reactions_comments
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app.database import engine  # noqa: E402


STATEMENTS = [
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS like_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS dislike_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS comment_count INTEGER NOT NULL DEFAULT 0",
    """
    CREATE TABLE IF NOT EXISTS video_reactions (
        video_id VARCHAR(64) NOT NULL REFERENCES videos(video_id),
        channel_id VARCHAR(64) NOT NULL REFERENCES channels(channel_id),
        value INTEGER NOT NULL,
        updated_at VARCHAR(40) NOT NULL,
        PRIMARY KEY (video_id, channel_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comments (
        id BIGSERIAL PRIMARY KEY,
        video_id VARCHAR(64) NOT NULL REFERENCES videos(video_id),
        channel_id VARCHAR(64) NOT NULL REFERENCES channels(channel_id),
        body VARCHAR(10000) NOT NULL,
        client_nonce VARCHAR(64) NOT NULL UNIQUE,
        created_at TIMESTAMP NOT NULL DEFAULT now(),
        removed BOOLEAN NOT NULL DEFAULT false,
        removed_reason TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_comments_video_id ON comments (video_id)",
]


async def run():
    async with engine.begin() as conn:
        for stmt in STATEMENTS:
            print(f"> {stmt.strip().splitlines()[0]}...")
            await conn.execute(text(stmt))
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
