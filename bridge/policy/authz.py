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

from i18n import t
from snark import SnarkIntegration, VideoTorrentHandle
from snark.torrent_builder import UntrustedTorrentError

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
    """Действие отклонено — НЕ обязательно проблема с самим токеном/
    сопряжением (см. AuthenticationFailed ниже для этого случая). Сюда
    попадает: пользователь отклонил конкретное действие в confirm-режиме,
    ownership-мисматч, сайт отверг что-то на своей стороне и т.п. — во
    всех этих случаях токен по-прежнему валиден, повторная авторизация
    не нужна."""
    pass


class AuthenticationFailed(PermissionDenied):
    """Именно проблема с токеном/сопряжением — невалидный/отозванный
    токен, заблокированный origin. Единственный случай, когда клиенту
    имеет смысл стирать сохранённый токен и заново запускать сопряжение
    (см. transport/http_server.py — маппится в HTTP 401, а не 403, и
    site/static/*.js — клиент перезапускает пейринг только на 401)."""
    pass


class BridgePolicy:
    def __init__(
        self,
        storage: PolicyStorage | None = None,
        dialog: TkinterPairingDialog | None = None,
        snark: SnarkIntegration | None = None,
        # КРИТИЧНО (было исправлено): дефолт был Mode.SILENT — единственное
        # подтверждение пользователя во всём протоколе происходило один раз,
        # на этапе самого сопряжения. После этого react/comment/
        # studio_update/add_torrent/remove_torrent выполнялись ПОЛНОСТЬЮ
        # тихо для уже сопряжённого origin. В сочетании с тем, что
        # react/comment/studio_update — это, по сути, "подпишите что угодно
        # моим ключом канала", один клик "Разрешить" на сопряжении фактически
        # выдавал сайту бессрочное тихое право действовать от имени личности
        # пользователя и скачивать произвольные торренты. Mode.CONFIRM —
        # безопасный дефолт "по умолчанию спрашивать"; пользователь может
        # осознанно переключиться на SILENT в настройках моста для сайтов,
        # которым действительно доверяет.
        mode: Mode = Mode.CONFIRM,
    ):
        self.storage = storage or PolicyStorage()
        self.dialog = dialog or TkinterPairingDialog()
        self.pairing = PairingManager(self.storage, self.dialog)
        self.snark = snark or SnarkIntegration(
            storage_dir_provider=self.storage.get_snark_storage_dir,
            trackers=self.storage.get_trackers(),
            max_torrent_bytes=self.storage.get_max_video_torrent_bytes(),
            max_torrent_files=self.storage.get_max_video_torrent_files(),
            # Раньше не передавались — SnarkIntegration/RPCClient всегда
            # молча падали на хардкодный дефолт 127.0.0.1:8002, который
            # верен только для режима i2pd (см. get_snark_rpc_url в
            # storage.py). В режиме javai2p RPC-плагин смонтирован в
            # консоли Java I2P роутера (обычно 7657), и без явной передачи
            # этих URL мост не мог достучаться до i2psnark вообще.
            rpc_url=self.storage.get_snark_rpc_url(),
            web_url=self.storage.get_snark_web_url(),
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

        # Публикация теперь разбита на два HTTP-запроса с сайта (см.
        # start_publish/finish_publish ниже): сначала подтверждение +
        # выбор локального файла (это может делать только мост — у сайта
        # в браузере нет доступа к файловой системе), затем, уже отдельным
        # запросом, название/описание, которые пользователь заполняет
        # прямо на сайте. Между этими двумя запросами мост должен помнить,
        # какой файл был выбран для какого origin — publish-сессия и
        # хранит эту связку. Только в памяти (как и stream-токены) — это
        # не данные для восстановления между перезапусками моста, при
        # рестарте пользователь просто выбирает файл заново.
        self._publish_sessions: dict[str, dict] = {}
        self._publish_session_ttl = 30 * 60  # 30 минут — с запасом на заполнение формы на сайте

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
            raise AuthenticationFailed("Невалидный или отозванный токен")
        if self.storage.is_blocked(origin):
            raise AuthenticationFailed("Origin в блеклисте")
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

        self._confirm_if_needed(origin, t("confirm.add_video", video_id=video_id))

        try:
            handle = self.snark.add_video_for_playback(torrent_bytes, expected_torrent_name)
        except UntrustedTorrentError as e:
            # Сайт прислал .torrent, не похожий на то, что могло бы быть
            # опубликовано через ITubeP (см. torrent_builder.validate_video_torrent)
            # — явный отказ с понятной причиной, а не общий 500.
            raise PermissionDenied(f"Торрент отклонён: {e}")
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

    def set_quality(
        self, token: str, torrent_id: int, high_start: int, high_count: int,
    ) -> None:
        """
        Смена качества просмотра — зритель выбрал другое качество на сайте
        (см. player.js), сайт знает диапазон файлов этого качества внутри
        единого торрента видео (manifest["qualities"][i].file_start_index/
        file_count, см. snark/publisher.py) и присылает его сюда как есть.
        Не требует confirm-диалога — это не новое разрешение, а обычное
        действие уже авторизованного зрителя над УЖЕ добавленным (и уже
        разрешённым через add_torrent) торрентом, тот же уровень
        чувствительности, что и обычный seek.
        """
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)

        handle = self._handles.get(torrent_id)
        if handle is None:
            return  # торрент не активен в этом процессе — нечего перевешивать

        self.snark.set_quality_priority(handle, high_start, high_count)

    def get_progress(self, token: str, torrent_id: int) -> dict:
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)
        return self.snark.get_progress(torrent_id)

    def remove_torrent(self, token: str, torrent_id: int, delete_local_data: bool = False) -> None:
        origin = self._authenticate(token)
        self._check_ownership(origin, torrent_id)
        self._confirm_if_needed(origin, t("confirm.delete_torrent", torrent_id=torrent_id))

        self.snark.remove_video(torrent_id, delete_local_data)
        self.storage.unregister_torrent(torrent_id)
        self._handles.pop(torrent_id, None)
    
    @property
    def mode(self) -> Mode:
        return Mode(self.storage.get_setting("mode", Mode.SILENT.value))

    @mode.setter
    def mode(self, value: Mode):
        self.storage.set_setting("mode", value.value)
        
    def start_publish(self, token: str) -> dict:
        """
        Первый шаг публикации, целиком на стороне моста: подтверждение
        пользователем самого факта, что сайт просит опубликовать видео
        (это может подтвердить только пользователь, не сайт), создание
        канала при необходимости, и выбор ЛОКАЛЬНОГО видеофайла — у сайта
        в браузере нет и не должно быть доступа к файловой системе
        пользователя, поэтому выбор файла не может переехать на сайт.

        Название/описание сюда больше не входят — их пользователь
        заполняет прямо на сайте и присылает вторым запросом, см.
        finish_publish. Результат этого шага — publish_session_id,
        связывающий это конкретное открытие/выбор файла с конкретным
        origin, чтобы finish_publish не могла быть вызвана "из воздуха"
        с произвольным путём.
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

        # Необязательно — Cancel здесь ЛЕГИТИМЕН (в отличие от отмены выбора
        # видеофайла выше), просто публикуем без превью.
        thumbnail_path = publish_dialogs.choose_thumbnail_file()

        self._cleanup_expired_publish_sessions()

        session_id = secrets.token_urlsafe(24)
        self._publish_sessions[session_id] = {
            "origin": origin,
            "video_path": video_path,
            "thumbnail_path": thumbnail_path,
            "expires_at": time.monotonic() + self._publish_session_ttl,
        }

        return {
            "publish_session_id": session_id,
            # Имя файла — просто чтобы сайт мог показать пользователю "выбран
            # файл: <имя>" для обратной связи. Полный путь на сайт не уходит.
            "filename": Path(video_path).name,
            "thumbnail_filename": Path(thumbnail_path).name if thumbnail_path else None,
        }

    def finish_publish(
        self, token: str, publish_session_id: str, title: str, description: str, nsfw: bool,
        qualities: list[str] | None = None,
    ) -> dict:
        """
        Второй шаг: название/описание, заполненные пользователем на сайте,
        для файла, выбранного на шаге start_publish В ЭТОЙ ЖЕ сессии.
        Запускает сегментацию (ffmpeg) + сборку торрента + отправку
        манифеста на сайт (см. VideoPublisher.publish). nsfw — обязательная
        авторская отметка с формы публикации (сайт требует явный bool в
        манифесте и без него отклонит публикацию, см. site/app/main.py:
        publish_video), мост тут её не домысливает и не подставляет
        дефолт — просто передаёт то, что реально выбрал автор.

        qualities — список качеств (360p/480p/720p/1080p), отмеченных
        автором на форме публикации сайта (см. templates/publish.html,
        включая предупреждение о размере/времени докачки для качеств выше
        360p) — VideoPublisher.publish сам гарантирует, что 360p попадёт в
        список, даже если сюда пришёл пустой/None.
        """
        origin = self._authenticate(token)

        title = (title or "").strip()
        if not title:
            raise PermissionDenied("Название не указано")

        session = self._publish_sessions.get(publish_session_id)
        if session is None or time.monotonic() > session["expires_at"]:
            self._publish_sessions.pop(publish_session_id, None)
            raise PermissionDenied(
                "Сессия публикации не найдена или истекла — выберите файл заново"
            )
        # КРИТИЧНО: сессия привязана к origin, выполнившему start_publish —
        # без этой проверки любой другой сопряжённый сайт, узнав/угадав
        # чужой publish_session_id, мог бы опубликовать выбранный для
        # ПЕРВОГО сайта файл от имени того же канала, но с собственными
        # title/description на своём origin (site_base_url = origin, ниже).
        if session["origin"] != origin:
            raise PermissionDenied("Сессия публикации принадлежит другому сайту")

        # Одноразовая сессия: повторный finish_publish с тем же
        # publish_session_id не должен повторно запускать сегментацию и
        # заново публиковать тот же файл (например, при повторном клике
        # или повторе запроса из-за сетевого сбоя на стороне сайта).
        del self._publish_sessions[publish_session_id]

        channel = self._channel_identity
        if channel is None:
            # Не должно происходить в норме (start_publish уже создаёт
            # канал до выдачи publish_session_id), но на случай рестарта
            # моста между шагами — явная ошибка вместо AttributeError ниже.
            raise PermissionDenied("Канал не инициализирован — начните публикацию заново")

        publisher = VideoPublisher(
            self.snark, channel,
            http_proxy=self.storage.get_i2p_http_proxy(),
            max_thumbnail_bytes=self.storage.get_max_thumbnail_bytes(),
        )
        try:
            result = publisher.publish(
                video_path=Path(session["video_path"]),
                title=title,
                description=description or "",
                site_base_url=origin,  # публикуем на тот же сайт, что и запросил
                nsfw=nsfw,
                qualities=qualities,
                thumbnail_path=Path(session["thumbnail_path"]) if session.get("thumbnail_path") else None,
            )
        except PublishError as e:
            raise PermissionDenied(f"Ошибка публикации: {e}")

        return result

    def _cleanup_expired_publish_sessions(self) -> None:
        now = time.monotonic()
        expired = [sid for sid, s in self._publish_sessions.items() if now > s["expires_at"]]
        for sid in expired:
            del self._publish_sessions[sid]
        
    # --- "Мой канал" / студия / настройки моста, вызываемые из меню сайта ---

    def get_show_nsfw_preference(self) -> bool:
        """
        Для static/nsfw-sync.js на КАЖДОЙ странице сайта: намеренно БЕЗ
        _authenticate/токена — в отличие от прочих методов этого раздела,
        это не действие от имени канала и не чтение чего-либо привязанного
        к конкретному сопряжённому origin, а одно глобальное локальное
        предпочтение отображения ("показывать ли на сайтах то, что их
        авторы сами отметили NSFW"), одинаковое для любого сайта, к
        которому подключается этот мост. Требовать для него полноценное
        сопряжение означало бы навязчивый запрос кода подтверждения
        просто для того, чтобы корректно отрисовать первую же страницу.
        Слой 1 (http_server.py) всё равно ограничивает CORS только
        валидными .i2p/dev-origin, так что это не открытый наружу эндпоинт
        как таковой — просто без пары "токен ⇄ авторизованный origin".
        """
        return self.storage.get_show_nsfw()

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
        self._confirm_if_needed(origin, t("confirm.update_channel_studio"))

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
            # См. react_to_video выше — привязка подписи к конкретному сайту.
            # Особенно важно здесь: без этого чужой сайт мог бы получить
            # подпись под studio-обновлением (например, "спрятать все видео")
            # и применить её к НАСТОЯЩЕМУ каналу жертвы на другом сайте.
            "audience_origin": origin,
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
            # См. react_to_video выше — привязка подписи к конкретному сайту.
            # Здесь это важно и для приватности: без привязки чужой сайт мог
            # бы этим же вызовом заставить мост подписать read-запрос и
            # получить (переслав его на настоящий сайт от своего имени —
            # впрочем этого недостаточно, ответ идёт мосту, а не ему;
            # тем не менее сама подпись — чувствительный артефакт, который
            # не должен быть переносим) состояние студии, включая
            # unlisted/private видео, если бы смог подставить этот запрос
            # где-то ещё.
            "audience_origin": origin,
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

    def update_thumbnail(self, token: str, video_id: str) -> dict:
        """
        Смена превью УЖЕ опубликованного видео из студии — открывает выбор
        файла на мосте (тот же choose_thumbnail_file, что при публикации),
        сжимает под лимит сайта (тот же compress_thumbnail/лестница, что
        и при публикации, см. snark/thumbnail.py) и отправляет подписанный
        запрос на /api/channel/{id}/studio/thumbnail. В отличие от
        choose_thumbnail_file в start_publish, здесь отмена выбора файла —
        это ошибка (пользователь явно нажал "сменить превью"), а не штатный
        "публикуем без превью".
        """
        import time
        import hashlib
        import json
        import requests
        from snark.publisher import _requests_session_for, I2P_REQUEST_TIMEOUT_SECONDS
        from snark.thumbnail import compress_thumbnail, ThumbnailError

        origin = self._authenticate(token)
        self._confirm_if_needed(origin, t("confirm.change_video_thumbnail"))

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, PublishDialogs())
            self._channel_identity = channel

        image_path = PublishDialogs().choose_thumbnail_file()
        if not image_path:
            raise PermissionDenied("Файл не выбран")

        try:
            thumbnail_bytes = compress_thumbnail(Path(image_path), self.storage.get_max_thumbnail_bytes())
        except ThumbnailError as e:
            raise PermissionDenied(str(e))
        if thumbnail_bytes is None:
            raise PermissionDenied(
                "Не удалось сжать превью до предела, который принимает сайт — попробуйте другое изображение"
            )

        record = {
            "channel_id": channel.channel_id,
            "video_id": video_id,
            "thumbnail_sha256": hashlib.sha256(thumbnail_bytes).hexdigest(),
            "updated_at": str(time.time()),
            "audience_origin": origin,
        }
        record["signature"] = channel.sign(record)

        try:
            resp = _requests_session_for(origin, self.storage.get_i2p_http_proxy()).post(
                f"{origin.rstrip('/')}/api/channel/{channel.channel_id}/studio/thumbnail",
                data={"auth_json": json.dumps(record)},
                files={"thumbnail": ("thumbnail.webp", thumbnail_bytes, "image/webp")},
                timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PermissionDenied(f"Не удалось соединиться с сайтом для смены превью: {e}")
        if resp.status_code != 200:
            raise PermissionDenied(f"Сайт отклонил смену превью: {resp.status_code} {resp.text}")

        return resp.json()

    def update_video_details(
        self, token: str, video_id: str, title: str, description: str, access_level: str, nsfw: bool,
    ) -> dict:
        """
        Сохранение "Сведений о видео" (title/description/доступ/NSFW) со
        страницы /studio/video/{id} — тот же паттерн подписи, что
        studio_update/update_thumbnail, отдельный эндпоинт под отдельную
        кнопку "Сохранить" именно этой страницы (не смешивается с общим
        батч-сохранением списка студии). nsfw здесь — постфактум-правка
        авторской отметки (см. finish_publish/VideoPublisher.publish —
        там она обязательна при самой публикации), на случай если автор
        ошибся при заполнении формы публикации.
        """
        import time
        import requests
        from snark.publisher import _requests_session_for, I2P_REQUEST_TIMEOUT_SECONDS

        title = (title or "").strip()
        if not title:
            raise PermissionDenied("Название не указано")
        if access_level not in ("public", "unlisted", "private"):
            raise PermissionDenied(f"Некорректный уровень доступа: {access_level}")

        origin = self._authenticate(token)
        self._confirm_if_needed(origin, t("confirm.edit_video_details", video_id=video_id))

        channel = self._channel_identity
        if channel is None:
            channel = get_or_create_channel(self.storage, PublishDialogs())
            self._channel_identity = channel

        record = {
            "channel_id": channel.channel_id,
            "video_id": video_id,
            "title": title,
            "description": description or "",
            "access_level": access_level,
            "nsfw": bool(nsfw),
            "updated_at": str(time.time()),
            "audience_origin": origin,
        }
        record["signature"] = channel.sign(record)

        try:
            resp = _requests_session_for(origin, self.storage.get_i2p_http_proxy()).post(
                f"{origin.rstrip('/')}/api/channel/{channel.channel_id}/studio/video",
                json=record, timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PermissionDenied(f"Не удалось соединиться с сайтом для сохранения сведений о видео: {e}")
        if resp.status_code != 200:
            raise PermissionDenied(f"Сайт отклонил сохранение: {resp.status_code} {resp.text}")

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
        self._confirm_if_needed(origin, t("confirm.vote_video", video_id=video_id))

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
            # КРИТИЧНО: audience_origin — сайт, ДЛЯ которого мы подписываем
            # эту запись (тот же origin, на который она будет отправлена
            # ниже). Входит в подписываемые данные (canonical_json), поэтому
            # сайт-получатель может отвергнуть запись, подписанную "для"
            # другого сайта, и её нельзя реплеить на другой сайт без
            # пересборки подписи — то есть без ключа канала. Без этого поля
            # ЛЮБОЙ сопряжённый сайт мог бы через этот же вызов получить от
            # моста валидную подпись голоса и переиспользовать её напрямую
            # против настоящего сайта жертвы.
            "audience_origin": origin,
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
        self._confirm_if_needed(origin, t("confirm.publish_comment"))

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
            # См. пояснение к audience_origin в react_to_video выше — тот же
            # смысл: привязка подписи к конкретному сайту-адресату.
            "audience_origin": origin,
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

        # Реальное имя файла — из ответа i2psnark (см. get_progress), а не
        # восстановленное по индексу шаблоном: имя сегмента теперь зависит
        # от качества ("segment_NNNN_{quality}.ts", см.
        # bridge/snark/publisher.py), и один и тот же file_index в разных
        # видео (и даже в одном видео с несколькими качествами) относится к
        # файлам с разными именами — гадать по индексу больше нельзя.
        progress = self.snark.get_progress(torrent_id)
        files = progress.get("files", [])
        if file_index >= len(files):
            return None
        segment_filename = files[file_index]["name"]

        path = self.snark.get_segment_path(torrent_name, segment_filename)
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
