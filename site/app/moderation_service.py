"""
moderation_service.py — общая логика модерации: поиск/список видео и
каналов, soft-delete видео, бан/разбан канала. Вынесена сюда из
scripts/moderate.py, чтобы её использовали ОБА интерфейса модерации —
CLI (scripts/moderate.py) и веб-админка (admin/app.py) — не дублируя SQL
дважды. Ничего не печатает и не спрашивает подтверждений — это дело
вызывающего кода (CLI сам спрашивает y/N, веб-морда сама рисует диалог
подтверждения).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, Video, VideoChunk, VideoReaction, Comment


@dataclass
class VideoRow:
    video: Video
    channel_name: str
    channel_banned: bool


async def list_videos(
    db: AsyncSession,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    status: str = "all",
    channel_id: str = "",
) -> list[VideoRow]:
    """status: 'all' — без фильтра по removed; 'active' — только
    неудалённые; 'removed_only' — только удалённые."""
    stmt = select(Video, Channel.display_name, Channel.banned).join(
        Channel, Video.channel_id == Channel.channel_id
    )
    if status == "active":
        stmt = stmt.where(Video.removed == False)  # noqa: E712
    elif status == "removed_only":
        stmt = stmt.where(Video.removed == True)  # noqa: E712
    if channel_id:
        stmt = stmt.where(Video.channel_id == channel_id)
    if query:
        # ILIKE по title — при большом числе видео обязательно нужен
        # pg_trgm+GIN индекс (см. scripts/migrate_add_admin_search.py),
        # иначе Postgres не сможет использовать индекс для '%...%' и будет
        # читать всю таблицу целиком при каждом поиске.
        stmt = stmt.where(Video.title.ilike(f"%{query}%"))
    stmt = stmt.order_by(Video.published_at.desc()).offset(offset).limit(limit)

    rows = (await db.execute(stmt)).all()
    return [VideoRow(video=v, channel_name=name, channel_banned=banned) for v, name, banned in rows]


async def count_videos(
    db: AsyncSession, query: str = "", status: str = "all", channel_id: str = ""
) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Video)
    if status == "active":
        stmt = stmt.where(Video.removed == False)  # noqa: E712
    elif status == "removed_only":
        stmt = stmt.where(Video.removed == True)  # noqa: E712
    if channel_id:
        stmt = stmt.where(Video.channel_id == channel_id)
    if query:
        stmt = stmt.where(Video.title.ilike(f"%{query}%"))
    return (await db.execute(stmt)).scalar_one()


async def list_channels(
    db: AsyncSession,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    status: str = "all",
) -> list[Channel]:
    """status: 'all' / 'active' (не забанены) / 'banned_only'."""
    stmt = select(Channel)
    if status == "active":
        stmt = stmt.where(Channel.banned == False)  # noqa: E712
    elif status == "banned_only":
        stmt = stmt.where(Channel.banned == True)  # noqa: E712
    if query:
        stmt = stmt.where(Channel.display_name.ilike(f"%{query}%"))
    stmt = stmt.order_by(Channel.updated_at.desc()).offset(offset).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def count_channels(db: AsyncSession, query: str = "", status: str = "all") -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Channel)
    if status == "active":
        stmt = stmt.where(Channel.banned == False)  # noqa: E712
    elif status == "banned_only":
        stmt = stmt.where(Channel.banned == True)  # noqa: E712
    if query:
        stmt = stmt.where(Channel.display_name.ilike(f"%{query}%"))
    return (await db.execute(stmt)).scalar_one()


class NotFound(Exception):
    pass


async def remove_video(db: AsyncSession, video_id: str, reason: str = "") -> Video:
    video = await db.get(Video, video_id)
    if video is None:
        raise NotFound(f"Видео {video_id} не найдено.")
    video.removed = True
    video.removed_reason = reason
    video.removed_at = datetime.utcnow()
    await db.commit()
    return video


async def restore_video(db: AsyncSession, video_id: str) -> Video:
    video = await db.get(Video, video_id)
    if video is None:
        raise NotFound(f"Видео {video_id} не найдено.")
    video.removed = False
    video.removed_reason = ""
    video.removed_at = None
    await db.commit()
    return video


async def get_video_or_404(db: AsyncSession, video_id: str) -> Video:
    video = await db.get(Video, video_id)
    if video is None:
        raise NotFound(f"Видео {video_id} не найдено.")
    return video


async def purge_video(db: AsyncSession, video_id: str) -> str:
    """Вычищает ВЕСЬ тяжёлый/приватный контент видео (торрент-блоб,
    превью, манифест, описание) и все зависимые строки (чанки, реакции,
    комментарии) — но НЕ удаляет саму строку videos.video_id.

    Почему не DELETE: video_id = sha256(manifest), и publish_video()
    проверяет "уже существует" именно по наличию строки в таблице (см.
    комментарий в app/models.py у Video.removed). Физическое удаление
    строки открыло бы автору возможность переопубликовать байт-в-байт
    то же самое видео под тем же video_id. Поэтому строка остаётся
    навсегда с removed=True, но занимает уже считанные байты."""
    video = await get_video_or_404(db, video_id)
    title = video.title

    await db.execute(delete(Comment).where(Comment.video_id == video_id))
    await db.execute(delete(VideoReaction).where(VideoReaction.video_id == video_id))
    await db.execute(delete(VideoChunk).where(VideoChunk.video_id == video_id))

    video.removed = True
    video.removed_reason = video.removed_reason or "стёрто модератором (purge)"
    video.removed_at = video.removed_at or datetime.utcnow()
    video.torrent_file = b""
    video.torrent_infohash = ""
    video.torrent_name = ""
    video.thumbnail = None
    video.thumbnail_content_type = ""
    video.manifest_json = "{}"
    video.description = ""
    video.signature = ""

    await db.commit()
    return title


async def purge_channel(db: AsyncSession, channel_id: str) -> tuple[str, int]:
    """Банит канал (навсегда) и вычищает тяжёлый контент всех его видео +
    все его комментарии/реакции (в том числе на чужих видео) — но НЕ
    удаляет ни строку channels.channel_id, ни videos.video_id.

    Почему не DELETE: channel_id = base32(sha256(pubkey)), и
    register_channel() проверяет бан именно по строке в таблице (см.
    комментарий в app/models.py у Channel.banned). Физическое удаление
    канала позволило бы владельцу того же ключа тут же зарегистрироваться
    заново тем же channel_id без флага banned — то есть снять себе бан
    самостоятельно."""
    channel = await get_channel_or_404(db, channel_id)
    name = channel.display_name

    video_ids = (
        await db.execute(select(Video.video_id).where(Video.channel_id == channel_id))
    ).scalars().all()

    for video_id in video_ids:
        await purge_video(db, video_id)

    await db.execute(delete(Comment).where(Comment.channel_id == channel_id))
    await db.execute(delete(VideoReaction).where(VideoReaction.channel_id == channel_id))

    channel.banned = True
    channel.banned_reason = channel.banned_reason or "стёрт модератором (purge)"
    channel.banned_at = channel.banned_at or datetime.utcnow()
    channel.channel_record_json = "{}"
    channel.signature = ""
    channel.site_description = ""

    await db.commit()
    return name, len(video_ids)


async def get_channel_or_404(db: AsyncSession, channel_id: str) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise NotFound(f"Канал {channel_id} не найден.")
    return channel


async def ban_channel(db: AsyncSession, channel_id: str, reason: str = "") -> tuple[Channel, int]:
    """Блокирует канал и каскадно скрывает все его текущие видео.
    Возвращает (канал, число скрытых видео). Подтверждение (y/N в CLI,
    диалог в вебе) — ответственность вызывающего кода, здесь его нет."""
    channel = await get_channel_or_404(db, channel_id)

    now = datetime.utcnow()
    channel.banned = True
    channel.banned_reason = reason
    channel.banned_at = now

    result = await db.execute(
        select(Video).where(Video.channel_id == channel_id, Video.removed == False)  # noqa: E712
    )
    videos = result.scalars().all()
    for video in videos:
        video.removed = True
        video.removed_reason = f"канал заблокирован: {reason}" if reason else "канал заблокирован"
        video.removed_at = now

    await db.commit()
    return channel, len(videos)


async def unban_channel(db: AsyncSession, channel_id: str) -> Channel:
    """Разблокирует канал. Видео, скрытые вместе с блокировкой, НЕ
    восстанавливаются автоматически — см. restore_video по каждому нужному
    видео (то же поведение, что и раньше было в CLI)."""
    channel = await get_channel_or_404(db, channel_id)
    channel.banned = False
    channel.banned_reason = ""
    channel.banned_at = None
    await db.commit()
    return channel
