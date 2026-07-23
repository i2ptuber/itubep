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
