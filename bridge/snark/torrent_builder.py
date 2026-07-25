"""
torrent_builder.py — генерация .torrent файлов (bencode) под схему
"1 файл = 1 HLS-сегмент" (multi-file torrent на видео, обсуждали в плане).

Поддерживает опциональный список announce-трекеров (см. build_torrent_with_hash,
параметр trackers). Изначально торренты собирались вообще без трекеров, в
расчёте только на DHT/PEX i2psnark — на практике это давало очень долгий
поиск первого пира для только что опубликованных видео (DHT-бутстрап для
нового info_hash небыстрый, а PEX сам зависит от уже установленного
коннекта). Список трекеров задаётся в настройках моста (см.
policy/storage.py:get_trackers) и должен указывать на живые открытые
BT-трекеры внутри I2P — DHT/PEX при этом остаются fallback'ом, ничего не
отключается.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


def bdecode(data: bytes) -> tuple:
    """
    Минимальный bdecode (только то, что нужно для миграции: разобрать уже
    опубликованный .torrent, добавить announce/announce-list, закодировать
    обратно). Возвращает (значение, остаток_после_разбора).
    """
    if data[0:1] == b"i":
        end = data.index(b"e")
        return int(data[1:end]), data[end + 1:]
    if data[0:1] == b"l":
        rest = data[1:]
        items = []
        while rest[0:1] != b"e":
            item, rest = bdecode(rest)
            items.append(item)
        return items, rest[1:]
    if data[0:1] == b"d":
        rest = data[1:]
        result = {}
        while rest[0:1] != b"e":
            key, rest = bdecode(rest)
            value, rest = bdecode(rest)
            result[key.decode("utf-8", errors="replace") if isinstance(key, bytes) else key] = value
            rest = rest
        return result, rest[1:]
    # строка: "<len>:<bytes>"
    colon = data.index(b":")
    length = int(data[:colon])
    start = colon + 1
    return data[start:start + length], data[start + length:]


# --- Минимальный bencode-кодировщик (никаких внешних зависимостей) ---

def bencode(value) -> bytes:
    if isinstance(value, int):
        return f"i{value}e".encode()
    if isinstance(value, bytes):
        return f"{len(value)}:".encode() + value
    if isinstance(value, str):
        b = value.encode("utf-8")
        return f"{len(b)}:".encode() + b
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        # bencode требует ключи в отсортированном (побайтовом) порядке
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-8") if isinstance(kv[0], str) else kv[0])
        out = b"d"
        for k, v in items:
            out += bencode(k) + bencode(v)
        out += b"e"
        return out
    raise TypeError(f"Не поддерживаемый тип для bencode: {type(value)}")


@dataclass
class TorrentFile:
    """Один файл в multi-file торренте — соответствует одному HLS-сегменту."""
    path: Path          # реальный путь на диске (для чтения содержимого)
    torrent_path: list[str]  # путь внутри торрента, напр. ["segment_0000.m4s"]


class UntrustedTorrentError(Exception):
    """
    .torrent, присланный сайтом через /bridge/add, не проходит проверку
    на "похож на видео, которое мы сами могли бы опубликовать" — см.
    validate_video_torrent(). НЕ добавляется в i2psnark ни при каких
    условиях, если это исключение брошено.
    """


# Формат имени файла сегмента, который сам мост использует при публикации
# (см. build_torrent_with_hash / snark/publisher.py:segment_video_ffmpeg —
# "segment_%04d.ts"). Любой .torrent, добавляемый через /bridge/add,
# ДОЛЖЕН состоять целиком из файлов такого вида — иначе это не "видео от
# ITubeP", а произвольный контент, который сайт пытается заставить мост
# скачать и раздавать (см. audit — было критичной находкой).
_SEGMENT_FILENAME_RE = re.compile(r"^segment_\d{4}\.ts$")


def validate_video_torrent(
    torrent_bytes: bytes,
    expected_torrent_name: str,
    max_total_size_bytes: int,
    max_files: int,
) -> None:
    """
    Проверяет .torrent, присланный сайтом (base64 в /bridge/add), ДО того,
    как он будет передан в i2psnark на скачивание/раздачу. Мост раньше
    принимал сюда абсолютно любой валидный .torrent-файл с любым
    содержимым (любые имена файлов, любой размер, любые встроенные
    трекеры) — то есть сопряжённый сайт мог тихо (в Mode.SILENT) заставить
    мост скачивать и сидировать произвольный контент, выбранный сайтом, а
    не то, что показывает плеер. Эта функция сужает принимаемые торренты
    до формы, которую сам мост способен породить при публикации:
    multi-file, все файлы — "segment_NNNN.ts" без вложенных директорий,
    имя торрента совпадает с video_id (sha256-хэш), в разумных пределах
    по числу файлов и суммарному размеру.

    Бросает UntrustedTorrentError с человекочитаемой причиной, если
    торрент не проходит — вызывающий код (integration.py) должен
    пробрасывать это как есть, НЕ пытаться "починить" или частично принять.
    """
    try:
        decoded, rest = bdecode(torrent_bytes)
    except Exception as e:
        raise UntrustedTorrentError(f"не удалось разобрать .torrent (bencode): {e}") from e
    if rest:
        raise UntrustedTorrentError("лишние данные после info-словаря .torrent")
    if not isinstance(decoded, dict):
        raise UntrustedTorrentError(".torrent верхнего уровня — не словарь")

    info = decoded.get("info")
    if not isinstance(info, dict):
        raise UntrustedTorrentError("отсутствует или некорректен info-словарь")

    name = info.get("name")
    name = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else name
    if name != expected_torrent_name:
        raise UntrustedTorrentError(
            f"info.name торрента ({name!r}) не совпадает с ожидаемым "
            f"video_id ({expected_torrent_name!r}) — торрент не тот, "
            f"который сайт заявил"
        )

    # Одно-файловые торренты (ключ "length" прямо в info, без "files") у
    # нашей схемы публикации не встречаются — все видео это multi-file
    # (список HLS-сегментов). Отказ — не пытаемся поддержать формат,
    # которого сам мост никогда не производит.
    files = info.get("files")
    if not isinstance(files, list) or not files:
        raise UntrustedTorrentError(
            "торрент не multi-file (нет info.files) — не похож на видео, "
            "опубликованное через ITubeP"
        )

    if len(files) > max_files:
        raise UntrustedTorrentError(
            f"торрент содержит {len(files)} файлов, лимит {max_files} "
            f"(см. настройки моста)"
        )

    total_size = 0
    for f in files:
        if not isinstance(f, dict):
            raise UntrustedTorrentError("некорректная запись в info.files")
        length = f.get("length")
        if not isinstance(length, int) or length < 0:
            raise UntrustedTorrentError("некорректная длина файла в info.files")
        total_size += length

        path_parts = f.get("path")
        if not isinstance(path_parts, list) or len(path_parts) != 1:
            # Ровно один компонент пути — файлы сегментов лежат плоско,
            # без вложенных директорий. Больше одного компонента —
            # потенциальный path traversal внутри директории торрента на
            # стороне i2psnark, чего наша схема никогда не производит.
            raise UntrustedTorrentError(
                "файл торрента не в корне (вложенные пути не поддерживаются "
                "схемой видео-сегментов)"
            )
        part = path_parts[0]
        part = part.decode("utf-8", errors="replace") if isinstance(part, bytes) else part
        if not isinstance(part, str) or not _SEGMENT_FILENAME_RE.match(part):
            raise UntrustedTorrentError(
                f"имя файла в торренте ({part!r}) не соответствует схеме "
                f"'segment_NNNN.ts' — торрент содержит нечто, что не "
                f"является HLS-сегментом от ITubeP"
            )

    if total_size > max_total_size_bytes:
        raise UntrustedTorrentError(
            f"суммарный размер торрента {total_size} байт превышает лимит "
            f"{max_total_size_bytes} байт (см. настройки моста)"
        )

    piece_length = info.get("piece length")
    if not isinstance(piece_length, int) or piece_length <= 0:
        raise UntrustedTorrentError("некорректный piece length")

    pieces = info.get("pieces")
    if not isinstance(pieces, bytes) or len(pieces) % 20 != 0:
        raise UntrustedTorrentError("некорректное поле pieces (не кратно 20 байтам SHA1)")

    expected_piece_count = (total_size + piece_length - 1) // piece_length if total_size else 0
    actual_piece_count = len(pieces) // 20
    if actual_piece_count != expected_piece_count:
        raise UntrustedTorrentError(
            f"число pieces ({actual_piece_count}) не соответствует "
            f"total_size/piece_length ({expected_piece_count}) — "
            f"похоже на подделанные метаданные"
        )


def build_torrent(
    name: str,
    files: list[TorrentFile],
    piece_length: int = 256 * 1024,
    private: bool = False,
) -> bytes:
    """
    Собирает .torrent (multi-file) из списка файлов.

    piece_length по умолчанию 256 KiB (совпадает с тем, что видели в реальном
    тесте — mktorrent выбрал 256 KiB для 20 MB тестового набора). Для реальных
    видео стоит пересчитывать под общий размер, чтобы piece count оставался
    разумным (не тысячи мелких pieces, но и не слишком крупные для быстрого
    старта воспроизведения).
    """
    # Конкатенируем содержимое всех файлов в один поток для нарезки на pieces —
    # это стандартная модель BitTorrent (piece может пересекать границы файлов)
    total_size = 0
    file_entries = []
    piece_hashes = bytearray()

    buf = bytearray()

    def flush_piece():
        nonlocal buf
        if buf:
            piece_hashes.extend(hashlib.sha1(bytes(buf)).digest())
            buf = bytearray()

    for tf in files:
        size = os.path.getsize(tf.path)
        total_size += size
        file_entries.append({
            "length": size,
            "path": tf.torrent_path,
        })

        with open(tf.path, "rb") as f:
            while True:
                chunk = f.read(piece_length - len(buf))
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) == piece_length:
                    flush_piece()

    flush_piece()  # последний неполный piece

    info = {
        "name": name,
        "piece length": piece_length,
        "pieces": bytes(piece_hashes),
        "files": file_entries,
    }
    if private:
        info["private"] = 1

    torrent = {
        "info": info,
        "created by": "ITubeP bridge",
    }

    return bencode(torrent)


def compute_info_hash(torrent_bytes: bytes) -> str:
    """
    Извлекает info-dict из уже собранного .torrent и считает его info-hash
    (SHA1 стандартного BitTorrent info-hash). Нужен для сверки с тем, что
    вернёт i2psnark после torrent-add.

    Примечание: для простоты не делаем полноценный bdecode здесь — раз мы сами
    строили torrent_bytes функцией build_torrent выше, проще пересчитать info-hash
    прямо в build_torrent и вернуть отдельно, если понадобится в интеграции.
    Оставлено как TODO, если возникнет необходимость декодировать чужие .torrent.
    """
    raise NotImplementedError(
        "Используйте build_torrent_with_hash() ниже, если нужен info-hash "
        "одновременно со сборкой — там нет необходимости в bdecode."
    )


def build_torrent_with_hash(
    name: str,
    files: list[TorrentFile],
    piece_length: int = 256 * 1024,
    private: bool = False,
    trackers: list[str] | None = None,
) -> tuple[bytes, str]:
    """Как build_torrent, но дополнительно возвращает info-hash (hex).

    trackers — список announce-URL живых I2P-трекеров (например, взятых из
    http://127.0.0.1:8002/i2psnark/configure на стороне моста-издателя).
    DHT/PEX i2psnark остаются работать как fallback, но announce-трекеры
    дают peer-list СРАЗУ при первом announce, а не после бутстрапа DHT —
    это критично для только что опубликованных видео с 1 сидом.
    Пустой список сохраняет старое поведение (только DHT/PEX).
    """
    total_size = 0
    file_entries = []
    piece_hashes = bytearray()
    buf = bytearray()

    def flush_piece():
        nonlocal buf
        if buf:
            piece_hashes.extend(hashlib.sha1(bytes(buf)).digest())
            buf = bytearray()

    for tf in files:
        size = os.path.getsize(tf.path)
        total_size += size
        file_entries.append({"length": size, "path": tf.torrent_path})
        with open(tf.path, "rb") as f:
            while True:
                chunk = f.read(piece_length - len(buf))
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) == piece_length:
                    flush_piece()
    flush_piece()

    info = {
        "name": name,
        "piece length": piece_length,
        "pieces": bytes(piece_hashes),
        "files": file_entries,
    }
    if private:
        info["private"] = 1

    info_bencoded = bencode(info)
    info_hash = hashlib.sha1(info_bencoded).hexdigest()

    torrent: dict = {"info": info, "created by": "ITubeP bridge"}

    trackers = trackers or []
    if trackers:
        # "announce" — основной (первый) трекер, для клиентов без поддержки
        # multi-tracker расширения; "announce-list" — BEP-12, i2psnark
        # проходит по нему по очереди/tier'ам, пока кто-то не ответит.
        # Каждый tracker кладём в свой tier (список из одного элемента) —
        # так i2psnark будет пробовать их все, а не только первый живой tier.
        torrent["announce"] = trackers[0]
        torrent["announce-list"] = [[t] for t in trackers]

    return bencode(torrent), info_hash
