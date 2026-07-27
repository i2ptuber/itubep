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

    # --- "Студия" (управляется владельцем канала через подписанный запрос
    # от моста, см. /api/channel/{channel_id}/studio) ---
    # site_display_name — переопределение имени ТОЛЬКО на этом сайте, не
    # трогает channel_record_json/display_name (тот приходит из подписанной
    # записи канала и синхронизируется той же записью на любом другом
    # сайте, где канал зарегистрирован). Пусто — значит показывать
    # display_name как есть.
    site_display_name: Mapped[str] = mapped_column(String(200), default="")
    site_description: Mapped[str] = mapped_column(Text, default="")
    pinned_video_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Анти-replay для studio-обновлений — независим от updated_at
    # channel_record_json (это разные подписанные документы).
    studio_updated_at: Mapped[str] = mapped_column(String(40), default="")

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
    download_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- Единый торрент на ВСЕ качества видео ---
    # Раньше у каждого качества был свой независимый .torrent (хранился на
    # VideoChunk.torrent_file/torrent_infohash) — переключение качества при
    # просмотре означало бы докачку ВТОРОГО торрента с нуля. Теперь сегменты
    # всех качеств, выбранных автором при публикации, лежат в ОДНОМ
    # multi-file торренте (см. bridge/snark/publisher.py:VideoPublisher.publish) —
    # переключение качества зрителем это только смена приоритета файлов
    # внутри уже добавленного торрента (см. bridge/snark/integration.py:
    # set_quality_priority), а не отдельная докачка. См. миграцию
    # scripts/migrate_add_unified_torrent.py для уже опубликованных видео.
    torrent_infohash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    torrent_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    torrent_file: Mapped[bytes] = mapped_column(nullable=False, default=b"")

    # --- Превью (необязательное) ---
    # BLOB, не файл на диске — тот же выбор, что и для .torrent в
    # VideoChunk.torrent_file ниже: одна БД-транзакция на публикацию, без
    # отдельной синхронизации "файл создан, но транзакция откатилась" и
    # без отдельного volume/backup-контура только под картинки. Уже сжато
    # мостом ДО отправки (см. bridge/snark/thumbnail.py) под лимит
    # MAX_THUMBNAIL_BYTES (main.py) — сайт при приёме заново проверяет и
    # размер, и что это действительно валидное изображение (см.
    # publish_video), не доверяя мосту слепо. NULL — превью нет, это
    # штатный случай (пользователь мог не выбрать картинку), а не ошибка.
    thumbnail: Mapped[bytes | None] = mapped_column(nullable=True)
    thumbnail_content_type: Mapped[str] = mapped_column(String(40), default="")

    # --- Модерация (держателем сайта) ---
    # Аналогично: soft-delete, не физическое удаление строки — video_id это
    # sha256 от манифеста, при жёстком удалении можно было бы попытаться
    # переопубликовать байт-в-байт то же самое (publish_video проверяет
    # "video_id уже существует" именно по наличию строки в этой таблице).
    removed: Mapped[bool] = mapped_column(default=False)
    removed_reason: Mapped[str] = mapped_column(Text, default="")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- Доступ, управляется владельцем канала из "Студии" ---
    # "public" — виден в поиске, на странице канала и по прямой ссылке;
    # "unlisted" — доступен только по прямой ссылке (video_id), скрыт из
    # поиска и со страницы канала; "private" — не отдаётся сайтом вообще
    # (404 всем, включая владельца — приватные видео смотрятся локально,
    # сайт для них используется только как реестр метаданных на будущее).
    access_level: Mapped[str] = mapped_column(String(20), default="public")

    # --- Саморегуляция NSFW автором ---
    # Обязательная отметка при публикации (см. main.py:publish_video — без
    # неё сайт отклоняет манифест). В отличие от access_level это НЕ
    # секретность/видимость по прямой ссылке — nsfw-видео остаются
    # доступны по прямой ссылке и владелец их всегда видит в студии,
    # независимо от значения этого поля. Влияет только на то, попадает ли
    # видео в "открытые" списки (поиск/главная/страница канала) — см.
    # _show_nsfw() в main.py и связанные SQL-запросы. Хранится как
    # мутируемая site-side колонка (как и access_level), а не жёстко
    # зашитое в неизменный manifest_json поле — автор мог ошибиться при
    # публикации и должен иметь возможность поправить отметку позже через
    # студию (см. update_studio_video), не переопубликовывая видео заново.
    nsfw: Mapped[bool] = mapped_column(default=False)

    # --- Денормализованные счётчики (см. VideoReaction/Comment ниже) ---
    # Держим прямо на Video, чтобы страница видео читалась одним запросом
    # (COUNT(*) по video_reactions/comments при каждом показе страницы был
    # бы лишней нагрузкой на таблицы, которые как раз и растут больше
    # всего). Обновляются в той же транзакции, что и запись реакции/
    # комментария — см. main.py:react_to_video/post_comment.
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    dislike_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)

    channel: Mapped["Channel"] = relationship(back_populates="videos")
    chunks: Mapped[list["VideoChunk"]] = relationship(back_populates="video")


class VideoChunk(Base):
    """
    Метаданные ОДНОГО качества внутри единого торрента видео (см.
    Video.torrent_infohash/torrent_name/torrent_file выше) — своего
    .torrent/infohash у качества больше нет, вместо этого — диапазон
    файлов [file_start_index, file_start_index+file_count) внутри общего
    торрента и собственные длительности HLS-сегментов этого качества
    (нужны для генерации плейлиста, см. bridge/transport/http_server.py:playlist).
    """
    __tablename__ = "video_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.video_id"), nullable=False)
    quality: Mapped[str] = mapped_column(String(20), nullable=False)  # "360p" и т.п.
    file_start_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segment_durations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    video: Mapped["Video"] = relationship(back_populates="chunks")


class VideoReaction(Base):
    """
    Лайк/дизлайк — composite PRIMARY KEY (video_id, channel_id) гарантирует
    "1 голос на канал на видео" на уровне схемы БД, а не только проверкой в
    коде приложения: повторная попытка того же канала проголосовать за то
    же видео физически не может создать вторую строку, это UPSERT
    (переключение лайк↔дизлайк) или DELETE (отмена голоса) — см.
    main.py:react_to_video.
    """
    __tablename__ = "video_reactions"

    video_id: Mapped[str] = mapped_column(ForeignKey("videos.video_id"), primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # +1 лайк, -1 дизлайк
    # Анти-replay ЭТОЙ КОНКРЕТНОЙ пары (video_id, channel_id) — сравнивается
    # с уже сохранённым значением при UPSERT, так же как Channel.studio_updated_at.
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Comment(Base):
    """
    Комментарий. Тело ограничено на уровне схемы (см. schemas.py:
    CommentCreateRequest — 2000 символов БЕЗ учёта пробелов) — при таком
    лимите комментарий всегда маленькая строка, специально ужимать
    хранение не нужно; единственный реальный рычаг экономии места —
    отсутствие истории правок (правки не поддерживаются: комментарий либо
    есть, либо removed=True, как у Video/Channel при модерации) и
    отсутствие полнотекстового индекса под комментарии (не нужен —
    показываются только пагинированным списком под конкретным видео).

    client_nonce — часть подписанной клиентом (мостом) записи, UNIQUE:
    защита от replay-атаки (без него повторная отправка того же
    подписанного запроса создавала бы дубликат комментария на каждый
    повтор, а не отклонялась бы как уже применённая).
    """
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.video_id"), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.channel_id"), nullable=False)
    body: Mapped[str] = mapped_column(String(10000), nullable=False)
    client_nonce: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    removed: Mapped[bool] = mapped_column(default=False)
    removed_reason: Mapped[str] = mapped_column(Text, default="")


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
