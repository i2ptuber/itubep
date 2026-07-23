"""
moderate.py — локальный CLI для модерации: удаление видео, блокировка
каналов. Работает НАПРЯМУЮ с БД сайта (та же async-сессия, что и сам
FastAPI-процесс) — никакого HTTP-эндпоинта, никакого токена. Запускать
нужно на том же хосте, что и сайт (или через SSH-туннель к БД) — доступ к
модерации определяется тем, что у вас есть доступ к самой машине/БД, а не
отдельным секретом, который можно потерять/перехватить/забрутфорсить по
сети.

Soft-delete/бан, не физическое удаление строк — video_id/channel_id
детерминированно выводятся из содержимого/ключа автора, при жёстком
удалении тот же человек мог бы просто переопубликовать то же самое заново
(см. комментарии у полей removed/banned в app/models.py).

Использование:
    cd site

    # посмотреть видео (последние 50, включая уже удалённые)
    python3 -m scripts.moderate list-videos
    python3 -m scripts.moderate list-videos --query "название" --no-include-removed

    # посмотреть каналы
    python3 -m scripts.moderate list-channels
    python3 -m scripts.moderate list-channels --query "имя"

    # удалить/восстановить конкретное видео
    python3 -m scripts.moderate remove-video <video_id> --reason "причина"
    python3 -m scripts.moderate restore-video <video_id>

    # заблокировать/разблокировать канал (блокировка каскадно скрывает
    # все его текущие видео; разблокировка видео автоматически НЕ вернёт)
    python3 -m scripts.moderate ban-channel <channel_id> --reason "причина"
    python3 -m scripts.moderate unban-channel <channel_id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models import Channel, Video  # noqa: E402


async def cmd_list_videos(args) -> None:
    async with async_session() as db:
        stmt = select(Video, Channel.display_name, Channel.banned).join(
            Channel, Video.channel_id == Channel.channel_id
        )
        if not args.include_removed:
            stmt = stmt.where(Video.removed == False)  # noqa: E712
        if args.query:
            stmt = stmt.where(Video.title.ilike(f"%{args.query}%"))
        stmt = stmt.order_by(Video.published_at.desc()).limit(args.limit)

        rows = (await db.execute(stmt)).all()
        if not rows:
            print("Ничего не найдено.")
            return

        for v, channel_name, channel_banned in rows:
            status = "УДАЛЕНО" if v.removed else ("канал заблокирован" if channel_banned else "ок")
            print(f"{v.video_id}  [{status}]")
            print(f"  название:  {v.title}")
            print(f"  канал:     {channel_name} ({v.channel_id})")
            print(f"  просмотры: {v.view_count}   опубликовано: {v.published_at}")
            if v.removed and v.removed_reason:
                print(f"  причина удаления: {v.removed_reason}")
            print()


async def cmd_list_channels(args) -> None:
    async with async_session() as db:
        stmt = select(Channel)
        if not args.include_banned:
            stmt = stmt.where(Channel.banned == False)  # noqa: E712
        if args.query:
            stmt = stmt.where(Channel.display_name.ilike(f"%{args.query}%"))
        stmt = stmt.order_by(Channel.updated_at.desc()).limit(args.limit)

        channels = (await db.execute(stmt)).scalars().all()
        if not channels:
            print("Ничего не найдено.")
            return

        for c in channels:
            status = "ЗАБЛОКИРОВАН" if c.banned else "активен"
            print(f"{c.channel_id}  [{status}]")
            print(f"  название: {c.display_name}")
            if c.banned and c.banned_reason:
                print(f"  причина блокировки: {c.banned_reason}")
            print()


async def cmd_remove_video(args) -> None:
    async with async_session() as db:
        video = await db.get(Video, args.video_id)
        if video is None:
            print(f"Видео {args.video_id} не найдено.", file=sys.stderr)
            sys.exit(1)

        video.removed = True
        video.removed_reason = args.reason
        video.removed_at = datetime.utcnow()
        await db.commit()
        print(f"Видео {args.video_id} ({video.title!r}) удалено.")


async def cmd_restore_video(args) -> None:
    async with async_session() as db:
        video = await db.get(Video, args.video_id)
        if video is None:
            print(f"Видео {args.video_id} не найдено.", file=sys.stderr)
            sys.exit(1)

        video.removed = False
        video.removed_reason = ""
        video.removed_at = None
        await db.commit()
        print(f"Видео {args.video_id} ({video.title!r}) восстановлено.")


async def cmd_ban_channel(args) -> None:
    async with async_session() as db:
        channel = await db.get(Channel, args.channel_id)
        if channel is None:
            print(f"Канал {args.channel_id} не найден.", file=sys.stderr)
            sys.exit(1)

        confirm = input(
            f"Заблокировать канал {channel.display_name!r} ({args.channel_id})? "
            f"Все его текущие видео тоже будут скрыты с сайта. [y/N] "
        )
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        now = datetime.utcnow()
        channel.banned = True
        channel.banned_reason = args.reason
        channel.banned_at = now

        result = await db.execute(
            select(Video).where(Video.channel_id == args.channel_id, Video.removed == False)  # noqa: E712
        )
        videos = result.scalars().all()
        for video in videos:
            video.removed = True
            video.removed_reason = f"канал заблокирован: {args.reason}" if args.reason else "канал заблокирован"
            video.removed_at = now

        await db.commit()
        print(f"Канал {args.channel_id} заблокирован, скрыто видео: {len(videos)}.")


async def cmd_unban_channel(args) -> None:
    async with async_session() as db:
        channel = await db.get(Channel, args.channel_id)
        if channel is None:
            print(f"Канал {args.channel_id} не найден.", file=sys.stderr)
            sys.exit(1)

        channel.banned = False
        channel.banned_reason = ""
        channel.banned_at = None
        await db.commit()
        print(
            f"Канал {args.channel_id} разблокирован. "
            f"Видео, скрытые вместе с блокировкой, НЕ восстановлены автоматически — "
            f"используйте 'restore-video <video_id>' по каждому нужному видео."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Локальная модерация ITubeP (без HTTP, напрямую в БД).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-videos", help="Список видео")
    p.add_argument("--query", default="", help="Фильтр по названию (подстрока)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--include-removed", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_list_videos)

    p = sub.add_parser("list-channels", help="Список каналов")
    p.add_argument("--query", default="", help="Фильтр по названию (подстрока)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--include-banned", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=cmd_list_channels)

    p = sub.add_parser("remove-video", help="Удалить (скрыть) видео")
    p.add_argument("video_id")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_remove_video)

    p = sub.add_parser("restore-video", help="Вернуть ранее удалённое видео")
    p.add_argument("video_id")
    p.set_defaults(func=cmd_restore_video)

    p = sub.add_parser("ban-channel", help="Заблокировать канал (каскадно скрывает его видео)")
    p.add_argument("channel_id")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_ban_channel)

    p = sub.add_parser("unban-channel", help="Разблокировать канал (видео нужно восстанавливать отдельно)")
    p.add_argument("channel_id")
    p.set_defaults(func=cmd_unban_channel)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
