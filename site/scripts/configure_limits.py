"""
configure_limits.py — локальный CLI для настройки rate-limit бюджетов
(app/rate_limit.py). Прямой доступ к БД, без HTTP/токенов — по тому же
принципу, что и scripts/moderate.py.

ВАЖНО: изменения применяются только после РЕСТАРТА сайта — конфигурация
загружается из БД один раз при старте (app/rate_limit.py:load_config_from_db),
не на каждый запрос, чтобы не делать лишний DB-запрос на каждый hit.

Использование:
    cd site

    # посмотреть текущие лимиты (дефолт из кода + override'ы из БД)
    python3 -m scripts.configure_limits list

    # включить лимит (или изменить существующий)
    python3 -m scripts.configure_limits set video_publish_global 500 3600

    # явно отключить лимит для bucket
    python3 -m scripts.configure_limits disable channel_register_global

    # вернуться к дефолту из кода (убрать override)
    python3 -m scripts.configure_limits reset search
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models import RateLimitConfig  # noqa: E402
from app.rate_limit import DEFAULTS  # noqa: E402


async def cmd_list(args) -> None:
    async with async_session() as db:
        result = await db.execute(select(RateLimitConfig))
        overrides = {row.bucket: (row.max_requests, row.window_seconds) for row in result.scalars().all()}

    all_buckets = sorted(set(DEFAULTS) | set(overrides))
    if not all_buckets:
        print("Нет известных bucket'ов.")
        return

    for bucket in all_buckets:
        if bucket in overrides:
            max_req, window = overrides[bucket]
            source = "override (БД)"
        elif bucket in DEFAULTS:
            max_req, window = DEFAULTS[bucket]
            source = "дефолт (код)"
        else:
            max_req, window = None, None
            source = "неизвестный bucket в БД — не описан в коде"

        limit_str = "ОТКЛЮЧЁН" if max_req is None else f"{max_req} запросов / {window}с"
        print(f"{bucket:28s} {limit_str:28s} [{source}]")


async def cmd_set(args) -> None:
    if args.bucket not in DEFAULTS:
        print(
            f"Предупреждение: {args.bucket!r} не описан в DEFAULTS (app/rate_limit.py) — "
            f"возможно, опечатка в названии bucket. Значение всё равно будет сохранено,",
            file=sys.stderr,
        )
        if input("продолжить? [y/N] ").strip().lower() != "y":
            print("Отменено.")
            return

    async with async_session() as db:
        existing = await db.get(RateLimitConfig, args.bucket)
        if existing is None:
            db.add(RateLimitConfig(bucket=args.bucket, max_requests=args.max_requests, window_seconds=args.window_seconds))
        else:
            existing.max_requests = args.max_requests
            existing.window_seconds = args.window_seconds
        await db.commit()

    print(f"{args.bucket}: {args.max_requests} запросов / {args.window_seconds}с")
    print("Изменение вступит в силу после рестарта сайта.")


async def cmd_disable(args) -> None:
    async with async_session() as db:
        existing = await db.get(RateLimitConfig, args.bucket)
        if existing is None:
            db.add(RateLimitConfig(bucket=args.bucket, max_requests=None, window_seconds=3600))
        else:
            existing.max_requests = None
        await db.commit()

    print(f"{args.bucket}: лимит отключён.")
    print("Изменение вступит в силу после рестарта сайта.")


async def cmd_reset(args) -> None:
    async with async_session() as db:
        existing = await db.get(RateLimitConfig, args.bucket)
        if existing is None:
            print(f"Для {args.bucket} и так нет override'а (уже используется дефолт из кода).")
            return
        await db.delete(existing)
        await db.commit()

    default = DEFAULTS.get(args.bucket)
    print(f"{args.bucket}: override удалён, теперь используется дефолт из кода ({default}).")
    print("Изменение вступит в силу после рестарта сайта.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Настройка rate-limit бюджетов ITubeP (без HTTP, напрямую в БД).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="Показать текущие лимиты (дефолты + override'ы)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("set", help="Задать/изменить лимит для bucket")
    p.add_argument("bucket")
    p.add_argument("max_requests", type=int)
    p.add_argument("window_seconds", type=int)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("disable", help="Явно отключить лимит для bucket")
    p.add_argument("bucket")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("reset", help="Убрать override, вернуться к дефолту из кода")
    p.add_argument("bucket")
    p.set_defaults(func=cmd_reset)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
