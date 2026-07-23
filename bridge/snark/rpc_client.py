"""
rpc_client.py — клиент к I2PSnark-RPC (Transmission-совместимый JSON-RPC).

Часть Слоя 3 (интеграция с i2psnark). Не содержит никакой логики авторизации/
происхождения запроса — это ответственность Слоя 2. Модуль просто исполняет то,
что ему говорят.

Подтверждённый экспериментально набор рабочих методов (см. план, раздел про
torrent-set): torrent-add, torrent-get, torrent-start, torrent-start-now,
torrent-stop, torrent-start-all, torrent-stop-all, torrent-verify, torrent-remove,
session-get, session-stats, session-close, free-space, tags-get-list.
torrent-set НЕДОСТУПЕН (нерабочий Vuze-порт) — приоритет файлов идёт через
web_client.py, не отсюда.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import requests


class RPCError(Exception):
    """Ошибка ответа RPC (result != 'success')."""


class RPCClient:
    def __init__(self, url: str = "http://127.0.0.1:8002/transmission/rpc"):
        self.url = url
        self.session = requests.Session()
        self._session_id: str | None = None

    def _refresh_session_id(self) -> None:
        resp = self.session.post(self.url, data=b"")
        if resp.status_code == 409:
            self._session_id = resp.headers.get("X-Transmission-Session-Id")
        elif "X-Transmission-Session-Id" in resp.headers:
            self._session_id = resp.headers["X-Transmission-Session-Id"]

    def call(self, method: str, arguments: dict | None = None, *, raise_on_error: bool = True) -> dict:
        if self._session_id is None:
            self._refresh_session_id()

        payload = {"method": method, "arguments": arguments or {}}
        headers = {"X-Transmission-Session-Id": self._session_id or ""}

        resp = self.session.post(self.url, headers=headers, data=json.dumps(payload))

        if resp.status_code == 409:
            self._session_id = resp.headers.get("X-Transmission-Session-Id")
            headers["X-Transmission-Session-Id"] = self._session_id or ""
            resp = self.session.post(self.url, headers=headers, data=json.dumps(payload))

        resp.raise_for_status()
        result = resp.json()

        if raise_on_error and result.get("result") != "success":
            raise RPCError(f"{method} failed: {result.get('result')}")

        return result

    # --- Высокоуровневые обёртки ---

    def session_get(self, fields: list[str] | None = None) -> dict:
        """
        session-get — подтверждённо рабочий базовый метод (см. заголовок
        файла). Возвращает текущие настройки сессии i2psnark, включая
        "download-dir" — РЕАЛЬНУЮ, фактически настроенную директорию
        загрузок, а не то, что мост о ней ПРЕДПОЛАГАЕТ (см.
        SnarkIntegration.get_real_storage_dir — там же объяснение, почему
        стоит спрашивать это у i2psnark напрямую, а не хардкодить путь).
        """
        result = self.call("session-get", {"fields": fields} if fields else {})
        return result.get("arguments", {})

    def torrent_get(self, ids: list[int] | None = None, fields: list[str] | None = None) -> list[dict]:
        fields = fields or [
            "id", "name", "status", "percentDone",
            "rateDownload", "rateUpload", "peersConnected",
            "files", "fileStats", "hashString",
        ]
        args: dict[str, Any] = {"fields": fields}
        if ids is not None:
            args["ids"] = ids
        result = self.call("torrent-get", args)
        return result.get("arguments", {}).get("torrents", [])

    def torrent_add_file(self, torrent_path: str, paused: bool = False, download_dir: str | None = None) -> dict:
        with open(torrent_path, "rb") as f:
            metainfo_b64 = base64.b64encode(f.read()).decode("ascii")
        args = {"metainfo": metainfo_b64, "paused": paused}
        if download_dir is not None:
            args["download-dir"] = download_dir
        result = self.call("torrent-add", args)
        return result.get("arguments", {}).get("torrent-added") \
            or result.get("arguments", {}).get("torrent-duplicate")

    def torrent_add_bytes(self, torrent_bytes: bytes, paused: bool = False, download_dir: str | None = None) -> dict:
        """
        download_dir — ЯВНО указать i2psnark, куда класть/искать данные этого
        конкретного торрента (родительская директория, БЕЗ добавления имени
        торрента — i2psnark сам создаёт поддиректорию <download_dir>/<name>/
        для multi-file торрента). КРИТИЧНО передавать при публикации: без
        этого i2psnark использует СВОЮ собственную глобально настроенную (в
        его же веб-интерфейсе) директорию загрузок, которая может не
        совпадать с тем, куда мост реально скопировал уже готовые сегменты
        (см. publisher.py) — тогда верификация не находит файлы на месте и
        торрент считается пустым/недокачанным, хотя данные физически лежат
        на диске просто в другом месте.
        """
        metainfo_b64 = base64.b64encode(torrent_bytes).decode("ascii")
        args = {"metainfo": metainfo_b64, "paused": paused}
        if download_dir is not None:
            args["download-dir"] = download_dir
        result = self.call("torrent-add", args)
        return result.get("arguments", {}).get("torrent-added") \
            or result.get("arguments", {}).get("torrent-duplicate")

    def torrent_start(self, torrent_id: int) -> None:
        self.call("torrent-start", {"ids": [torrent_id]})

    def torrent_start_now(self, torrent_id: int) -> None:
        self.call("torrent-start-now", {"ids": [torrent_id]})

    def torrent_stop(self, torrent_id: int) -> None:
        self.call("torrent-stop", {"ids": [torrent_id]})

    def torrent_verify(self, torrent_id: int) -> None:
        self.call("torrent-verify", {"ids": [torrent_id]})

    def torrent_remove(self, torrent_id: int, delete_local_data: bool = False) -> None:
        self.call("torrent-remove", {
            "ids": [torrent_id],
            "delete-local-data": delete_local_data,
        })

    def wait_for_verification(
        self, torrent_id: int, timeout_seconds: float = 30.0,
    ) -> dict | None:
        """
        Ждёт, пока torrent-verify реально завершится — то есть статус выйдет
        из "check-wait"(1)/"checking"(2). ВАЖНО: не ждём статус "seeding"(6) —
        для paused-торрента (как при публикации) он попросту недостижим без
        отдельного torrent-start, верификация на паузе останавливается на
        status=0 с уже корректным percentDone. Возвращает последний известный
        torrent-dict (с percentDone) или None по таймауту.
        """
        import time
        CHECKING_STATUSES = {1, 2}
        deadline = time.monotonic() + timeout_seconds
        last = None
        while time.monotonic() < deadline:
            torrents = self.torrent_get(ids=[torrent_id], fields=["id", "status", "percentDone"])
            if not torrents:
                return None
            last = torrents[0]
            if last.get("status") not in CHECKING_STATUSES:
                return last
            time.sleep(0.5)
        return last

    def wait_for_status(self, torrent_id: int, target_status: int, timeout_seconds: float = 10.0) -> bool:
        """
        Блокирующее ожидание нужного статуса торрента (0=stopped и т.п. — см.
        Transmission RPC spec для конкретных кодов). Возвращает True, если дождались,
        False по таймауту.
        """
        import time
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            torrents = self.torrent_get(ids=[torrent_id], fields=["id", "status"])
            if torrents and torrents[0].get("status") == target_status:
                return True
            time.sleep(0.5)
        return False
