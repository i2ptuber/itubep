"""
migrate_add_trackers.py — добавляет announce/announce-list в уже
опубликованные .torrent, хранящиеся в БД сайта (VideoChunk.torrent_file).

Нужен ОДИН раз после того, как включили трекеры в bridge (см.
bridge/policy/storage.py:get_trackers) — иначе новые зрители старых видео
по-прежнему будут получать .torrent без трекеров, потому что сайт отдаёт
ровно те байты, что были загружены при публикации.

Безопасно: announce/announce-list лежат ВНЕ словаря "info", поэтому
info_hash (sha1 от info-словаря) не меняется, torrent_infohash в БД
остаётся верным, семантика видео не затрагивается.

Запуск:
    cd site && python3 -m scripts.migrate_add_trackers \
        http://tracker1.example.i2p:6969/announce \
        http://tracker2.example.i2p:6969/announce
"""

from __future__ import annotations

import sys
import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import async_session  # noqa: E402
from app.models import VideoChunk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))
from snark.torrent_builder import bdecode, bencode  # noqa: E402


def add_trackers_to_torrent(torrent_bytes: bytes, trackers: list[str]) -> bytes:
    parsed, rest = bdecode(torrent_bytes)
    assert not rest, "Лишние байты после торрент-словаря — файл повреждён?"

    info = parsed["info"]
    original_info_hash = hashlib.sha1(bencode(info)).hexdigest()

    parsed["announce"] = trackers[0]
    parsed["announce-list"] = [[t] for t in trackers]

    new_bytes = bencode(parsed)

    # sanity-check: info-словарь не тронут
    reparsed, _ = bdecode(new_bytes)
    new_info_hash = hashlib.sha1(bencode(reparsed["info"])).hexdigest()
    assert new_info_hash == original_info_hash, (
        "info_hash изменился при добавлении трекеров — это баг, откатываем"
    )

    return new_bytes


async def run(trackers: list[str]) -> None:
    async with async_session() as db:
        result = await db.execute(select(VideoChunk))
        chunks = result.scalars().all()
        updated = 0
        for chunk in chunks:
            try:
                chunk.torrent_file = add_trackers_to_torrent(chunk.torrent_file, trackers)
                updated += 1
            except Exception as e:
                print(f"[!] Пропущен chunk id={chunk.id} (video_id={chunk.video_id}): {e}")
        await db.commit()
        print(f"Обновлено {updated} из {len(chunks)} торрентов.")


def main():
    if len(sys.argv) < 2:
        print("Использование: python3 -m scripts.migrate_add_trackers <tracker_url> [tracker_url...]")
        sys.exit(1)
    asyncio.run(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
