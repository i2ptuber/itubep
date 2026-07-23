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

engine = create_async_engine(DATABASE_URL, echo=False)
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
