"""
database.py — асинхронное подключение к PostgreSQL через SQLAlchemy.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = os.environ.get(
    "ITUBEP_DATABASE_URL",
    "postgresql+asyncpg://itubep:PASSWORD@127.0.0.1:5432/itubep",
)

# Размер пула соединений к PostgreSQL — БЕЗ этих настроек SQLAlchemy async
# engine молча берёт дефолт pool_size=5 + max_overflow=10 = 15 соединений
# НА ОДИН процесс uvicorn. При нескольких воркерах (см. ITUBEP_SITE_ORIGIN
# и README — "uvicorn --workers N") это умножается на N: 4 воркера с
# дефолтами уже 60 соединений, и это только один сайт — если на той же
# PostgreSQL что-то ещё, легко упереться в max_connections сервера
# (дефолт PostgreSQL — 100). Считайте так:
#   (ITUBEP_DB_POOL_SIZE + ITUBEP_DB_MAX_OVERFLOW) × число воркеров uvicorn
#     должно быть заметно МЕНЬШЕ max_connections в postgresql.conf
#     (оставляйте запас на служебные scripts/*, суперюзера, будущий рост).
DB_POOL_SIZE = int(os.environ.get("ITUBEP_DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.environ.get("ITUBEP_DB_MAX_OVERFLOW", "20"))
# Сколько секунд запрос на соединение будет ждать в очереди, если пул +
# overflow уже заняты, прежде чем упасть с TimeoutError — лучше явная
# ошибка за разумное время, чем зависший на дефолтные 30с запрос.
DB_POOL_TIMEOUT = int(os.environ.get("ITUBEP_DB_POOL_TIMEOUT", "10"))
# pool_recycle — пересоздавать соединение старше N секунд. Без этого
# долгоживущий процесс (systemd-сервис, а не разовый запуск) рано или
# поздно поймает "connection reset"/"server closed the connection
# unexpectedly" от PostgreSQL или промежуточного файрвола/NAT, закрывшего
# простаивающее соединение по таймауту. 1800с = 30 минут — с запасом
# меньше типичных idle-таймаутов.
DB_POOL_RECYCLE = int(os.environ.get("ITUBEP_DB_POOL_RECYCLE", "1800"))

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT,
    pool_recycle=DB_POOL_RECYCLE,
    # pool_pre_ping — лёгкий SELECT 1 перед выдачей соединения из пула,
    # чтобы протухшее (см. pool_recycle выше) или разорванное соединение
    # не долетало до реального запроса пользователя как 500-я ошибка —
    # SQLAlchemy тихо переоткроет его первым. Небольшая накладная плата
    # на каждый checkout ради заметно меньшего числа случайных отказов.
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_models():
    """Только для прототипа — создаёт таблицы напрямую из моделей.
    В продакшене заменить на Alembic-миграции."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
