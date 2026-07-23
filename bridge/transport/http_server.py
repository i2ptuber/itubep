"""
http_server.py — Слой 1 (транспорт): HTTP/CORS фасад между JS сайта и Слоем 2.

Не содержит логики авторизации — только парсинг запросов, извлечение Origin/token,
проброс в BridgePolicy, формирование ответа с нужными CORS-заголовками.
"""

from __future__ import annotations

import base64
import json
import asyncio

from aiohttp import web

from policy.authz import BridgePolicy, PermissionDenied

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 9080


def _extract_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return ""


def _cors_headers(origin: str) -> dict:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }


@web.middleware
async def cors_middleware(request: web.Request, handler):
    origin = request.headers.get("Origin", "")

    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(origin))

    try:
        response = await handler(request)
    except PermissionDenied as e:
        response = web.json_response({"error": str(e)}, status=403)
    except web.HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = web.json_response({"error": f"internal error: {e}"}, status=500)

    for k, v in _cors_headers(origin).items():
        response.headers[k] = v
    return response


def create_app(policy: BridgePolicy) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])

    async def pair_request(request: web.Request):
        origin = request.headers.get("Origin", "")
        if not origin:
            return web.json_response({"error": "missing Origin header"}, status=400)
        result = policy.request_pairing(origin)
        return web.json_response(result)

    async def pair_confirm(request: web.Request):
        origin = request.headers.get("Origin", "")
        body = await request.json()
        code = body.get("code", "")
        loop = asyncio.get_event_loop()
        token = await loop.run_in_executor(None, policy.confirm_pairing, origin, code)
        if token is None:
            return web.json_response({"error": "invalid or expired code"}, status=403)
        return web.json_response({"token": token})

    async def add_torrent(request: web.Request):
        token = _extract_token(request)
        body = await request.json()

        torrent_b64 = body.get("torrent_b64", "")
        torrent_name = body.get("torrent_name", "")
        video_id = body.get("video_id", "")
        if not (torrent_b64 and torrent_name and video_id):
            return web.json_response({"error": "missing required fields"}, status=400)

        torrent_bytes = base64.b64decode(torrent_b64)
        handle = policy.add_torrent(token, torrent_bytes, torrent_name, video_id)

        return web.json_response({
            "torrent_id": handle.torrent_id,
            "torrent_name": handle.torrent_name,
            "info_hash": handle.info_hash,
            "total_files": handle.total_files,
        })

    async def seek(request: web.Request):
        token = _extract_token(request)
        body = await request.json()

        torrent_id = body.get("torrent_id")
        target_segment_index = body.get("target_segment_index")
        if torrent_id is None or target_segment_index is None:
            return web.json_response({"error": "missing required fields"}, status=400)

        policy.set_seek_priority(
            token, torrent_id, target_segment_index,
            window_ahead=body.get("window_ahead", 5),
            window_behind=body.get("window_behind", 1),
        )
        return web.json_response({"status": "ok"})

    async def progress(request: web.Request):
        token = _extract_token(request)
        torrent_id = int(request.query.get("torrent_id", "0"))
        data = policy.get_progress(token, torrent_id)
        return web.json_response(data)

    async def remove(request: web.Request):
        token = _extract_token(request)
        body = await request.json()
        torrent_id = body.get("torrent_id")
        if torrent_id is None:
            return web.json_response({"error": "missing torrent_id"}, status=400)
        policy.remove_torrent(token, torrent_id, delete_local_data=body.get("delete_local_data", False))
        return web.json_response({"status": "ok"})
    
    async def publish(request: web.Request):
        token = _extract_token(request)
        try:
            result = policy.publish_video(token)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(result)

    async def stream_token(request: web.Request):
        """
        Минтит короткоживущий scoped-токен для чтения ОДНОГО torrent_id —
        именно он, не основной bearer-токен, идёт в query-параметры
        /bridge/playlist и /bridge/segment (HLS.js не даёт надёжно
        проставить кастомный заголовок на каждый запрос сегмента). Сам
        этот запрос авторизуется как обычно — Authorization-заголовком,
        не query-параметром.
        """
        token = _extract_token(request)
        body = await request.json()
        torrent_id = body.get("torrent_id")
        if torrent_id is None:
            return web.json_response({"error": "missing torrent_id"}, status=400)

        stream_tok, ttl_seconds = policy.create_stream_token(token, torrent_id)
        return web.json_response({"stream_token": stream_tok, "expires_in": ttl_seconds})

    async def playlist(request: web.Request):
        stream_tok = request.query.get("stream_token", "")
        torrent_id = int(request.query.get("torrent_id", "0"))
        durations_b64 = request.query.get("durations_b64", "")

        if not policy.check_stream_access(stream_tok, torrent_id):
            return web.Response(status=403, text="invalid or expired stream_token")

        try:
            durations = json.loads(base64.b64decode(durations_b64).decode())
        except Exception:
            return web.Response(status=400, text="invalid durations_b64")

        lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{int(max(durations)) + 1}",
                 "#EXT-X-PLAYLIST-TYPE:VOD"]
        for i, dur in enumerate(durations):
            lines.append(f"#EXTINF:{dur:.3f},")
            lines.append(f"/bridge/segment?stream_token={stream_tok}&torrent_id={torrent_id}&index={i}")
        lines.append("#EXT-X-ENDLIST")

        return web.Response(text="\n".join(lines), content_type="application/vnd.apple.mpegurl")

    async def segment(request: web.Request):
        stream_tok = request.query.get("stream_token", "")
        torrent_id = int(request.query.get("torrent_id", "0"))
        index = int(request.query.get("index", "0"))

        torrents = policy.snark.rpc.torrent_get(ids=[torrent_id], fields=["id", "name"])
        if not torrents:
            return web.Response(status=404, text="torrent not found")
        torrent_name = torrents[0]["name"]

        data = policy.get_segment_bytes(stream_tok, torrent_id, torrent_name, index)
        if data is None:
            # 404 без тела — HLS.js трактует как обычную сетевую ошибку
            # (retryable через fragLoadingMaxRetry), не пытается парсить тело
            # как медиа-данные
            return web.Response(status=404)

        return web.Response(body=data, content_type="video/mp2t")

    app.router.add_post("/bridge/stream_token", stream_token)
    app.router.add_get("/bridge/playlist", playlist)
    app.router.add_get("/bridge/segment", segment)

    app.router.add_post("/bridge/publish", publish)
    
    app.router.add_post("/bridge/pair/request", pair_request)
    app.router.add_post("/bridge/pair/confirm", pair_confirm)
    app.router.add_post("/bridge/add", add_torrent)
    app.router.add_post("/bridge/seek", seek)
    app.router.add_get("/bridge/progress", progress)
    app.router.add_post("/bridge/remove", remove)

    return app


def run():
    from ui.tkinter_dialog import TkinterPairingDialog
    policy = BridgePolicy(dialog=TkinterPairingDialog())
    print("Возобновляю раздачу ранее добавленных торрентов...")
    policy.resume_all_owned_torrents()
    print("Готово.")
    app = create_app(policy)
    web.run_app(app, host=BRIDGE_HOST, port=BRIDGE_PORT)


if __name__ == "__main__":
    run()
