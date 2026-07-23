"""
integration.py — публичный интерфейс Слоя 3 (интеграция с i2psnark).

Слой 2 (авторизация) обращается ТОЛЬКО к методам SnarkIntegration, не к
RPCClient/I2PSnarkWebClient напрямую — так сохраняется чёткая граница между
"что разрешено" (Слой 2) и "как это физически сделать" (Слой 3).

Этот модуль ничего не знает про origin/токены/pairing — он выполняет команды,
доверяя, что вызывающий код уже всё проверил.
"""

from __future__ import annotations

import logging
import re
import time
import os
from dataclasses import dataclass
from pathlib import Path

from .rpc_client import RPCClient
from .torrent_builder import TorrentFile
from .web_client import I2PSnarkWebClient, TorrentMustBeStoppedError

log = logging.getLogger(__name__)

# Статус-коды Transmission RPC (см. rpc-spec.txt) — используемые нами значения
STATUS_STOPPED = 0
STATUS_SEED_WAIT = 5
STATUS_SEEDING = 6


@dataclass
class VideoTorrentHandle:
    """То, что интеграция возвращает после публикации/добавления видео."""
    torrent_id: int
    torrent_name: str  # для web_client (директория в i2psnark)
    info_hash: str
    total_files: int


class SnarkIntegration:
    def __init__(
        self,
        rpc_url: str = "http://127.0.0.1:8002/transmission/rpc",
        web_url: str = "http://127.0.0.1:8002/i2psnark/",
        stop_wait_timeout: float = 10.0,
        storage_dir_provider=None,
        trackers: list[str] | None = None,
    ):
        self.rpc = RPCClient(rpc_url)
        self.web = I2PSnarkWebClient(web_url)
        self.stop_wait_timeout = stop_wait_timeout
        # Список announce-URL живых I2P-трекеров, добавляемых в каждый
        # публикуемый .torrent (см. torrent_builder.build_torrent_with_hash).
        # Задаётся из настроек моста (обычно — копия того, что уже настроено
        # в самом i2psnark на configure-странице, т.к. это заведомо живые
        # трекеры для данного роутера).
        self.trackers = trackers or []
        # Текущее применённое окно приоритета и метка времени последней
        # реальной репрайоритизации — на torrent_id, нужно чтобы не гонять
        # stop/apply/start i2psnark (обрывает все текущие BT-соединения)
        # при каждом seek-событии, если по факту ничего менять не нужно.
        self._priority_window: dict[int, tuple[int, int]] = {}
        self._last_reprioritize_at: dict[int, float] = {}
        # storage_dir_provider — функция без аргументов, возвращающая путь к
        # storage-директории i2psnark (нужна publisher.py). Задаётся снаружи,
        # т.к. SnarkIntegration сама по себе не хранит настройки моста.
        self.storage_dir_provider = storage_dir_provider or (
            lambda: os.path.expanduser("~/i2psnark-run/i2psnark")
        )

    # --- Публикация (издатель) ---
    #
    # Собственно сборка+публикация торрента живёт в snark/publisher.py
    # (VideoPublisher.publish) — там video_id (sha256 манифеста) и
    # torrent_name (sha256 СОДЕРЖИМОГО сегментов, compute_content_id)
    # намеренно вычисляются НЕЗАВИСИМО друг от друга. Здесь раньше был
    # метод SnarkIntegration.publish_video(), оставшийся от версии ДО этого
    # разделения — в нём torrent_name буквально присваивался равным
    # video_id, что противоречит текущей модели данных. Метод нигде не
    # вызывался (мёртвый код), но был опасен именно потому, что выглядел
    # как легитимный API и мог быть по ошибке использован в будущем,
    # молча воспроизведя старый баг — поэтому удалён целиком, а не оставлен
    # "на всякий случай". Актуальный путь публикации — всегда через
    # publisher.py.

    # video_id ОБЯЗАН быть hex-хешем (sha256 манифеста, см. план) — используется
    # как имя торрента в i2psnark (директория данных + URL веб-интерфейса), поэтому
    # должен быть глобально уникален. Человекочитаемое название видео (title) сюда
    # не подставлять никогда — коллизия названий двух разных видео/каналов иначе
    # приведёт к конфликту данных на диске одного пользователя.
    #
    # (Тот же формат-паттерн используется и для проверки torrent_name в
    # add_video_for_playback ниже — там это не в буквальном смысле video_id,
    # а просто тот же самый "выглядит как sha256-хеш" критерий формата.)
    _VIDEO_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")  # sha256 hex = 64 символа

    @classmethod
    def _validate_video_id(cls, video_id: str) -> None:
        if not cls._VIDEO_ID_PATTERN.match(video_id):
            raise ValueError(
                f"Ожидался sha256-хеш в hex (64 символа [0-9a-f]), "
                f"получено: {video_id!r}. Не используйте название видео/канала "
                f"напрямую — так гарантируется уникальность торрентов в i2psnark."
            )

    # --- Добавление для скачивания (зритель) ---

    def add_video_for_playback(
        self, torrent_bytes: bytes, expected_torrent_name: str, enable_sequential: bool = True,
    ) -> VideoTorrentHandle:
        self._validate_video_id(expected_torrent_name)
        added = self.rpc.torrent_add_bytes(torrent_bytes, paused=True)
        torrent_id = added["id"]
        torrent_name = expected_torrent_name

        # Проверяем реальное состояние — если торрент уже полностью скачан
        # (например, мы сами его публиковали и он уже seeding), форма
        # приоритезации файлов у i2psnark для него не рендерится вообще,
        # и enableInOrder там не нужен и не применим
        torrents = self.rpc.torrent_get(ids=[torrent_id], fields=["id", "percentDone", "status", "files"])
        already_complete = bool(torrents) and torrents[0].get("percentDone", 0) >= 1.0
        total_files = len(torrents[0].get("files", [])) if torrents else 0

        handle = VideoTorrentHandle(
            torrent_id=torrent_id,
            torrent_name=torrent_name,
            info_hash=added.get("hashString", ""),
            total_files=total_files,
        )

        if enable_sequential and not already_complete:
            self._toggle_sequential(handle, enabled=True)

        self.rpc.torrent_start(torrent_id)
        return handle

    # --- Приоритет при seek ---

    def set_seek_priority(
        self,
        handle: VideoTorrentHandle,
        target_segment_index: int,
        window_ahead: int = 5,
        window_behind: int = 1,
        min_reprioritize_interval: float = 4.0,
    ) -> None:
        """
        TODO(seek-priority): временно ОТКЛЮЧЕНО.

        Единственный способ поменять приоритет файла у i2psnark — через
        web_client.set_file_priorities(), а он ТРЕБУЕТ остановленного
        торрента (см. web_client.py) и сам i2psnark при этом рвёт все
        текущие BT-соединения на stop и заново устанавливает их на start.
        На практике (см. обсуждение) это оказалось хуже, чем просто ждать
        естественной докачки по enableInOrder — каждый seek заново кладёт
        время на переустановку соединений с сидами, которое может быть
        сравнимо или больше выигрыша от форсированного приоритета.

        Раньше здесь было частичное смягчение (пропуск повторной
        репрайоритизации, если сегмент уже докачан / уже в текущем окне /
        прошло меньше min_reprioritize_interval) — оставлено в истории
        коммитов, но раз проблема осталась ощутимой даже с этим смягчением,
        решили отключить функцию целиком, а не тюнить константы дальше.

        Возможные направления на будущее, если возвращаться к этой задаче:
          - собственная реализация BT-клиента (или патч i2psnark), которая
            умеет менять приоритет "на лету" без разрыва соединений;
          - переход на меньшие сегменты + узкое sequential-окно, чтобы сама
            естественная докачка по порядку достаточно быстро добиралась до
            места перемотки без форсирования;
          - показ пользователю честного "буферизируется" вместо попытки
            форсировать скачку через приоритеты.

        Пока что — просто ничего не делаем, HLS.js будет ждать сегмент по
        мере естественной докачки (через enableInOrder), без форсирования.
        """
        return

    # --- Служебное ---

    def _toggle_sequential(self, handle: VideoTorrentHandle, enabled: bool) -> None:
        self._stop_apply_start(
            handle.torrent_id,
            lambda: self.web.set_in_order(handle.torrent_name, enabled),
        )

    def _stop_apply_start(self, torrent_id: int, action, max_attempts: int = 5) -> None:
        """
        Общий шаблон: остановить торрент, дождаться остановки, выполнить
        действие над веб-интерфейсом (требующее остановленного торрента),
        запустить обратно. Используется и для приоритета, и для inOrder.

        Свежедобавленный торрент может не сразу перейти в состояние, которое
        веб-интерфейс i2psnark считает "действительно остановленным" (расходится
        с RPC-статусом) — поэтому повторяем несколько раз с нарастающей паузой,
        а не один раз.
        """
        self.rpc.torrent_stop(torrent_id)
        self.rpc.wait_for_status(
            torrent_id, STATUS_STOPPED, timeout_seconds=self.stop_wait_timeout,
        )

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                action()
                last_error = None
                break
            except TorrentMustBeStoppedError as e:
                last_error = e
                wait_seconds = min(1.0 * attempt, 5.0)
                log.warning(
                    "Гонка состояний при apply (попытка %d/%d), жду %.1fс и повторяю",
                    attempt, max_attempts, wait_seconds,
                )
                time.sleep(wait_seconds)
                self.rpc.torrent_stop(torrent_id)
                self.rpc.wait_for_status(
                    torrent_id, STATUS_STOPPED, timeout_seconds=self.stop_wait_timeout,
                )

        self.rpc.torrent_start_now(torrent_id)

        if last_error is not None:
            raise last_error

    def get_progress(self, torrent_id: int) -> dict:
        """Прогресс докачки — для UI/статистики (Слой 1 сможет это транслировать)."""
        torrents = self.rpc.torrent_get(ids=[torrent_id])
        if not torrents:
            return {}
        t = torrents[0]
        return {
            "name": t.get("name"),
            "percent_done": t.get("percentDone", 0.0),
            "rate_download": t.get("rateDownload", 0),
            "rate_upload": t.get("rateUpload", 0),
            "peers_connected": t.get("peersConnected", 0),
            "files": [
                {
                    "name": f.get("name"),
                    "bytes_completed": fs.get("bytesCompleted", 0),
                    "length": f.get("length"),
                }
                for f, fs in zip(t.get("files", []), t.get("fileStats", []))
            ],
        }

    def remove_video(self, torrent_id: int, delete_local_data: bool = False) -> None:
        self.rpc.torrent_remove(torrent_id, delete_local_data=delete_local_data)
        self._priority_window.pop(torrent_id, None)
        self._last_reprioritize_at.pop(torrent_id, None)

    def verify_video(self, torrent_id: int) -> None:
        self.rpc.torrent_verify(torrent_id)
        
    def is_file_ready(self, torrent_id: int, file_index: int, torrent_name: str | None = None) -> bool:
        progress = self.get_progress(torrent_id)
        files = progress.get("files", [])
        if file_index >= len(files):
            return False
        f = files[file_index]
        if not (f["bytes_completed"] >= f["length"] and f["length"] > 0):
            return False

        # Дополнительная защита от гонки: RPC мог отчитаться о завершении
        # чуть раньше, чем данные реально сброшены на диск. Сверяем реальный
        # размер файла, если известно имя торрента.
        if torrent_name is not None:
            path = self.get_segment_path(torrent_name, file_index)
            if not path.exists() or path.stat().st_size != f["length"]:
                return False

        return True

    def get_segment_path(self, torrent_name: str, file_index: int) -> Path:
        storage_dir = Path(self.storage_dir_provider()) / torrent_name
        return storage_dir / f"segment_{file_index:04d}.ts"
