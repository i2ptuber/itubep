"""
admin/app.py — веб-панель модерации ("студия в масштабе всего сайта").

ВАЖНО: это ОТДЕЛЬНЫЙ процесс от публичного сайта (app/main.py), с
собственным uvicorn и собственным портом. Он ничего не знает про i2p и
никогда не должен слушать ничего, кроме 127.0.0.1 — доступ снаружи только
через SSH-туннель (см. README раздел "Модерация"). Никакого логина/пароля
здесь нет — как и в scripts/moderate.py, доступ определяется тем, что вы
вообще можете достучаться до порта, а не отдельным секретом.

Использует ту же БД и ту же логику (app/moderation_service.py), что и
CLI-скрипт scripts/moderate.py — оба лишь разные интерфейсы к одним и тем
же функциям, а не два независимых куска SQL.

Запуск (из каталога site/):
    python3 -m uvicorn admin.app:app --host 127.0.0.1 --port 8877
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import async_session
from app import moderation_service as svc

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="ITubeP — модерация")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

PAGE_SIZE = 40


@app.on_event("startup")
async def _warn_if_not_loopback() -> None:
    host = os.environ.get("UVICORN_HOST", "")
    if host and host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "!!! ВНИМАНИЕ: похоже, админка запущена НЕ на 127.0.0.1. "
            "Эта панель не предназначена для публичного доступа — "
            "проверьте флаг --host у uvicorn.",
            flush=True,
        )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return RedirectResponse(url="/videos")


@app.get("/videos", response_class=HTMLResponse)
async def videos_page(
    request: Request, q: str = "", page: int = 1, status: str = "all", channel_id: str = ""
):
    if status not in ("all", "active", "removed_only"):
        status = "all"
    page = max(page, 1)
    async with async_session() as db:
        rows = await svc.list_videos(
            db,
            query=q,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
            status=status,
            channel_id=channel_id,
        )
        total = await svc.count_videos(db, query=q, status=status, channel_id=channel_id)

    return templates.TemplateResponse(
        request,
        "videos.html",
        {
            "rows": rows,
            "q": q,
            "page": page,
            "total_pages": max(1, math.ceil(total / PAGE_SIZE)),
            "total": total,
            "status": status,
            "channel_id": channel_id,
        },
    )


@app.get("/channels", response_class=HTMLResponse)
async def channels_page(request: Request, q: str = "", page: int = 1, status: str = "all"):
    if status not in ("all", "active", "banned_only"):
        status = "all"
    page = max(page, 1)
    async with async_session() as db:
        channels = await svc.list_channels(
            db, query=q, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, status=status
        )
        total = await svc.count_channels(db, query=q, status=status)

    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "channels": channels,
            "q": q,
            "page": page,
            "total_pages": max(1, math.ceil(total / PAGE_SIZE)),
            "total": total,
            "status": status,
        },
    )


@app.post("/videos/{video_id}/remove")
async def action_remove_video(
    video_id: str, reason: str = Form(""), q: str = Form(""), page: int = Form(1), status: str = Form("all")
):
    async with async_session() as db:
        try:
            await svc.remove_video(db, video_id, reason=reason)
        except svc.NotFound:
            raise HTTPException(404, "видео не найдено")
    return RedirectResponse(url=f"/videos?q={q}&page={page}&status={status}", status_code=303)


@app.post("/videos/{video_id}/restore")
async def action_restore_video(
    video_id: str, q: str = Form(""), page: int = Form(1), status: str = Form("all")
):
    async with async_session() as db:
        try:
            await svc.restore_video(db, video_id)
        except svc.NotFound:
            raise HTTPException(404, "видео не найдено")
    return RedirectResponse(url=f"/videos?q={q}&page={page}&status={status}", status_code=303)


@app.post("/videos/{video_id}/purge")
async def action_purge_video(
    video_id: str, q: str = Form(""), page: int = Form(1), status: str = Form("all")
):
    """Необратимо: чистит торрент/превью/манифест/описание/комментарии/реакции.
    Строка video_id остаётся (заблокированной) — см. moderation_service.purge_video."""
    async with async_session() as db:
        try:
            await svc.purge_video(db, video_id)
        except svc.NotFound:
            raise HTTPException(404, "видео не найдено")
    return RedirectResponse(url=f"/videos?q={q}&page={page}&status={status}", status_code=303)


@app.post("/channels/{channel_id}/ban")
async def action_ban_channel(
    channel_id: str, reason: str = Form(""), q: str = Form(""), page: int = Form(1), status: str = Form("all")
):
    async with async_session() as db:
        try:
            await svc.ban_channel(db, channel_id, reason=reason)
        except svc.NotFound:
            raise HTTPException(404, "канал не найден")
    return RedirectResponse(url=f"/channels?q={q}&page={page}&status={status}", status_code=303)


@app.post("/channels/{channel_id}/unban")
async def action_unban_channel(
    channel_id: str, q: str = Form(""), page: int = Form(1), status: str = Form("all")
):
    async with async_session() as db:
        try:
            await svc.unban_channel(db, channel_id)
        except svc.NotFound:
            raise HTTPException(404, "канал не найден")
    return RedirectResponse(url=f"/channels?q={q}&page={page}&status={status}", status_code=303)


@app.post("/channels/{channel_id}/purge")
async def action_purge_channel(
    channel_id: str, q: str = Form(""), page: int = Form(1), status: str = Form("all")
):
    """Необратимо: банит канал + чистит тяжёлый контент всех его видео и
    его комментарии/реакции. Строки channel_id/video_id остаются
    (заблокированными) — см. moderation_service.purge_channel."""
    async with async_session() as db:
        try:
            await svc.purge_channel(db, channel_id)
        except svc.NotFound:
            raise HTTPException(404, "канал не найден")
    return RedirectResponse(url=f"/channels?q={q}&page={page}&status={status}", status_code=303)
