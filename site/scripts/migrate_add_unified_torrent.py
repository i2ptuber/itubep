"""
migrate_add_unified_torrent.py — переход на ЕДИНЫЙ multi-quality торрент
на видео (см. bridge/snark/publisher.py:VideoPublisher.publish).

До этой миграции у каждого качества был свой независимый .torrent,
хранившийся на VideoChunk.torrent_infohash/torrent_file. Так как до этой
фичи у видео могло быть ровно ОДНО качество (360p), у каждого Video есть
ровно один VideoChunk — и его торрент становится "единым" торрентом видео
без всякого объединения: переименовываем/переносим поля, добавляем
file_start_index=0 и file_count/segment_durations_json, взятые из
manifest_json (там они всё это время были — под qualities[0], просто не
дублировались в отдельные колонки VideoChunk).

ВАЖНО: если на момент миграции в БД уже есть видео с НЕСКОЛЬКИМИ
VideoChunk (т.е. миграция запускается на инсталляции, где фича уже
частично включена вручную/тестово) — для них автоматический перенос
НЕВОЗМОЖЕН: объединить уже опубликованные независимые торренты в один
задним числом нельзя, не изменив info_hash (а значит — не сломав уже
раздающиеся копии у существующих зрителей). Такие video_id скрипт
пропускает и выводит отдельным списком в конце — их нужно либо
удалить и переопубликовать через новый publish(), либо оставить
как есть (старый плеер-путь ниже по коду уже не поддерживается, так что
их придётся republish).

Запуск (один раз, после обновления кода):
    cd site && python3 -m scripts.migrate_add_unified_torrent
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text, select  # noqa: E402
from app.database import engine, async_session  # noqa: E402
from app.models import Video, VideoChunk  # noqa: E402


SCHEMA_STATEMENTS = [
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS torrent_infohash VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS torrent_name VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS torrent_file BYTEA NOT NULL DEFAULT ''",
    "ALTER TABLE video_chunks ADD COLUMN IF NOT EXISTS file_start_index INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE video_chunks ADD COLUMN IF NOT EXISTS file_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE video_chunks ADD COLUMN IF NOT EXISTS segment_durations_json TEXT NOT NULL DEFAULT '[]'",
    # Старые torrent_infohash/torrent_file на video_chunks были NOT NULL —
    # новая ORM-модель VideoChunk их больше не объявляет и не заполняет
    # при вставке (см. models.py), поэтому INSERT новых строк падал с
    # NotNullViolationError. Сами колонки не роняем (см. комментарий выше
    # про DROP COLUMN), просто снимаем ограничение — старые значения в них
    # остаются нетронутыми, для новых строк там будет NULL.
    "ALTER TABLE video_chunks ALTER COLUMN torrent_infohash DROP NOT NULL",
    "ALTER TABLE video_chunks ALTER COLUMN torrent_file DROP NOT NULL",
    # Старые torrent_infohash/torrent_file на video_chunks НЕ удаляем —
    # DROP COLUMN на "живой" таблице рискованнее, чем оставить мёртвые
    # колонки (см. тот же принцип в остальных migrate_add_* — они тоже
    # никогда ничего не роняют). Просто больше не читаются кодом после
    # этой миграции.
]


async def backfill() -> None:
    async with async_session() as db:
        result = await db.execute(select(Video))
        videos = result.scalars().all()

        skipped_multi_chunk: list[str] = []
        migrated = 0
        already_done = 0

        for video in videos:
            try:
                manifest = json.loads(video.manifest_json)
            except json.JSONDecodeError as e:
                print(f"[!] Пропущен video_id={video.video_id}: manifest_json повреждён ({e})")
                skipped_multi_chunk.append(video.video_id)
                continue

            if manifest.get("torrent_name"):
                # Уже мигрировано — критерий берём из САМОГО manifest_json,
                # а не из video.torrent_infohash: более ранняя версия этого
                # скрипта успевала проставить колонки Video, но не
                # переписывала manifest_json, и по колонке идемпотентность
                # ошибочно считала такие видео уже готовыми, хотя браузер
                # (читающий именно manifest_json, см. main.py:video_page)
                # всё ещё получал старую форму без torrent_name.
                already_done += 1
                continue

            chunk_result = await db.execute(
                select(VideoChunk).where(VideoChunk.video_id == video.video_id)
            )
            chunks = chunk_result.scalars().all()

            if len(chunks) != 1:
                skipped_multi_chunk.append(video.video_id)
                continue

            chunk = chunks[0]
            # torrent_infohash/torrent_file на chunk — это старые колонки
            # (см. models.py до этой фичи), их читаем через raw SQL, так
            # как текущая ORM-модель VideoChunk их уже не объявляет.
            # Читаем их всегда из video_chunks (а не из video.torrent_*,
            # даже если те уже когда-то были проставлены предыдущим
            # неполным прогоном) — video_chunks остаются нетронутым
            # первоисточником для старых видео.
            old = await db.execute(
                text(
                    "SELECT torrent_infohash, torrent_file FROM video_chunks WHERE id = :id"
                ),
                {"id": chunk.id},
            )
            old_infohash, old_torrent_file = old.one()

            try:
                old_quality = manifest["qualities"][0]
                segment_durations = old_quality["segment_durations"]
                torrent_name = old_quality.get("torrent_name") or manifest.get("torrent_name")
            except (KeyError, IndexError) as e:
                print(f"[!] Пропущен video_id={video.video_id}: не удалось прочитать manifest_json ({e})")
                skipped_multi_chunk.append(video.video_id)
                continue

            if not torrent_name:
                print(f"[!] Пропущен video_id={video.video_id}: manifest без torrent_name")
                skipped_multi_chunk.append(video.video_id)
                continue

            video.torrent_infohash = old_infohash
            video.torrent_name = torrent_name
            video.torrent_file = old_torrent_file

            chunk.file_start_index = 0
            chunk.file_count = len(segment_durations)
            chunk.segment_durations_json = json.dumps(segment_durations)

            # ВАЖНО: main.py:video_page (и другие места) читают качества/имя
            # торрента напрямую из manifest_json, а не из отдельных колонок
            # video_chunks — поэтому DB-колонки выше сами по себе НЕ решают
            # проблему для уже загруженных страниц: без переписывания самого
            # manifest_json старые видео продолжили бы отдавать браузеру
            # старую форму (torrent_name внутри qualities[0], без
            # file_start_index/file_count), и /bridge/add получал бы пустой
            # torrent_name → "missing required fields" ("mостом отклонено").
            # manifest_json нигде повторно не сверяется с подписью после
            # публикации (сама подпись проверяется только в момент
            # publish_video), поэтому переписать его тут безопасно.
            manifest["torrent_infohash"] = old_infohash
            manifest["torrent_name"] = torrent_name
            manifest["qualities"][0] = {
                "label": old_quality["label"],
                "segment_durations": segment_durations,
                "file_start_index": 0,
                "file_count": len(segment_durations),
            }
            video.manifest_json = json.dumps(manifest)

            migrated += 1

        await db.commit()

        print(f"Перенесено: {migrated}. Уже было перенесено ранее: {already_done}.")
        if skipped_multi_chunk:
            print(
                f"[!] Пропущено {len(skipped_multi_chunk)} видео (несколько качеств "
                f"или повреждённый manifest_json) — требуют переопубликования:"
            )
            for vid in skipped_multi_chunk:
                print(f"    - {vid}")


async def run() -> None:
    async with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            print(f"> {stmt}")
            await conn.execute(text(stmt))
    print("Схема обновлена, переношу данные существующих видео...")
    await backfill()
    print("Миграция завершена.")


if __name__ == "__main__":
    asyncio.run(run())
