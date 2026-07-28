"""
moderate.py — локальный CLI для модерации: удаление видео, блокировка
каналов. Работает НАПРЯМУЮ с БД сайта (та же async-сессия, что и сам
FastAPI-процесс) — никакого HTTP-эндпоинта, никакого токена. Запускать
нужно на том же хосте, что и сайт (или через SSH-туннель к БД) — доступ к
модерации определяется тем, что у вас есть доступ к самой машине/БД, а не
отдельным секретом, который можно потерять/перехватить/забрутфорсить по
сети.

Вся логика (поиск/список/удаление/бан) — в app/moderation_service.py,
её же использует веб-админка (admin/app.py), чтобы не держать два разных
SQL под одно и то же действие.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import async_session  # noqa: E402
from app import moderation_service as svc  # noqa: E402


async def cmd_list_videos(args) -> None:
    async with async_session() as db:
        rows = await svc.list_videos(
            db, query=args.query, limit=args.limit, status=args.status
        )
        if not rows:
            print("Ничего не найдено.")
            return

        for row in rows:
            v = row.video
            status = "УДАЛЕНО" if v.removed else ("канал заблокирован" if row.channel_banned else "ок")
            print(f"{v.video_id}  [{status}]")
            print(f"  название:  {v.title}")
            print(f"  канал:     {row.channel_name} ({v.channel_id})")
            print(f"  скачивания: {v.download_count}   опубликовано: {v.published_at}")
            if v.removed and v.removed_reason:
                print(f"  причина удаления: {v.removed_reason}")
            print()


async def cmd_list_channels(args) -> None:
    async with async_session() as db:
        channels = await svc.list_channels(
            db, query=args.query, limit=args.limit, status=args.status
        )
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
        try:
            video = await svc.remove_video(db, args.video_id, reason=args.reason)
        except svc.NotFound as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(f"Видео {args.video_id} ({video.title!r}) удалено.")


async def cmd_restore_video(args) -> None:
    async with async_session() as db:
        try:
            video = await svc.restore_video(db, args.video_id)
        except svc.NotFound as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(f"Видео {args.video_id} ({video.title!r}) восстановлено.")


async def cmd_ban_channel(args) -> None:
    async with async_session() as db:
        try:
            channel = await svc.get_channel_or_404(db, args.channel_id)
        except svc.NotFound as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)

        confirm = input(
            f"Заблокировать канал {channel.display_name!r} ({args.channel_id})? "
            f"Все его текущие видео тоже будут скрыты с сайта. [y/N] "
        )
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        channel, hidden_count = await svc.ban_channel(db, args.channel_id, reason=args.reason)
        print(f"Канал {args.channel_id} заблокирован, скрыто видео: {hidden_count}.")


async def cmd_unban_channel(args) -> None:
    async with async_session() as db:
        try:
            await svc.unban_channel(db, args.channel_id)
        except svc.NotFound as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(
            f"Канал {args.channel_id} разблокирован. "
            f"Видео, скрытые вместе с блокировкой, НЕ восстановлены автоматически — "
            f"используйте 'restore-video <video_id>' по каждому нужному видео."
        )


async def cmd_purge_video(args) -> None:
    async with async_session() as db:
        try:
            video = await svc.get_video_or_404(db, args.video_id)
        except svc.NotFound as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)

        confirm = input(
            f"НЕОБРАТИМО стереть содержимое видео {video.title!r} ({args.video_id})? "
            f"Торрент, превью, манифест, описание, комментарии и реакции будут удалены. "
            f"Строка video_id останется заблокированной. [y/N] "
        )
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        title = await svc.purge_video(db, args.video_id)
        print(f"Видео {args.video_id} ({title!r}) стёрто.")


async def cmd_purge_channel(args) -> None:
    async with async_session() as db:
        try:
            channel = await svc.get_channel_or_404(db, args.channel_id)
        except svc.NotFound as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)

        confirm = input(
            f"НЕОБРАТИМО стереть канал {channel.display_name!r} ({args.channel_id}) и все его видео? "
            f"Канал будет забанен, весь тяжёлый контент удалён. "
            f"Строки channel_id/video_id останутся заблокированными. [y/N] "
        )
        if confirm.strip().lower() != "y":
            print("Отменено.")
            return

        name, count = await svc.purge_channel(db, args.channel_id)
        print(f"Канал {args.channel_id} ({name!r}) стёрт, вместе с {count} видео.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Локальная модерация ITubeP (без HTTP, напрямую в БД).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list-videos", help="Список видео")
    p.add_argument("--query", default="", help="Фильтр по названию (подстрока)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument(
        "--status", choices=["all", "active", "removed_only"], default="all",
        help="all — все, active — только неудалённые, removed_only — только удалённые",
    )
    p.set_defaults(func=cmd_list_videos)

    p = sub.add_parser("list-channels", help="Список каналов")
    p.add_argument("--query", default="", help="Фильтр по имени (подстрока)")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument(
        "--status", choices=["all", "active", "banned_only"], default="all",
        help="all — все, active — только не забаненные, banned_only — только забаненные",
    )
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

    p = sub.add_parser(
        "purge-video",
        help="Стереть тяжёлый контент видео (торрент/превью/манифест/описание) НАВСЕГДА. "
        "Строка video_id остаётся заблокированной — иначе видео можно переопубликовать байт-в-байт.",
    )
    p.add_argument("video_id")
    p.set_defaults(func=cmd_purge_video)

    p = sub.add_parser(
        "purge-channel",
        help="Забанить канал и стереть весь тяжёлый контент его видео + его комментарии/реакции НАВСЕГДА. "
        "Строки channel_id/video_id остаются заблокированными — иначе канал можно перерегистрировать тем же ключом.",
    )
    p.add_argument("channel_id")
    p.set_defaults(func=cmd_purge_channel)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
