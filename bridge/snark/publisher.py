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

import logging
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
from .thumbnail import compress_thumbnail, ThumbnailError
from policy.crypto_utils import ChannelIdentity, canonical_json_for_id
from policy.origin_validation import is_i2p_host, _DEV_HOSTS
from .integration import VideoTorrentHandle

log = logging.getLogger(__name__)


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

    Для localhost/127.0.0.1 (локальное тестирование) прокси не используется —
    иначе локальная разработка сломалась бы, если у человека вообще нет
    запущенного I2P-роутера на машине.

    КРИТИЧНО (было исправлено): для ЛЮБОГО другого хоста — то есть НЕ .i2p
    и НЕ localhost/127.0.0.1 — раньше запрос уходил напрямую, в обход
    I2P-прокси, с реальным IP-адресом машины пользователя в качестве
    источника соединения. Это деанонимизировало пользователя, если origin
    (прошедший валидацию сопряжения или нет — этот модуль не должен на неё
    полагаться) вдруг оказывался внешним clearnet-хостом. Теперь для любого
    хоста вне allowlist'а (.i2p / localhost) мы ОТКАЗЫВАЕМ в запросе, а не
    отправляем его напрямую — это единственный безопасный дефолт для сети,
    само существование которой основано на анонимности.
    """
    session = requests.Session()
    host = (urlparse(url).hostname or "").lower()

    if is_i2p_host(host):
        if not http_proxy:
            raise PublishError(
                f"Адрес сайта ({url}) — .i2p-домен, но HTTP-прокси I2P не "
                f"настроен (см. настройки моста). Без него мост не может "
                f"достучаться до сайта."
            )
        session.proxies = {"http": http_proxy, "https": http_proxy}
        return session

    if host in _DEV_HOSTS:
        return session

    raise PublishError(
        f"Отказ отправлять запрос на {url!r}: это не .i2p-адрес и не "
        f"localhost. Отправка напрямую раскрыла бы реальный IP-адрес "
        f"пользователя в обход I2P — такой запрос никогда не выполняется, "
        f"независимо от того, как сайт был сопряжён с мостом."
    )


# Профили кодирования по качеству. Качества выше 360p дают заметно больше
# байт на диске и в сети I2P (которая заметно медленнее clearnet) — сайт
# обязан показать пользователю предупреждение об этом ПЕРЕД выбором качеств
# для публикации (см. templates/publish.html), сам мост это предупреждение
# не показывает повторно, просто честно кодирует то, что попросили.
#
# Кодирование — CRF (постоянное качество), а не фиксированный -b:v: при
# фиксированном битрейте кодировщик тратит одинаковое число бит что на
# статичную сцену, что на сложную, и на практике сильно раздувает файл на
# простом контенте, где столько бит объективно не нужно. crf позволяет
# кодировщику самому решать, сколько бит нужно КАЖДОМУ кадру для заданного
# уровня качества — тот же визуальный результат заметно меньшим числом байт.
# maxrate/bufsize оставлены только как потолок (VBV) на случай особо
# сложных сцен, не как целевой битрейт. preset "slow" — дороже по времени
# кодирования на стороне автора, но даёт заметно лучшее сжатие на бит, чем
# дефолтный "medium" — при разовой публикации это всегда выгодный обмен,
# учитывая насколько дороже избыточные байты обходятся при скачивании по I2P.
QUALITY_PROFILES: dict[str, dict] = {
    "360p":  {"height": 360,  "crf": 26, "maxrate": "500k",  "bufsize": "1000k", "a_bitrate": "64k"},
    "480p":  {"height": 480,  "crf": 25, "maxrate": "800k",  "bufsize": "1600k", "a_bitrate": "96k"},
    "720p":  {"height": 720,  "crf": 24, "maxrate": "1500k", "bufsize": "3000k", "a_bitrate": "128k"},
    "1080p": {"height": 1080, "crf": 23, "maxrate": "2800k", "bufsize": "5600k", "a_bitrate": "128k"},
}
# Канонический порядок качеств внутри единого торрента — от самого лёгкого
# к самому тяжёлому. Порядок важен: file_start_index/file_count в манифесте
# (см. VideoPublisher.publish) зависят от того, в каком порядке блоки
# сегментов разных качеств уложены в общий список файлов торрента, а
# зритель по умолчанию должен получать САМОЕ лёгкое качество (360p) —
# см. player.js:initPlayer, который берёт qualities[0].
QUALITY_ORDER = ["360p", "480p", "720p", "1080p"]

# 360p — обязательный минимум для КАЖДОЙ публикации, независимо от того, что
# выбрал автор на форме сайта: это единственное качество, которое достаточно
# лёгкое, чтобы худший случай (зритель без выбора/с нестабильной I2P-сетью)
# всё равно мог что-то посмотреть без долгого ожидания и лишнего трафика.
MANDATORY_QUALITY = "360p"


def segment_video_ffmpeg(
    input_path: Path, output_dir: Path, quality: str, segment_seconds: int = 3,
) -> tuple[list[Path], list[float]]:
    """
    Кодирует input_path в конкретное качество (см. QUALITY_PROFILES) и
    режет результат нативным HLS-мьюксером ffmpeg (-f hls) на MPEG-TS
    сегменты (самодостаточные, не требуют init-сегмента — в отличие от
    fMP4-фрагментов, которые мы пробовали раньше и которые зависят друг от
    друга через общий moov-блок в первом сегменте).

    Имя сегмента включает суффикс качества ("segment_0000_720p.ts") —
    это позволяет сегментам всех качеств одного видео сосуществовать в
    одной директории (и позже — в одном multi-file торренте, см.
    VideoPublisher.publish) без коллизий по имени, а заодно это ЕДИНСТВЕННЫЙ
    формат имени, который проходит проверку torrent_builder.validate_video_torrent
    на стороне зрителя.

    Возвращает (список путей сегментов, список их реальных длительностей —
    последний сегмент почти всегда короче остальных, точную длительность
    ffmpeg сам пишет в сгенерированный playlist, откуда мы её и берём).
    """
    if quality not in QUALITY_PROFILES:
        raise PublishError(
            f"Неизвестное качество {quality!r}, ожидалось одно из "
            f"{sorted(QUALITY_PROFILES)}"
        )
    profile = QUALITY_PROFILES[quality]

    output_dir.mkdir(parents=True, exist_ok=True)
    segment_pattern = str(output_dir / f"segment_%04d_{quality}.ts")
    playlist_path = output_dir / f"playlist_{quality}.m3u8"

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-map", "0",
        "-vf", f"scale=-2:{profile['height']}",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(profile["crf"]),
        "-maxrate", profile["maxrate"], "-bufsize", profile["bufsize"],
        "-c:a", "aac", "-b:a", profile["a_bitrate"],
        "-f", "hls", "-hls_time", str(segment_seconds),
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", segment_pattern,
        str(playlist_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise PublishError(f"ffmpeg failed ({quality}): {result.stderr[-2000:]}")

    # Глоб ограничен ЭТИМ качеством — иначе при повторных вызовах для
    # разных качеств в тот же output_dir подхватили бы чужие сегменты.
    segments = sorted(output_dir.glob(f"segment_*_{quality}.ts"))
    if not segments:
        raise PublishError(f"ffmpeg не создал ни одного .ts сегмента ({quality})")

    durations = _parse_m3u8_durations(playlist_path)
    if len(durations) != len(segments):
        raise PublishError(
            f"Число сегментов ({len(segments)}) не совпадает с числом "
            f"EXTINF-записей в playlist_{quality}.m3u8 ({len(durations)})"
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
    def __init__(
        self,
        snark: SnarkIntegration,
        channel: ChannelIdentity,
        http_proxy: str | None = None,
        max_thumbnail_bytes: int = 100 * 1024,
    ):
        self.snark = snark
        self.channel = channel
        self.http_proxy = http_proxy
        self.max_thumbnail_bytes = max_thumbnail_bytes

    def publish(
        self,
        video_path: Path,
        title: str,
        description: str,
        site_base_url: str,
        nsfw: bool,
        qualities: list[str] | None = None,
        segment_seconds: int = 3,
        work_dir: Path | None = None,
        thumbnail_path: Path | None = None,
    ) -> dict:
        """
        qualities — список качеств, выбранных автором на форме публикации
        сайта (см. templates/publish.html), например ["360p", "720p"].
        MANDATORY_QUALITY (360p) всегда включается, даже если автор его не
        выбирал — единственный безопасный минимум для нестабильной I2P-сети.

        ВАЖНО (см. обсуждение с автором проекта): раньше у каждого качества
        был свой независимый .torrent — переключение качества при просмотре
        означало бы добавление ВТОРОГО торрента, то есть двойную докачку.
        Теперь сегменты ВСЕХ выбранных качеств складываются в ОДИН
        multi-file торрент (см. build_torrent_with_hash ниже) — все качества
        качаются и раздаются одновременно одним и тем же торрентом, а выбор
        качества зрителем — это только смена приоритета файлов внутри него
        (high — для сегментов выбранного качества, normal — для остальных,
        см. snark/integration.py:SnarkIntegration.set_quality_priority),
        БЕЗ отдельного торрента на каждое качество и без остановки докачки
        остальных качеств.
        """
        work_dir = work_dir or Path.home() / ".cache" / "itubep-bridge" / "publish" / str(int(time.time()))

        requested = set(qualities or [])
        requested.add(MANDATORY_QUALITY)
        unknown = requested - set(QUALITY_PROFILES)
        if unknown:
            raise PublishError(f"Неизвестные качества: {sorted(unknown)}")
        # Канонический порядок — от лёгкого к тяжёлому (см. QUALITY_ORDER) —
        # так file_start_index в манифесте растёт предсказуемо, а зритель по
        # умолчанию (qualities[0] на стороне player.js) получает 360p.
        ordered_qualities = [q for q in QUALITY_ORDER if q in requested]

        self._ensure_channel_registered(site_base_url)

        # Кодируем и режем на сегменты КАЖДОЕ выбранное качество отдельно —
        # они пишутся в один и тот же work_dir, но с именами файлов,
        # включающими суффикс качества (см. segment_video_ffmpeg), поэтому
        # не конфликтуют между собой.
        quality_segments: dict[str, list[Path]] = {}
        quality_durations: dict[str, list[float]] = {}
        for q in ordered_qualities:
            segs, durs = segment_video_ffmpeg(video_path, work_dir, q, segment_seconds)
            quality_segments[q] = segs
            quality_durations[q] = durs

        # Единый список файлов торрента — блоками по качеству, в
        # ordered_qualities-порядке. file_start_index/file_count каждого
        # качества (см. manifest_draft ниже) — это просто смещение и длина
        # его блока в этом списке.
        all_segments: list[Path] = []
        quality_ranges: dict[str, tuple[int, int]] = {}
        for q in ordered_qualities:
            start = len(all_segments)
            all_segments.extend(quality_segments[q])
            quality_ranges[q] = (start, len(quality_segments[q]))

        torrent_name = compute_content_id(all_segments)

        torrent_files = [TorrentFile(path=p, torrent_path=[p.name]) for p in all_segments]
        torrent_bytes, info_hash = build_torrent_with_hash(
            name=torrent_name, files=torrent_files, trackers=self.snark.trackers,
        )

        # Превью — необязательно, и в отличие от самого видео (которое
        # ходит только по BitTorrent между зрителями) байты превью реально
        # ложатся на диск САЙТА, поэтому сжимаем агрессивно, под лимит,
        # который задаёт сайт (self.max_thumbnail_bytes — см. обсуждение
        # констант и PolicyStorage.get_max_thumbnail_bytes). Если картинку
        # не удалось уместить ни одной из ступеней сжатия — публикуем без
        # превью, а не превышаем лимит сайта или шлём "как получилось".
        thumbnail_bytes: bytes | None = None
        if thumbnail_path is not None:
            try:
                thumbnail_bytes = compress_thumbnail(thumbnail_path, self.max_thumbnail_bytes)
            except ThumbnailError as e:
                log.warning("Превью не будет отправлено: %s", e)
                thumbnail_bytes = None

        manifest_draft = {
            "channel_id": self.channel.channel_id,
            "title": title,
            "description": description,
            # Длительность контента одинакова для всех качеств (это одно и
            # то же видео, просто перекодированное на разные разрешения) —
            # берём из первого (самого лёгкого) качества.
            "duration": round(sum(quality_durations[ordered_qualities[0]]), 3),
            # Единый торрент на ВСЕ качества (см. docstring publish() выше) —
            # torrent_infohash/torrent_name общие, у каждого качества внутри
            # него — только свой диапазон файлов и свои длительности сегментов.
            "torrent_infohash": info_hash,
            "torrent_name": torrent_name,
            "qualities": [
                {
                    "label": q,
                    "segment_durations": quality_durations[q],
                    "file_start_index": quality_ranges[q][0],
                    "file_count": quality_ranges[q][1],
                }
                for q in ordered_qualities
            ],
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # Обязательная авторская отметка NSFW — часть подписанных данных,
            # как title/description (сайт отклоняет публикацию без неё, см.
            # site/app/main.py:publish_video). Значение приходит с сайта уже
            # как явный bool (форма /publish требует осознанного выбора Да/
            # Нет, без варианта по умолчанию — см. templates/publish.html).
            "nsfw": bool(nsfw),
        }
        if thumbnail_bytes is not None:
            # Часть манифеста, а значит — часть подписанных данных (см.
            # canonical_json/canonical_json_for_id в policy/crypto_utils.py,
            # оба работают с произвольным dict, отдельных правок не нужно).
            # Сайт при получении сверяет sha256 присланного файла с этим
            # полем ДО того, как вообще посмотреть на его содержимое —
            # значит подменить превью после подписи (или подсунуть чужое
            # под чужой манифест) невозможно без переподписи ключом канала.
            manifest_draft["thumbnail_sha256"] = hashlib.sha256(thumbnail_bytes).hexdigest()

        video_id = hashlib.sha256(canonical_json_for_id(manifest_draft)).hexdigest()

        # КРИТИЧНО: копируем уже готовые сегменты в storage-директорию i2psnark
        # ПОД ТЕМ ЖЕ ИМЕНЕМ, что и торрент — иначе i2psnark будет пытаться
        # СКАЧАТЬ данные, которые у автора уже есть локально, и раздача
        # никогда не начнётся (0% докачано, нет других сидов)
        # Спрашиваем у РЕАЛЬНО запущенного i2psnark, куда он на самом деле
        # кладёт данные (session-get), а не полагаемся на путь, который мост
        # сам предполагает — см. SnarkIntegration.get_real_storage_dir.
        storage_root = Path(self.snark.get_real_storage_dir())
        storage_dir = storage_root / torrent_name
        storage_dir.mkdir(parents=True, exist_ok=True)
        for seg in all_segments:
            shutil.copy2(seg, storage_dir / seg.name)

        # download-dir как аргумент torrent-add, судя по всему, этим
        # RPC-плагином не поддерживается (в списке "advanced features" —
        # см. README плагина) и молча игнорируется, поэтому больше на него
        # не полагаемся — данные и так лежат там, где i2psnark их и ждёт.
        added = self.snark.rpc.torrent_add_bytes(torrent_bytes, paused=True)
        torrent_id = added["id"]

        # Просим i2psnark проверить хеши уже лежащих на месте файлов — это
        # переведёт торрент из "нужно скачать" в "уже скачано, можно раздавать".
        # ВАЖНО: торрент добавлен с paused=True, поэтому статус "seeding"(6)
        # для него недостижим без отдельного torrent-start — ждём не его, а
        # окончания самой проверки (см. RPCClient.wait_for_verification).
        total_bytes = sum(f.path.stat().st_size for f in torrent_files)
        verify_timeout = max(30.0, total_bytes / (5 * 1024 * 1024))  # ~5 МБ/с как нижняя граница скорости хеширования

        self.snark.rpc.torrent_verify(torrent_id)
        final = self.snark.rpc.wait_for_verification(torrent_id, timeout_seconds=verify_timeout)
        if final is None:
            raise PublishError(
                f"Торрент {torrent_id} не завершил верификацию за "
                f"{verify_timeout:.0f}с (не удалось получить статус от i2psnark)"
            )
        if final.get("percentDone", 0) < 1.0:
            raise PublishError(
                f"Торрент {torrent_id}: после верификации percentDone="
                f"{final.get('percentDone')} — локальные файлы в "
                f"storage-директории не совпадают с только что собранным "
                f".torrent (проверьте storage_dir_provider/копирование сегментов)"
            )
        log.info("Торрент %s верифицирован (100%%), запускаю раздачу", torrent_id)

        # enableInOrder НЕ нужен раздающей стороне — эта настройка управляет
        # порядком ДОКАЧКИ, а у автора уже всё скачано (100%, seeding). Форма
        # приоритезации файлов у i2psnark попросту не рендерится для полностью
        # завершённых торрентов, поэтому попытка её вызвать здесь была ошибкой.
        self.snark.rpc.torrent_start_now(torrent_id)

        manifest_draft["video_id"] = video_id
        manifest_draft["signature"] = self.channel.sign(manifest_draft)

        files = {"torrent": (f"{torrent_name}.torrent", torrent_bytes, "application/x-bittorrent")}
        if thumbnail_bytes is not None:
            files["thumbnail"] = (f"{torrent_name}.webp", thumbnail_bytes, "image/webp")

        try:
            resp = _requests_session_for(site_base_url, self.http_proxy).post(
                f"{site_base_url.rstrip('/')}/api/video/publish",
                data={"manifest_json": json.dumps(manifest_draft)},
                files=files,
                timeout=I2P_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise PublishError(
                f"Не удалось соединиться с сайтом для публикации видео ({site_base_url}): {e}"
            )
        if resp.status_code != 200:
            raise PublishError(f"Сайт отклонил публикацию: {resp.status_code} {resp.text}")

        return {
            "video_id": video_id,
            "torrent_id": torrent_id,
            "torrent_name": torrent_name,
            "site_response": resp.json(),
        }

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
