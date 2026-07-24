"""
main.py — точка входа FastAPI-приложения.
"""

from __future__ import annotations

import json

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import get_session, init_models, async_session
from .models import Channel, Video, VideoChunk, RateLimitConfig
from .schemas import ChannelRegisterRequest, ChannelResponse, SearchResultItem, SearchResponse, VideoListItem, ChannelVideosResponse
from .crypto import verify_channel_record, verify_video_manifest
from .rate_limit import enforce

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
    if video is None or video.removed:
        raise HTTPException(status_code=404, detail="Video not found")
    return json.loads(video.manifest_json)


@app.get("/api/video/{video_id}/chunk/{quality}.torrent")
async def get_torrent(
    video_id: str, quality: str, session: AsyncSession = Depends(get_session),
):
    from fastapi.responses import Response

    enforce("torrent_download")

    video = await session.get(Video, video_id)
    if video is None or video.removed:
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
        .where(Video.channel_id == channel_id, Video.removed == False)  # noqa: E712
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(Video)
        .where(Video.channel_id == channel_id, Video.removed == False)  # noqa: E712
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
            )
            for v in videos
        ],
    )

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request, q: str = "", session: AsyncSession = Depends(get_session)):
    results = []
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
              AND c.banned = false
            ORDER BY rank DESC LIMIT 40
        """)
        result = await session.execute(sql, {"query": q})
        results = result.fetchall()

    return templates.TemplateResponse(
        request, "search.html", {"query": q, "results": results},
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
        .where(Video.channel_id == channel_id, Video.removed == False)  # noqa: E712
        .order_by(Video.published_at.desc())
    )
    videos = result.scalars().all()

    return templates.TemplateResponse(
        request, "channel.html", {"channel": channel, "videos": videos},
    )


@app.get("/video/{video_id}", response_class=HTMLResponse)
async def video_page(
    video_id: str, request: Request, session: AsyncSession = Depends(get_session),
):
    enforce("video_page")

    video = await session.get(Video, video_id)
    if video is None or video.removed:
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
            },
            "channel_display_name": channel.display_name if channel else "Unknown",
        },
    )

@app.get("/publish", response_class=HTMLResponse)
async def publish_page(request: Request):
    return templates.TemplateResponse(request, "publish.html", {})

# Модерация (remove video / ban channel) намеренно НЕ выставлена через HTTP —
# см. scripts/moderate.py: локальный CLI-скрипт, работающий напрямую с БД на
# том же хосте. Даже с токеном HTTP-эндпоинт — лишняя поверхность атаки
# (можно найти путь перебором, токен может утечь через логи прокси и т.п.);
# для одного оператора, имеющего shell-доступ к машине с сайтом, в этом
# просто нет необходимости.
