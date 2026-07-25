"""
authz.py — публичный интерфейс Слоя 2. Оборачивает SnarkIntegration (Слой 3)
проверками токена/owner_origin/блеклиста/режима подтверждения.

Слой 1 обращается ТОЛЬКО к BridgePolicy, никогда напрямую к SnarkIntegration.
"""

from __future__ import annotations

import secrets
import time
from enum import Enum
from pathlib import Path

from snark import SnarkIntegration, VideoTorrentHandle

from ui.tkinter_dialog import TkinterPairingDialog
from .pairing import PairingManager
from .storage import PolicyStorage
from .crypto_utils import get_or_create_channel
from snark.publisher import VideoPublisher, PublishError
from ui.publish_dialogs import PublishDialogs

class Mode(Enum):
    SILENT = "silent"
    CONFIRM = "confirm"


class PermissionDenied(Exception):
    pass


class BridgePolicy:
    def __init__(
        self,
        storage: PolicyStorage | None = None,
        dialog: TkinterPairingDialog | None = None,
        snark: SnarkIntegration | None = None,
        mode: Mode = Mode.SILENT,
    ):
        self.storage = storage or PolicyStorage()
        self.dialog = dialog or TkinterPairingDialog()
        self.pairing = PairingManager(self.storage, self.dialog)
        self.snark = snark or SnarkIntegration(
            storage_dir_provider=self.storage.get_snark_storage_dir,
            trackers=self.storage.get_trackers(),
        )
        # mode больше не хранится как простое поле — читается из БД при каждом
        # обращении, чтобы окно настроек (отдельный процесс) могло его менять
        if self.storage.get_setting("mode") is None:
            self.storage.set_setting("mode", mode.value)
        self._handles: dict[int, VideoTorrentHandle] = {}
        # Расшифрованный ключ канала кешируется в памяти процесса (никогда
        # на диске) после первой успешной разблокировки — иначе пароль
        # спрашивался бы на КАЖДУЮ публикацию, что было бы избыточно
        # навязчиво при том, что риск от кеширования в памяти одного и
        # того же процесса минимален (тот же уровень доверия, что и просто
        # запущенный процесс моста). Сбрасывается только перезапуском моста.
        self._channel_identity = None
        # Короткоживущие scoped-токены для playlist/segment (см.
        # create_stream_token/validate_stream_token ниже) — намеренно ТОЛЬКО
        # в памяти, не в БД: они и так живут часы, а не недели, потеря при
        # рестарте моста означает просто "плеер перезапросит новый при
        # следующей загрузке страницы", это не авторизационные данные,
        # которые нужно помнить между запусками.
        self._stream_tokens: dict[str, dict] = {}
        STREAM_TOKEN_TTL_SECONDS = 6 * 60 * 60  # 6 часов — с запасом на сессию просмотра
        self._stream_token_ttl = STREAM_TOKEN_TTL_SECONDS

    # --- Pairing (см. pairing.py, тут просто проброс) ---

    def request_pairing(self, origin: str) -> dict:
        return self.pairing.request_pairing(origin)

    def confirm_pairing(self, origin: str, code: str) -> str | None:
        return self.pairing.confirm_pairing(origin, code)

    def revoke(self, origin: str):
        self.storage.revoke_origin(origin)

    def block_origin(self, origin: str, reason: str = ""):
        self.storage.add_to_blocklist(origin, reason)

    # --- Проверка токена ---

    def _authenticate(self, token: str) -> str:
        origin = self.storage.get_origin_for_token(token)
        if origin is None:
            raise PermissionDenied("Невалидный или отозванный токен")
        if self.storage.is_blocked(origin):
            raise PermissionDenied("Origin в блеклисте")
        return origin

    def _confirm_if_needed(self, origin: str, description: str):
        if self.mode == Mode.CONFIRM:
            if not self.dialog.show_confirm_action(origin, description):
                raise PermissionDenied("Пользователь отклонил действие")

    def _check_ownership(self, origin: str, torrent_id: int):
        owner = self.storage.get_owner(torrent_id)
        if owner is None:
            raise PermissionDenied(f"Торрент {torrent_id} не найден в реестре владения")
        if owner != origin:
            raise PermissionDenied(f"Торрент {torrent_id} принадлежит другому origin")

    # --- Действия (вызываются Слоем 1 с token в каждом запросе) ---

    def add_torrent(
        self, token: str, torrent_bytes: bytes, expected_torrent_name: str, video_id: str,
    ) -> VideoTorrentHandle:
        origin = self._authenticate(token)

        # Идемпотентность: если этот же зритель уже добавлял это же видео
        # (например, просто перезагрузил страницу), НЕ повторяем цикл
        # stop->toggle->start заново — это рвёт уже установленные P2P-соединения
        # без всякой пользы, раз всё уже настроено правильно
        existing_torrent_id = self.storage.find_torrent_for_video(origin, video_id)
        if existing_torrent_id is not None:
            cached_handle = self._handles.get(existing_torrent_id)
            if cached_handle is not None:
                return cached_handle

            # Кэш в памяти пуст (например, мост перезапускался) — восстанавливаем
            # handle из RPC без повторного add/toggle, просто убеждаемся, что
            # торрент активен
            torrents = self.snark.rpc.torrent_get(ids=[existing_torrent_id], fields=["id", "name", "files"])
            if torrents:
                handle = VideoTorrentHandle(
                    torrent_id=existing_torrent_id,
                    torrent_name=torrents[0]["name"],
                    info_hash="",
                    total_files=len(torrents[0].get("files", [])),
                )
                self.snark.rpc.torrent_start_now(existing_torrent_id)
                self._handles[existing_torrent_id] = handle
                return handle

        self._confirm_if_needed(origin, f"добавить видео {video_id}")

        handle = self.snark.add_video_for_playback(torrent_bytes, expected_torrent_name)
        self.storage.register_torrent(handle.torrent_id, origin, video_id)
        self._handles[handle.torrent_id] = handle
        return handle

    def set_seek_priority(
        self, token: str, torrent_id: int,
        target_segment_index: int, window_ahead: int = 5, window_behind: int = 1,
    ) -> None:
        # TODO(seek-priority): функция временно отключена на уровне Слоя 3
        # (см. snark/integration.py:SnarkIntegration.set_seek_priority) —
        # stop/start у i2psnark при смене приоритета рвёт все текущие
        # BT-соединения, что перевешивает выигрыш от форсированной докачки.
        # Аутентификацию всё равно проверяем (endpoint остаётся валидным
        # для будущего), но не ходим в confirm-диалог ради действия,
        # которое сейчас ничего не делает — это было бы просто спамом
        # подтверждений в режиме Mode.CONFIRM без всякой пользы.
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)

        handle = self._handles.get(torrent_id)
        if handle is None:
            return  # то же самое: нет активного handle — и так нечего форсировать

        self.snark.set_seek_priority(handle, target_segment_index, window_ahead, window_behind)

    def get_progress(self, token: str, torrent_id: int) -> dict:
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)
        return self.snark.get_progress(torrent_id)

    def remove_torrent(self, token: str, torrent_id: int, delete_local_data: bool = False) -> None:
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)
        self._confirm_if_needed(origin, f"удалить торрент {torrent_id}")

        self.snark.remove_video(torrent_id, delete_local_data)
        self.storage.unregister_torrent(torrent_id)
        self._handles.pop(torrent_id, None)
    
    @property
    def mode(self) -> Mode:
        return Mode(self.storage.get_setting("mode", Mode.SILENT.value))

    @mode.setter
    def mode(self, value: Mode):
        self.storage.set_setting("mode", value.value)
        
    def publish_video(self, token: str, video_path_hint: str | None = None) -> dict:
        """
        Полный мастер публикации: подтверждение -> (создание канала при
        необходимости) -> выбор файла -> название/описание -> сегментация ->
        торрент -> отправка на сайт.
        """
        origin = self._authenticate(token)

        publish_dialogs = PublishDialogs()

        if not publish_dialogs.confirm_publish_request(origin):
            raise PermissionDenied("Пользователь отклонил запрос на публикацию")

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, publish_dialogs)
            self._channel_identity = channel

        video_path = publish_dialogs.choose_video_file()
        if not video_path:
            raise PermissionDenied("Файл не выбран")

        title_desc = publish_dialogs.prompt_title_description()
        if title_desc is None:
            raise PermissionDenied("Название не указано")
        title, description = title_desc

        from pathlib import Path
        publisher = VideoPublisher(self.snark, channel, http_proxy=self.storage.get_i2p_http_proxy())
        try:
            result = publisher.publish(
                video_path=Path(video_path),
                title=title,
                description=description,
                site_base_url=origin,  # публикуем на тот же сайт, что и запросил
            )
        except PublishError as e:
            raise PermissionDenied(f"Ошибка публикации: {e}")

        return result
        
    # --- "Мой канал" / студия / настройки моста, вызываемые из меню сайта ---

    def get_my_channel_id(self, token: str) -> str | None:
        """
        Для кнопки "Мой канал" — отдаёт channel_id. Если публичные данные
        канала уже закешированы в памяти или сохранены в БД — без пароля.
        Если канал СОЗДАН, но публичные данные ещё не сохранены (типичный
        случай для каналов, созданных до появления этой фичи — то есть до
        первого обращения к get_or_create_channel ПОСЛЕ обновления моста),
        один раз просит пароль, чтобы разблокировать и досохранить их —
        дальше уже без пароля. Если канала нет вообще — None, без запросов.
        """
        self._authenticate(token)
        if self._channel_identity is not None:
            return self._channel_identity.channel_id
        info = self.storage.get_channel_public_info()
        if info is not None:
            return info["channel_id"]
        if self.storage.get_setting("channel_private_key") is None:
            return None  # канала действительно ещё нет
        channel = get_or_create_channel(self.storage, PublishDialogs())
        self._channel_identity = channel
        return channel.channel_id

    def open_bridge_settings(self, token: str) -> None:
        """
        Для кнопки "Настройки" в меню сайта. _authenticate() выше бросает
        PermissionDenied для любого origin, который не был явно сопряжён
        (или был отозван/заблокирован) — http_server.py транслирует это в
        403, и сайт должен молча игнорировать такой ответ, а не спамить
        пользователя всплывающими окнами моста от лица непроверенного сайта.
        """
        self._authenticate(token)
        self._launch_settings_window()

    def _launch_settings_window(self) -> None:
        import subprocess
        import sys

        proc = getattr(self, "_settings_proc", None)
        if proc is not None and proc.poll() is None:
            return  # окно уже открыто — не плодим второй процесс на клик

        # cwd намеренно вычисляется от расположения ЭТОГО файла (bridge/policy/authz.py),
        # а не берётся неявно из os.getcwd() процесса — субпроцессу нужен
        # BRIDGE_DIR в качестве working directory, чтобы `python -m
        # ui.settings_window` нашёл пакет ui/ (python -m ищет модуль в
        # sys.path, куда автоматически попадает cwd). Если сервер почему-то
        # запущен не из BRIDGE_DIR (например, systemd-юнит с другим
        # WorkingDirectory, или ручной запуск из другого каталога), неявный
        # os.getcwd() был бы неверным и `-m ui.settings_window` падал бы с
        # ModuleNotFoundError без внятной причины на стороне сайта.
        bridge_dir = Path(__file__).resolve().parent.parent

        try:
            self._settings_proc = subprocess.Popen(
                [sys.executable, "-m", "ui.settings_window"], cwd=str(bridge_dir),
            )
        except OSError as e:
            # Пробрасываем дальше как обычное исключение — middleware на
            # стороне http_server.py (cors_middleware) поймает его,
            # напечатает traceback в stdout/лог моста и вернёт 500 с
            # текстом ошибки, а не тихо "ничего не произошло".
            raise RuntimeError(f"Не удалось запустить окно настроек: {e}") from e

    def studio_update(self, token: str, updates: dict) -> dict:
        """
        Для страницы /studio: переименование НА ЭТОМ САЙТЕ, описание,
        закреплённое видео, доступ к отдельным видео (public/unlisted/
        private). Подписывается тем же ключом канала, что публикация
        видео, и отправляется на /api/channel/{id}/studio того же сайта,
        что и запросил (см. VideoPublisher._ensure_channel_registered —
        тот же паттерн: origin как site_base_url).
        """
        import time
        import requests
        from snark.publisher import _requests_session_for, I2P_REQUEST_TIMEOUT_SECONDS, PublishError

        origin = self._authenticate(token)
        self._confirm_if_needed(origin, "обновить студию канала")

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, PublishDialogs())
            self._channel_identity = channel

        record = {
            "channel_id": channel.channel_id,
            "site_display_name": updates.get("site_display_name", ""),
            "site_description": updates.get("site_description", ""),
            "pinned_video_id": updates.get("pinned_video_id"),
            "video_access": updates.get("video_access", {}),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        record["signature"] = channel.sign(record)

        try:
            resp = _requests_session_for(origin, self.storage.get_i2p_http_proxy()).post(
                f"{origin.rstrip('/')}/api/channel/{channel.channel_id}/studio",
                json=record, timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PermissionDenied(f"Не удалось соединиться с сайтом для обновления студии: {e}")
        if resp.status_code != 200:
            raise PermissionDenied(f"Сайт отклонил обновление студии: {resp.status_code} {resp.text}")

        return resp.json()

    def get_studio_state(self, token: str) -> dict:
        """
        Для страницы /studio при загрузке: подписанный read-запрос на
        /api/channel/{id}/studio-state (в отличие от /api/channel/{id}/videos
        отдаёт ВСЕ видео канала, включая unlisted/private — иначе владельцу
        нечем было бы управлять на этой странице).
        """
        import time
        import requests
        from snark.publisher import _requests_session_for, I2P_REQUEST_TIMEOUT_SECONDS

        origin = self._authenticate(token)

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, PublishDialogs())
            self._channel_identity = channel

        record = {
            "channel_id": channel.channel_id,
            "timestamp": str(time.time()),
        }
        record["signature"] = channel.sign(record)

        try:
            resp = _requests_session_for(origin, self.storage.get_i2p_http_proxy()).post(
                f"{origin.rstrip('/')}/api/channel/{channel.channel_id}/studio-state",
                json=record, timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PermissionDenied(f"Не удалось соединиться с сайтом для чтения студии: {e}")
        if resp.status_code != 200:
            raise PermissionDenied(f"Сайт отклонил чтение студии: {resp.status_code} {resp.text}")

        return resp.json()

    def react_to_video(self, token: str, video_id: str, value: int) -> dict:
        """
        Лайк/дизлайк (value=1/-1) или отмена голоса (value=0). Подписывается
        ключом канала — тот же паттерн, что studio_update.
        """
        import time
        import requests
        from snark.publisher import _requests_session_for, I2P_REQUEST_TIMEOUT_SECONDS

        if value not in (-1, 0, 1):
            raise PermissionDenied("Некорректное значение голоса")

        origin = self._authenticate(token)
        self._confirm_if_needed(origin, f"проголосовать за видео {video_id}")

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, PublishDialogs())
            self._channel_identity = channel

        record = {
            "video_id": video_id,
            "channel_id": channel.channel_id,
            "value": value,
            # Фиксированная длина + нулями дополненные микросекунды — важно,
            # т.к. main.py сравнивает updated_at КАК СТРОКУ (VARCHAR-колонка),
            # а не как число; "163.10" < "163.9" лексикографически, что было
            # бы неверно для голосов, отправленных в быстрой последовательности.
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                          + f".{int((time.time() % 1) * 1_000_000):06d}Z",
        }
        record["signature"] = channel.sign(record)

        try:
            resp = _requests_session_for(origin, self.storage.get_i2p_http_proxy()).post(
                f"{origin.rstrip('/')}/api/video/{video_id}/react",
                json=record, timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PermissionDenied(f"Не удалось соединиться с сайтом для отправки голоса: {e}")
        if resp.status_code != 200:
            raise PermissionDenied(f"Сайт отклонил голос: {resp.status_code} {resp.text}")

        return resp.json()

    def post_comment(self, token: str, video_id: str, body: str) -> dict:
        """
        Комментарий под видео. client_nonce — случайный, для защиты от
        replay на стороне сайта (см. site: models.py:Comment.client_nonce),
        генерируется на КАЖДЫЙ вызов заново — иначе повторная отправка
        после сбоя сети выглядела бы для сайта как дубликат и отклонялась
        бы, даже если первая попытка реально не дошла.
        """
        import secrets
        import time
        import requests
        from snark.publisher import _requests_session_for, I2P_REQUEST_TIMEOUT_SECONDS

        origin = self._authenticate(token)
        self._confirm_if_needed(origin, "опубликовать комментарий")

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, PublishDialogs())
            self._channel_identity = channel

        record = {
            "video_id": video_id,
            "channel_id": channel.channel_id,
            "body": body,
            "client_nonce": secrets.token_hex(16),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        record["signature"] = channel.sign(record)

        try:
            resp = _requests_session_for(origin, self.storage.get_i2p_http_proxy()).post(
                f"{origin.rstrip('/')}/api/video/{video_id}/comment",
                json=record, timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PermissionDenied(f"Не удалось соединиться с сайтом для отправки комментария: {e}")
        if resp.status_code != 200:
            raise PermissionDenied(f"Сайт отклонил комментарий: {resp.status_code} {resp.text}")

        return resp.json()

    def create_stream_token(self, token: str, torrent_id: int) -> tuple[str, int]:
        """
        Минтит короткоживущий scoped-токен для чтения ОДНОГО конкретного
        torrent_id (плейлист+сегменты) — именно он, а не основной
        bearer-токен, попадает в query-параметры /bridge/playlist и
        /bridge/segment. HLS.js не умеет надёжно проставлять кастомные
        заголовки на каждый запрос сегмента, поэтому токен в URL не
        убрать полностью — но можно резко сократить его "стоимость" при
        утечке (история браузера, логи, скриншот): scoped-токен даёт
        только чтение одного видео на ограниченное время, а не полный
        доступ к /bridge/add, /bridge/seek и остальным торрентам этого же
        origin.

        Требует основной bearer-токен в заголовке Authorization (как и
        остальные действующие эндпоинты) — минтинг сам по себе НЕ виден
        в URL, светится только результат, с урезанными правами.
        """
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)

        stream_token = secrets.token_urlsafe(24)
        self._stream_tokens[stream_token] = {
            "origin": origin,
            "torrent_id": torrent_id,
            "expires_at": time.monotonic() + self._stream_token_ttl,
        }
        return stream_token, self._stream_token_ttl

    def _validate_stream_token(self, stream_token: str, torrent_id: int) -> str | None:
        entry = self._stream_tokens.get(stream_token)
        if entry is None:
            return None
        if entry["torrent_id"] != torrent_id:
            return None
        if time.monotonic() > entry["expires_at"]:
            del self._stream_tokens[stream_token]
            return None

        origin = entry["origin"]
        # Догоняем немедленную реакцию на revoke/блокировку — иначе
        # отозванный основной токен всё равно давал бы доступ к уже
        # выданным stream-токенам до истечения их отдельного TTL (до 6ч).
        if self.storage.is_blocked(origin) or not self.storage.origin_has_active_token(origin):
            del self._stream_tokens[stream_token]
            return None

        return origin

    def check_stream_access(self, stream_token: str, torrent_id: int) -> bool:
        """Используется Слоем 1 (http_server.py) и для /bridge/playlist, и
        для /bridge/segment — единая точка проверки scoped-токена."""
        return self._validate_stream_token(stream_token, torrent_id) is not None

    def get_segment_bytes(self, stream_token: str, torrent_id: int, torrent_name: str, file_index: int) -> bytes | None:
        origin = self._validate_stream_token(stream_token, torrent_id)
        if origin is None:
            return None
        self._check_ownership(origin, torrent_id)

        if not self.snark.is_file_ready(torrent_id, file_index, torrent_name):
            return None

        path = self.snark.get_segment_path(torrent_name, file_index)
        if not path.exists():
            return None
        return path.read_bytes()
        
    def resume_all_owned_torrents(self) -> None:
        """
        Вызывается при старте моста — форсирует раздачу/докачку всех торрентов,
        зарегистрированных за каким-либо origin в реестре владения. Записи о
        торрентах, которых больше физически нет в i2psnark (удалены вручную,
        либо остались от старых экспериментов) — автоматически вычищаются из
        реестра, чтобы не копить мусор и не печатать одну и ту же ошибку при
        каждом перезапуске.
        """
        import logging
        log = logging.getLogger(__name__)

        rows = self.storage.conn.execute(
            "SELECT DISTINCT torrent_id FROM torrent_ownership"
        ).fetchall()

        # Получаем список реально существующих id одним запросом — быстрее и
        # надёжнее, чем ловить исключение на каждый несуществующий id по отдельности
        existing_ids = {t["id"] for t in self.snark.rpc.torrent_get(fields=["id"])}

        for (torrent_id,) in rows:
            if torrent_id not in existing_ids:
                log.info(
                    "Торрент id=%s из реестра владения больше не существует в "
                    "i2psnark — удаляю устаревшую запись", torrent_id,
                )
                self.storage.unregister_torrent(torrent_id)
                continue

            try:
                self.snark.rpc.torrent_start_now(torrent_id)
            except Exception as e:
                log.warning(
                    "Не удалось возобновить торрент id=%s при старте: %s", torrent_id, e,
                )
