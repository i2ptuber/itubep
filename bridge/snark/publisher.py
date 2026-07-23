"""
publisher.py — сегментация видео (ffmpeg) + сборка торрента + отправка
манифеста на сайт. Часть Слоя 3, вызывается Слоем 2.

ВАЖНО: torrent_name (имя торрента в i2psnark) НЕ равен video_id. video_id
вычисляется из манифеста (который включает info_hash торрента), поэтому не может
одновременно быть именем ВНУТРИ этого же торрента (циклическая зависимость).
torrent_name вычисляется из содержимого сегментов независимо и хранится в
манифесте отдельным полем "torrent_name" рядом с "torrent_infohash".
"""

from __future__ import annotations

import subprocess
import time
import hashlib
import json
import shutil
import requests
from pathlib import Path
from urllib.parse import urlparse

from .torrent_builder import TorrentFile, build_torrent_with_hash
from .integration import SnarkIntegration
from policy.crypto_utils import ChannelIdentity, canonical_json_for_id
from .integration import VideoTorrentHandle


class PublishError(Exception):
    pass


# I2P — сеть с высокой и переменной задержкой (туннели строятся не мгновенно,
# у публикации ещё и файлы приличного размера в теле запроса) — дефолтный
# requests-таймаут "без ограничения" на практике означает "зависнет навсегда",
# если сайт недоступен, а короткий таймаут clearnet-масштаба (типа 10с)
# гарантированно будет ложно рваться на живых I2P-запросах.
I2P_REQUEST_TIMEOUT_SECONDS = 180.0


def _requests_session_for(url: str, http_proxy: str | None) -> requests.Session:
    """
    Возвращает requests.Session, замаршрутизированную через HTTP-прокси I2P
    роутера — но ТОЛЬКО если url это .i2p-адрес. requests не умеет резолвить
    .i2p-домены напрямую (это не DNS), их обязательно нужно вести через
    HTTP-прокси роутера (i2pd/Java I2P слушает его обычно на 127.0.0.1:4444).

    Для localhost/127.0.0.1 (локальное тестирование, как раньше) прокси не
    используется — иначе локальная разработка сломалась бы, если у человека
    вообще нет запущенного I2P-роутера на машине.
    """
    session = requests.Session()
    host = urlparse(url).hostname or ""
    if host.endswith(".i2p"):
        if not http_proxy:
            raise PublishError(
                f"Адрес сайта ({url}) — .i2p-домен, но HTTP-прокси I2P не "
                f"настроен (см. настройки моста). Без него мост не может "
                f"достучаться до сайта."
            )
        session.proxies = {"http": http_proxy, "https": http_proxy}
    return session


def segment_video_ffmpeg(
    input_path: Path, output_dir: Path, segment_seconds: int = 3,
) -> tuple[list[Path], list[float]]:
    """
    Использует нативный HLS-мьюксер ffmpeg (-f hls), генерирующий MPEG-TS
    сегменты (самодостаточные, не требуют init-сегмента — в отличие от
    fMP4-фрагментов, которые мы пробовали раньше и которые зависят друг от
    друга через общий moov-блок в первом сегменте).

    Возвращает (список путей сегментов, список их реальных длительностей —
    последний сегмент почти всегда короче остальных, точную длительность
    ffmpeg сам пишет в сгенерированный playlist.m3u8, откуда мы её и берём).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = str(output_dir / "segment_%04d.ts")
    playlist_path = output_dir / "playlist.m3u8"

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-map", "0",
        "-vf", "scale=-2:360",  # реальное масштабирование до 360p по высоте
        "-c:v", "libx264", "-b:v", "600k", "-maxrate", "700k", "-bufsize", "1200k",
        "-c:a", "aac", "-b:a", "96k",
        "-f", "hls", "-hls_time", str(segment_seconds),
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", segment_pattern,
        str(playlist_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise PublishError(f"ffmpeg failed: {result.stderr[-2000:]}")

    segments = sorted(output_dir.glob("segment_*.ts"))
    if not segments:
        raise PublishError("ffmpeg не создал ни одного .ts сегмента")

    durations = _parse_m3u8_durations(playlist_path)
    if len(durations) != len(segments):
        raise PublishError(
            f"Число сегментов ({len(segments)}) не совпадает с числом "
            f"EXTINF-записей в playlist.m3u8 ({len(durations)})"
        )

    return segments, durations


def _parse_m3u8_durations(playlist_path: Path) -> list[float]:
    """Достаёт длительности из строк EXTINF:X.XXX, в порядке следования."""
    durations = []
    for line in playlist_path.read_text().splitlines():
        if line.startswith("#EXTINF:"):
            value = line[len("#EXTINF:"):].rstrip(",")
            durations.append(float(value))
    return durations


def compute_content_id(segments: list[Path]) -> str:
    """
    Детерминированный идентификатор торрента, вычисляемый ТОЛЬКО из содержимого
    файлов сегментов — не зависит от video_id/манифеста, поэтому вычисляется
    один раз, без циклических зависимостей.
    """
    hasher = hashlib.sha256()
    for seg in segments:
        with open(seg, "rb") as f:
            hasher.update(hashlib.sha256(f.read()).digest())
    return hasher.hexdigest()


class VideoPublisher:
    def __init__(self, snark: SnarkIntegration, channel: ChannelIdentity, http_proxy: str | None = None):
        self.snark = snark
        self.channel = channel
        self.http_proxy = http_proxy

    def publish(
        self,
        video_path: Path,
        title: str,
        description: str,
        site_base_url: str,
        segment_seconds: int = 3,
        work_dir: Path | None = None,
    ) -> dict:
        work_dir = work_dir or Path.home() / ".cache" / "itubep-bridge" / "publish" / str(int(time.time()))

        self._ensure_channel_registered(site_base_url)

        segments, durations = segment_video_ffmpeg(video_path, work_dir, segment_seconds)

        torrent_name = compute_content_id(segments)

        torrent_files = [TorrentFile(path=p, torrent_path=[p.name]) for p in segments]
        torrent_bytes, info_hash = build_torrent_with_hash(
            name=torrent_name, files=torrent_files, trackers=self.snark.trackers,
        )

        manifest_draft = {
            "channel_id": self.channel.channel_id,
            "title": title,
            "description": description,
            "duration": round(sum(durations), 3),
            "qualities": [{
                "label": "360p",
                "torrent_infohash": info_hash,
                "torrent_name": torrent_name,
                "segment_durations": durations,
            }],
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        video_id = hashlib.sha256(canonical_json_for_id(manifest_draft)).hexdigest()

        # КРИТИЧНО: копируем уже готовые сегменты в storage-директорию i2psnark
        # ПОД ТЕМ ЖЕ ИМЕНЕМ, что и торрент — иначе i2psnark будет пытаться
        # СКАЧАТЬ данные, которые у автора уже есть локально, и раздача
        # никогда не начнётся (0% докачано, нет других сидов)
        storage_dir = Path(self.snark.storage_dir_provider()) / torrent_name
        storage_dir.mkdir(parents=True, exist_ok=True)
        for seg in segments:
            shutil.copy2(seg, storage_dir / seg.name)

        added = self.snark.rpc.torrent_add_bytes(torrent_bytes, paused=True)
        torrent_id = added["id"]

        # Просим i2psnark проверить хеши уже лежащих на месте файлов — это
        # переведёт торрент из "нужно скачать" в "уже скачано, можно раздавать"
        self.snark.rpc.torrent_verify(torrent_id)
        self.snark.rpc.wait_for_status(torrent_id, target_status=6, timeout_seconds=30.0)  # 6 = seeding

        # enableInOrder НЕ нужен раздающей стороне — эта настройка управляет
        # порядком ДОКАЧКИ, а у автора уже всё скачано (100%, seeding). Форма
        # приоритезации файлов у i2psnark попросту не рендерится для полностью
        # завершённых торрентов, поэтому попытка её вызвать здесь была ошибкой.
        self.snark.rpc.torrent_start_now(torrent_id)

        manifest_draft["video_id"] = video_id
        manifest_draft["signature"] = self.channel.sign(manifest_draft)

        try:
            resp = _requests_session_for(site_base_url, self.http_proxy).post(
                f"{site_base_url.rstrip('/')}/api/video/publish",
                data={"manifest_json": json.dumps(manifest_draft)},
                files={"torrents": (f"{torrent_name}.torrent", torrent_bytes, "application/x-bittorrent")},
                timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PublishError(
                f"Не удалось соединиться с сайтом для публикации видео ({site_base_url}): {e}"
            )
        if resp.status_code != 200:
            raise PublishError(f"Сайт отклонил публикацию: {resp.status_code} {resp.text}")

        return {"video_id": video_id, "torrent_id": torrent_id, "site_response": resp.json()}

    def _ensure_channel_registered(self, site_base_url: str) -> None:
        channel_record = {
            "channel_id": self.channel.channel_id,
            "public_key": self.channel.public_key_b64,
            "display_name": self.channel.display_name,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "latest_videos": [],
            }
        channel_record["signature"] = self.channel.sign(channel_record)

        try:
            resp = _requests_session_for(site_base_url, self.http_proxy).post(
                f"{site_base_url.rstrip('/')}/api/channel/register", json=channel_record,
                timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PublishError(
                f"Не удалось соединиться с сайтом для регистрации канала ({site_base_url}): {e}"
            )

        if resp.status_code == 200:
            return
        if resp.status_code == 409:
            return

        raise PublishError(
            f"Не удалось зарегистрировать канал на сайте: {resp.status_code} {resp.text}"
        )
