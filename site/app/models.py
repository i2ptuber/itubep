"""
models.py — SQLAlchemy-модели. Соответствуют модели данных из плана (1.3),
channel_id/video_id — криптографические идентификаторы, не назначаются сайтом.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Channel(Base):
    __tablename__ = "channels"

    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # base32(sha256(pubkey))
    public_key: Mapped[str] = mapped_column(String(128), nullable=False)   # ed25519 pubkey, base64
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel_record_json: Mapped[str] = mapped_column(Text, nullable=False)  # полная подписанная запись
    signature: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # --- Модерация (держателем сайта) ---
    # banned=True блокирует: обновление channel-записи (register_channel),
    # публикацию новых видео на этот channel_id, показ канала и его видео
    # где-либо на сайте. НЕ удаляем строку физически — channel_id
    # детерминированно выводится из публичного ключа автора, при жёстком
    # удалении тот же человек мог бы просто "переопубликовать" тот же канал
    # заново тем же ключом. Забанить нужно тот же самый channel_id навсегда
    # (пока явно не разбанен), а не открыть слот для повторной регистрации.
    banned: Mapped[bool] = mapped_column(default=False)
    banned_reason: Mapped[str] = mapped_column(Text, default="")
    banned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    videos: Mapped[list["Video"]] = relationship(back_populates="channel")


class Video(Base):
    __tablename__ = "videos"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(manifest)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(String(200), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    view_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- Модерация (держателем сайта) ---
    # Аналогично: soft-delete, не физическое удаление строки — video_id это
    # sha256 от манифеста, при жёстком удалении можно было бы попытаться
    # переопубликовать байт-в-байт то же самое (publish_video проверяет
    # "video_id уже существует" именно по наличию строки в этой таблице).
    removed: Mapped[bool] = mapped_column(default=False)
    removed_reason: Mapped[str] = mapped_column(Text, default="")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    channel: Mapped["Channel"] = relationship(back_populates="videos")
    chunks: Mapped[list["VideoChunk"]] = relationship(back_populates="video")


class VideoChunk(Base):
    __tablename__ = "video_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.video_id"), nullable=False)
    quality: Mapped[str] = mapped_column(String(20), nullable=False)  # "360p" и т.п.
    torrent_infohash: Mapped[str] = mapped_column(String(64), nullable=False)
    torrent_file: Mapped[bytes] = mapped_column(nullable=False)  # сам .torrent, BLOB

    video: Mapped["Video"] = relationship(back_populates="chunks")


class RateLimitConfig(Base):
    """
    Настраиваемые override'ы rate-limit бюджетов (см. app/rate_limit.py) —
    правится через scripts/configure_limits.py, не через код. Отсутствие
    строки для bucket означает "использовать дефолт из кода"
    (rate_limit.py:DEFAULTS). max_requests=NULL означает "лимит явно
    отключён" — используется, например, чтобы по умолчанию выключить
    глобальные лимиты на регистрацию каналов/публикацию видео (см.
    обсуждение — эти лимиты нужны только если реально начнётся злоупотребление).
    """
    __tablename__ = "rate_limit_config"

    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    max_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
