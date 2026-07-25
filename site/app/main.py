"""
main.py — точка входа FastAPI-приложения.
"""

from __future__ import annotations

import json

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import get_session, init_models, async_session
from .models import Channel, Video, VideoChunk, RateLimitConfig, VideoReaction, Comment
from .schemas import (
    ChannelRegisterRequest, ChannelResponse, SearchResultItem, SearchResponse,
    VideoListItem, ChannelVideosResponse, StudioUpdateRequest,
    StudioAuthRequest, StudioStateResponse,
    ReactionRequest, ReactionResponse, CommentCreateRequest, CommentItem, CommentsListResponse,
)
from sqlalchemy.exc import IntegrityError
from .crypto import verify_channel_record, verify_video_manifest, verify_signature
from .rate_limit import enforce
from .i18n import get_language, get_translator, get_strings, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE, COOKIE_NAME

app = FastAPI(title="ITubeP")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def on_startup():
    await init_models()  # прототип-режим — в проде заменить на Alembic

    from .rate_limit import load_config_from_db
    async with async_session() as session:
        await load_config_from_db(session)


@app.post("/api/channel/register", response_model=ChannelResponse)
async def register_channel(
    req: ChannelRegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    # Глобальный лимит — генерация нового ed25519-ключа/channel_id ничего не
    # стоит атакующему, поэтому лимит ПО channel_id тут не спасает от "наплодить
    # много каналов": один и тот же лимит легко обойти каждый раз новым ключом.
    # Единственная защита от этого конкретного сценария — общий бюджет на
    # ВЕСЬ эндпоинт разом.
    enforce("channel_register_global")
    # Плюс per-channel_id — ограничивает спам ПОВТОРНЫМИ регистрациями/
    # обновлениями одного и того же уже существующего канала.
    enforce("channel_register_id", req.channel_id)

    record = req.model_dump()

    ok, error = verify_channel_record(record)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid channel record: {error}")

    existing = await session.get(Channel, req.channel_id)

    if existing is not None:
        if existing.banned:
            raise HTTPException(
                status_code=403,
                detail="Канал заблокирован держателем сайта, обновление записи отклонено",
            )
        # Обновление существующего канала — но ТОЛЬКО если updated_at новее
        # (защита от replay/отката на старую запись атакующим)
        if req.updated_at <= json.loads(existing.channel_record_json)["updated_at"]:
            raise HTTPException(
                status_code=409,
                detail="Записанная версия channel record не новее существующей",
            )
        existing.display_name = req.display_name
        existing.channel_record_json = json.dumps(record)
        existing.signature = req.signature
        channel = existing
    else:
        channel = Channel(
            channel_id=req.channel_id,
            public_key=req.public_key,
            display_name=req.display_name,
            channel_record_json=json.dumps(record),
            signature=req.signature,
        )
        session.add(channel)

    await session.commit()
    await session.refresh(channel)

    return ChannelResponse(
        channel_id=channel.channel_id,
        display_name=channel.display_name,
        updated_at=req.updated_at,
    )


@app.get("/api/channel/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: str, session: AsyncSession = Depends(get_session)):
    channel = await session.get(Channel, channel_id)
    if channel is None or channel.banned:
        raise HTTPException(status_code=404, detail="Channel not found")
    record = json.loads(channel.channel_record_json)
    return ChannelResponse(
        channel_id=channel.channel_id,
        display_name=channel.display_name,
        updated_at=record.get("updated_at", ""),
        site_display_name=channel.site_display_name,
        site_description=channel.site_description,
        pinned_video_id=channel.pinned_video_id,
    )


@app.post("/api/channel/{channel_id}/studio")
async def studio_update(
    channel_id: str,
    req: StudioUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Обновление "студии" канала (переименование НА ЭТОМ САЙТЕ, описание,
    закреплённое видео, доступ к отдельным видео) — вызывается мостом
    (bridge/policy/authz.py:studio_update), не напрямую браузером. Запись
    подписана приватным ключом канала — проверяем подпись против
    public_key, УЖЕ известного сайту из БД (не из тела запроса), иначе
    кто угодно мог бы прислать свой ключ вместе с "обновлением" чужого
    channel_id.
    """
    if req.channel_id != channel_id:
        raise HTTPException(status_code=400, detail="channel_id mismatch between path and body")

    enforce("studio_update_id", channel_id)

    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel_id — register channel first")
    if channel.banned:
        raise HTTPException(status_code=403, detail="Канал заблокирован держателем сайта")

    if not verify_signature(req.model_dump(), channel.public_key):
        raise HTTPException(status_code=400, detail="Invalid signature")

    if req.updated_at <= channel.studio_updated_at:
        raise HTTPException(
            status_code=409,
            detail="Записанная версия студийного обновления не новее существующей",
        )

    if req.pinned_video_id:
        pinned = await session.get(Video, req.pinned_video_id)
        if pinned is None or pinned.channel_id != channel_id or pinned.removed:
            raise HTTPException(status_code=400, detail="pinned_video_id does not belong to this channel")

    if req.video_access:
        result = await session.execute(
            select(Video).where(Video.video_id.in_(list(req.video_access.keys())))
        )
        videos_by_id = {v.video_id: v for v in result.scalars().all()}
        for video_id, level in req.video_access.items():
            if level not in ("public", "unlisted", "private"):
                raise HTTPException(status_code=400, detail=f"Invalid access level: {level}")
            video = videos_by_id.get(video_id)
            if video is None or video.channel_id != channel_id:
                raise HTTPException(status_code=400, detail=f"Video {video_id} does not belong to this channel")
            video.access_level = level

    channel.site_display_name = req.site_display_name
    channel.site_description = req.site_description
    channel.pinned_video_id = req.pinned_video_id
    channel.studio_updated_at = req.updated_at

    await session.commit()

    return {"status": "ok", "channel_id": channel_id}


STUDIO_STATE_TIMESTAMP_TOLERANCE_SECONDS = 300  # окно свежести для read-запроса (не монотонный счётчик — просто анти-replay "не старше N минут")


@app.post("/api/channel/{channel_id}/studio-state", response_model=StudioStateResponse)
async def studio_state(
    channel_id: str,
    req: StudioAuthRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Читает полное состояние "студии" владельца канала — В ОТЛИЧИЕ от
    /api/channel/{id}/videos (публичный, только access_level="public"),
    здесь возвращаются ВСЕ видео канала (включая unlisted/private), потому
    что это нужно владельцу для управления доступом. Поэтому запрос должен
    быть подписан приватным ключом канала (проверяем против public_key из
    БД), а не быть просто GET по известному channel_id.
    """
    import time as _time

    if req.channel_id != channel_id:
        raise HTTPException(status_code=400, detail="channel_id mismatch between path and body")

    enforce("studio_state_id", channel_id)

    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel_id")
    if channel.banned:
        raise HTTPException(status_code=403, detail="Канал заблокирован держателем сайта")

    if not verify_signature(req.model_dump(), channel.public_key):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        ts = float(req.timestamp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid timestamp")
    if abs(_time.time() - ts) > STUDIO_STATE_TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(status_code=403, detail="Timestamp too old or too far in the future")

    result = await session.execute(
        select(Video)
        .where(Video.channel_id == channel_id, Video.removed == False)  # noqa: E712
        .order_by(Video.published_at.desc())
    )
    videos = result.scalars().all()

    return StudioStateResponse(
        channel_id=channel.channel_id,
        display_name=channel.display_name,
        site_display_name=channel.site_display_name,
        site_description=channel.site_description,
        pinned_video_id=channel.pinned_video_id,
        videos=[
            VideoListItem(
                video_id=v.video_id,
                title=v.title,
                duration_seconds=v.duration_seconds,
                download_count=v.download_count,
                published_at=v.published_at.isoformat(),
                access_level=v.access_level,
            )
            for v in videos
        ],
    )


@app.post("/api/video/{video_id}/react", response_model=ReactionResponse)
async def react_to_video(
    video_id: str,
    req: ReactionRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Лайк/дизлайк. "1 голос на канал на видео" гарантирован PRIMARY KEY
    (video_id, channel_id) в video_reactions (см. models.py) — это UPSERT
    (переключение лайк↔дизлайк) либо DELETE строки (value=0, отмена
    голоса), не INSERT новой строки поверх старой. Анти-replay сравнивает
    updated_at с уже сохранённым значением ИМЕННО ЭТОЙ пары (video_id,
    channel_id), а не с чем-то общим на канал — голоса за разные видео
    независимы друг от друга.
    """
    if req.video_id != video_id:
        raise HTTPException(status_code=400, detail="video_id mismatch between path and body")

    enforce("reaction_id", req.channel_id)

    channel = await session.get(Channel, req.channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel_id — register channel first")
    if channel.banned:
        raise HTTPException(status_code=403, detail="Канал заблокирован держателем сайта")

    video = await session.get(Video, video_id)
    if video is None or video.removed or video.access_level != "public":
        raise HTTPException(status_code=404, detail="Video not found")

    if not verify_signature(req.model_dump(), channel.public_key):
        raise HTTPException(status_code=400, detail="Invalid signature")

    existing_result = await session.execute(
        select(VideoReaction).where(
            VideoReaction.video_id == video_id, VideoReaction.channel_id == req.channel_id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None and req.updated_at <= existing.updated_at:
        raise HTTPException(status_code=409, detail="Записанный голос не новее существующего")

    old_value = existing.value if existing is not None else 0

    if req.value == 0:
        if existing is not None:
            await session.delete(existing)
    elif existing is not None:
        existing.value = req.value
        existing.updated_at = req.updated_at
    else:
        session.add(VideoReaction(
            video_id=video_id, channel_id=req.channel_id,
            value=req.value, updated_at=req.updated_at,
        ))

    # Денормализованные счётчики (см. models.py:Video.like_count) — считаем
    # дельту старого/нового значения вместо COUNT(*) по всей таблице реакций.
    if old_value == 1:
        video.like_count = max(0, video.like_count - 1)
    elif old_value == -1:
        video.dislike_count = max(0, video.dislike_count - 1)
    if req.value == 1:
        video.like_count += 1
    elif req.value == -1:
        video.dislike_count += 1

    await session.commit()

    return ReactionResponse(
        video_id=video_id,
        like_count=video.like_count,
        dislike_count=video.dislike_count,
        my_value=req.value,
    )


@app.get("/api/video/{video_id}/comments", response_model=CommentsListResponse)
async def get_comments(
    video_id: str,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    enforce("comment_read")
    limit = min(limit, 100)

    video = await session.get(Video, video_id)
    if video is None or video.removed or video.access_level != "public":
        raise HTTPException(status_code=404, detail="Video not found")

    result = await session.execute(
        select(Comment, Channel.display_name, Channel.site_display_name)
        .join(Channel, Comment.channel_id == Channel.channel_id)
        .where(Comment.video_id == video_id, Comment.removed == False)  # noqa: E712
        .order_by(Comment.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()

    return CommentsListResponse(
        video_id=video_id,
        total=video.comment_count,
        comments=[
            CommentItem(
                id=c.id,
                channel_id=c.channel_id,
                channel_display_name=site_name or display_name,
                body=c.body,
                created_at=c.created_at.isoformat(),
            )
            for c, display_name, site_name in rows
        ],
    )


@app.post("/api/video/{video_id}/comment", response_model=CommentItem)
async def post_comment(
    video_id: str,
    req: CommentCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Подписанный комментарий — тот же паттерн, что react_to_video/studio.
    Длина тела уже провалидирована схемой (CommentCreateRequest: 2000
    символов без пробелов). client_nonce UNIQUE защищает от replay: если
    подписанный запрос отправят повторно (перехват, баг клиента,
    намеренный повтор), IntegrityError на уникальном индексе — не создаём
    дубликат комментария на каждый повтор.
    """
    if req.video_id != video_id:
        raise HTTPException(status_code=400, detail="video_id mismatch between path and body")

    enforce("comment_id", req.channel_id)

    channel = await session.get(Channel, req.channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel_id — register channel first")
    if channel.banned:
        raise HTTPException(status_code=403, detail="Канал заблокирован держателем сайта")

    video = await session.get(Video, video_id)
    if video is None or video.removed or video.access_level != "public":
        raise HTTPException(status_code=404, detail="Video not found")

    if not verify_signature(req.model_dump(), channel.public_key):
        raise HTTPException(status_code=400, detail="Invalid signature")

    existing_nonce = await session.execute(
        select(Comment).where(Comment.client_nonce == req.client_nonce)
    )
    if existing_nonce.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Duplicate comment (client_nonce already used)")

    comment = Comment(
        video_id=video_id, channel_id=req.channel_id,
        body=req.body, client_nonce=req.client_nonce,
    )
    session.add(comment)
    video.comment_count += 1
    try:
        await session.commit()
    except IntegrityError:
        # Гонка с параллельным запросом с тем же client_nonce (или
        # предыдущая проверка устарела) — откатываем ЦЕЛИКОМ, включая
        # инкремент comment_count, транзакция атомарна.
        await session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate comment (client_nonce already used)")
    await session.refresh(comment)

    return CommentItem(
        id=comment.id,
        channel_id=comment.channel_id,
        channel_display_name=channel.site_display_name or channel.display_name,
        body=comment.body,
        created_at=comment.created_at.isoformat(),
    )
    
@app.post("/api/video/publish")
async def publish_video(
    manifest_json: str = Form(...),
    torrents: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
):
    try:
        manifest = json.loads(manifest_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid manifest JSON")

    channel = await session.get(Channel, manifest.get("channel_id"))
    if channel is None:
        raise HTTPException(status_code=400, detail="Unknown channel_id — register channel first")
    if channel.banned:
        raise HTTPException(status_code=403, detail="Канал заблокирован держателем сайта")

    # Регистрация канала уже глобально лимитирована выше (register_channel) —
    # поэтому здесь per-channel_id лимита в целом достаточно: чтобы спамить
    # публикациями под МНОГИМИ каналами, атакующему сначала пришлось бы
    # зарегистрировать их все, а это уже упирается в channel_register_global.
    # Лёгкий общий бэкстоп — просто на случай, если легитимных каналов
    # наберётся много одновременно.
    enforce("video_publish_id", channel.channel_id)
    enforce("video_publish_global")

    ok, error = verify_video_manifest(manifest, channel.public_key)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid manifest: {error}")

    video_id = manifest["video_id"]
    existing = await session.get(Video, video_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Video with this video_id already exists")

    qualities = manifest.get("qualities", [])
    if len(qualities) != len(torrents):
        raise HTTPException(
            status_code=400,
            detail=f"Mismatch: {len(qualities)} qualities in manifest, {len(torrents)} torrent files uploaded",
        )

    video = Video(
        video_id=video_id,
        channel_id=manifest["channel_id"],
        title=manifest["title"],
        description=manifest.get("description", ""),
        duration_seconds=manifest.get("duration", 0),
        manifest_json=json.dumps(manifest),
        signature=manifest["signature"],
    )
    session.add(video)

    for quality_meta, torrent_file in zip(qualities, torrents):
        torrent_bytes = await torrent_file.read()
        chunk = VideoChunk(
            video_id=video_id,
            quality=quality_meta["label"],
            torrent_infohash=quality_meta["torrent_infohash"],
            torrent_file=torrent_bytes,
        )
        session.add(chunk)

    await session.commit()

    return {"video_id": video_id, "status": "published"}


@app.get("/api/video/{video_id}/manifest")
async def get_manifest(video_id: str, session: AsyncSession = Depends(get_session)):
    enforce("manifest_read")
    video = await session.get(Video, video_id)
    if video is None or video.removed or video.access_level == "private":
        raise HTTPException(status_code=404, detail="Video not found")
    return json.loads(video.manifest_json)


@app.get("/api/video/{video_id}/chunk/{quality}.torrent")
async def get_torrent(
    video_id: str, quality: str, session: AsyncSession = Depends(get_session),
):
    from fastapi.responses import Response

    enforce("torrent_download")

    video = await session.get(Video, video_id)
    if video is None or video.removed or video.access_level == "private":
        raise HTTPException(status_code=404, detail="Video not found")

    result = await session.execute(
        select(VideoChunk).where(
            VideoChunk.video_id == video_id, VideoChunk.quality == quality,
        )
    )
    chunk = result.scalar_one_or_none()
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")

    video.download_count += 1
    await session.commit()

    return Response(content=chunk.torrent_file, media_type="application/x-bittorrent")
    
@app.get("/api/search", response_model=SearchResponse)
async def search_videos(
    q: str,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
):
    # Строже остальных read-путей: ts_vector считается на лету (нет
    # выражения-индекса), это самый CPU-дорогой запрос на сайте.
    enforce("search")

    if not q or len(q.strip()) == 0:
        return SearchResponse(query=q, results=[])

    # Полнотекстовый поиск PostgreSQL по title + description видео,
    # плюс отдельно по display_name канала (объединяем через UNION по video_id)
    sql = text("""
        SELECT DISTINCT v.video_id, v.title, v.channel_id, c.display_name,
               v.duration_seconds, v.download_count,
               ts_rank(
                   to_tsvector('simple', v.title || ' ' || v.description || ' ' || c.display_name),
                   plainto_tsquery('simple', :query)
               ) AS rank
        FROM videos v
        JOIN channels c ON v.channel_id = c.channel_id
        WHERE to_tsvector('simple', v.title || ' ' || v.description || ' ' || c.display_name)
              @@ plainto_tsquery('simple', :query)
          AND v.removed = false
          AND v.access_level = 'public'
          AND c.banned = false
        ORDER BY rank DESC
        LIMIT :limit
    """)

    result = await session.execute(sql, {"query": q, "limit": limit})
    rows = result.fetchall()

    results = [
        SearchResultItem(
            video_id=row.video_id,
            title=row.title,
            channel_id=row.channel_id,
            channel_display_name=row.display_name,
            duration_seconds=row.duration_seconds,
            download_count=row.download_count,
        )
        for row in rows
    ]

    return SearchResponse(query=q, results=results)
    
@app.get("/api/channel/{channel_id}/videos", response_model=ChannelVideosResponse)
async def get_channel_videos(
    channel_id: str,
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    enforce("channel_videos")

    channel = await session.get(Channel, channel_id)
    if channel is None or channel.banned:
        raise HTTPException(status_code=404, detail="Channel not found")

    count_result = await session.execute(
        select(func.count()).select_from(Video)
        .where(
            Video.channel_id == channel_id, Video.removed == False,  # noqa: E712
            Video.access_level == "public",
        )
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(Video)
        .where(
            Video.channel_id == channel_id, Video.removed == False,  # noqa: E712
            Video.access_level == "public",
        )
        .order_by(Video.published_at.desc())
        .limit(limit)
        .offset(offset)
    )
    videos = result.scalars().all()

    return ChannelVideosResponse(
        channel_id=channel_id,
        total=total,
        videos=[
            VideoListItem(
                video_id=v.video_id,
                title=v.title,
                duration_seconds=v.duration_seconds,
                download_count=v.download_count,
                published_at=v.published_at.isoformat(),
                access_level=v.access_level,
            )
            for v in videos
        ],
    )

def _i18n_ctx(request: Request) -> dict:
    """Общие переменные локализации сайта для каждого шаблона: t() —
    функция перевода, lang — текущий язык, для переключателя в шапке."""
    return {
        "t": get_translator(request),
        "lang": get_language(request),
        "js_strings": get_strings(request),
    }


@app.get("/set-lang")
async def set_lang(request: Request, lang: str, next: str = "/"):
    """
    Переключение языка САЙТА (независимо от языка моста — см.
    app/i18n.py). Хранится в cookie конкретного браузера, а не в БД —
    сайт не привязывает выбор языка к конкретному пользователю/сессии,
    просто к устройству/браузеру, с которого зашли.
    """
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    response = RedirectResponse(url=next or "/")
    response.set_cookie(COOKIE_NAME, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request, q: str = "", session: AsyncSession = Depends(get_session)):
    results = []
    recent_videos = []
    random_videos = []

    if q.strip():
        # Тот же дорогой ts_vector-запрос, что и /api/search — тот же бюджет.
        enforce("search")
        sql = text("""
            SELECT DISTINCT v.video_id, v.title, c.display_name AS channel_display_name,
                   v.duration_seconds,
                   ts_rank(
                       to_tsvector('simple', v.title || ' ' || v.description || ' ' || c.display_name),
                       plainto_tsquery('simple', :query)
                   ) AS rank
            FROM videos v
            JOIN channels c ON v.channel_id = c.channel_id
            WHERE to_tsvector('simple', v.title || ' ' || v.description || ' ' || c.display_name)
                  @@ plainto_tsquery('simple', :query)
              AND v.removed = false
              AND v.access_level = 'public'
              AND c.banned = false
            ORDER BY rank DESC LIMIT 40
        """)
        result = await session.execute(sql, {"query": q})
        results = result.fetchall()
    else:
        # Без поискового запроса — лента главной: "recent" (8 последних
        # опубликованных) + "random" (16 случайных, за вычетом уже попавших
        # в recent, чтобы одно и то же видео не мелькало на странице дважды).
        # Вкладка "подписки" — после того, как появится сам механизм подписок.
        enforce("home_feed")

        recent_sql = text("""
            SELECT v.video_id, v.title, c.display_name AS channel_display_name, v.duration_seconds
            FROM videos v
            JOIN channels c ON v.channel_id = c.channel_id
            WHERE v.removed = false AND v.access_level = 'public' AND c.banned = false
            ORDER BY v.published_at DESC
            LIMIT 8
        """)
        recent_result = await session.execute(recent_sql)
        recent_videos = recent_result.fetchall()
        recent_ids = {v.video_id for v in recent_videos}

        # ORDER BY random() — на скромном объёме данных этого проекта
        # (нишевый сайт, не миллионы строк) достаточно быстро и не требует
        # отдельной инфраструктуры для сэмплирования; берём с запасом на
        # длину recent_ids, чтобы после фильтрации осталось 16.
        random_sql = text("""
            SELECT v.video_id, v.title, c.display_name AS channel_display_name, v.duration_seconds
            FROM videos v
            JOIN channels c ON v.channel_id = c.channel_id
            WHERE v.removed = false AND v.access_level = 'public' AND c.banned = false
            ORDER BY random()
            LIMIT :limit
        """)
        random_result = await session.execute(random_sql, {"limit": 16 + len(recent_ids)})
        random_videos = [v for v in random_result.fetchall() if v.video_id not in recent_ids][:16]

    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "query": q,
            "results": results,
            "recent_videos": recent_videos,
            "random_videos": random_videos,
            **_i18n_ctx(request),
        },
    )


@app.get("/channel/{channel_id}", response_class=HTMLResponse)
async def channel_page(
    channel_id: str, request: Request, session: AsyncSession = Depends(get_session),
):
    enforce("channel_page")

    channel = await session.get(Channel, channel_id)
    if channel is None or channel.banned:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await session.execute(
        select(Video)
        .where(
            Video.channel_id == channel_id, Video.removed == False,  # noqa: E712
            Video.access_level == "public",
        )
        .order_by(Video.published_at.desc())
    )
    videos = result.scalars().all()

    pinned_video = None
    if channel.pinned_video_id:
        pinned_video = await session.get(Video, channel.pinned_video_id)
        if pinned_video is not None and (pinned_video.removed or pinned_video.access_level == "private"):
            pinned_video = None

    return templates.TemplateResponse(
        request,
        "channel.html",
        {"channel": channel, "videos": videos, "pinned_video": pinned_video, **_i18n_ctx(request)},
    )


@app.get("/video/{video_id}", response_class=HTMLResponse)
async def video_page(
    video_id: str, request: Request, session: AsyncSession = Depends(get_session),
):
    enforce("video_page")

    video = await session.get(Video, video_id)
    if video is None or video.removed or video.access_level == "private":
        # private — намеренно не отдаётся сайтом никому (см. models.py:Video.access_level) —
        # сайт не умеет отличать владельца от постороннего посетителя, поэтому
        # приватные видео предназначены для просмотра только локально у автора.
        raise HTTPException(status_code=404, detail="Video not found")

    channel = await session.get(Channel, video.channel_id)
    if channel is not None and channel.banned:
        raise HTTPException(status_code=404, detail="Video not found")
    manifest = json.loads(video.manifest_json)

    return templates.TemplateResponse(
        request,
        "video.html",
        {
            "video": {
                "video_id": video.video_id,
                "channel_id": video.channel_id,
                "title": video.title,
                "description": video.description,
                "qualities": manifest.get("qualities", []),
                "like_count": video.like_count,
                "dislike_count": video.dislike_count,
                "comment_count": video.comment_count,
            },
            "channel_display_name": (channel.site_display_name or channel.display_name) if channel else "Unknown",
            **_i18n_ctx(request),
        },
    )

@app.get("/publish", response_class=HTMLResponse)
async def publish_page(request: Request):
    return templates.TemplateResponse(request, "publish.html", _i18n_ctx(request))


@app.get("/studio", response_class=HTMLResponse)
async def studio_page(request: Request):
    """
    "Студия" — управление своим каналом: доступ к видео (открытый/по
    ссылке/ограниченный), название и описание НА ЭТОМ САЙТЕ, закреплённое
    видео. Какой именно channel_id редактировать, страница узнаёт сама на
    клиенте через локальный мост (GET /bridge/my_channel) — сайт этого не
    знает и не должен: у него нет понятия "текущий пользователь", только
    криптографические channel_id/подписи.
    """
    return templates.TemplateResponse(request, "studio.html", _i18n_ctx(request))


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request):
    """
    Менеджер каналов — реализуется на стороне моста (управление несколькими
    каналами/идентичностями), отдельная задача. Пока просто информационная
    страница-заглушка.
    """
    return templates.TemplateResponse(request, "channels.html", _i18n_ctx(request))


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request, "about.html", _i18n_ctx(request))


@app.get("/rules", response_class=HTMLResponse)
async def rules_page(request: Request):
    return templates.TemplateResponse(request, "rules.html", _i18n_ctx(request))

# Модерация (remove video / ban channel) намеренно НЕ выставлена через HTTP —
# см. scripts/moderate.py: локальный CLI-скрипт, работающий напрямую с БД на
# том же хосте. Даже с токеном HTTP-эндпоинт — лишняя поверхность атаки
# (можно найти путь перебором, токен может утечь через логи прокси и т.п.);
# для одного оператора, имеющего shell-доступ к машине с сайтом, в этом
# просто нет необходимости.
