"""
thumbnail.py — сжатие превью видео на стороне моста ДО отправки на сайт.

Сайт хранит байты превью у себя (в отличие от самого видео, которое ходит
только через BitTorrent между зрителями) — то есть это реальный disk-space
на сервере сайта, оплачиваемый его держателем, а не пользователем. Мост
поэтому обязан прислать что-то заведомо компактное, а не переложить всю
работу по сжатию/отказу на сайт: сайт со своей стороны всё равно СВОЮ
проверку размера делает независимо (см. site/app/main.py:MAX_THUMBNAIL_BYTES
и validate_thumbnail_bytes) — этот модуль просто избавляет от почти всех
отказов "превью слишком большое" ещё до того, как байты вообще уйдут в
сеть.

Стратегия — "лестница" попыток по убыванию качества/разрешения, см.
_ATTEMPTS ниже: останавливаемся на первой попытке, уместившейся в лимит.
Если не уместилась ни одна — превью не отправляется вообще (публикация
видео идёт без него), а не "как есть, лишь бы влезло" ценой совсем
нечитаемой картинки.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

log = logging.getLogger(__name__)


class ThumbnailError(Exception):
    pass


# (максимальная сторона рамки (contain, пропорции сохраняются), WebP quality)
# см. обсуждение констант превью — 320×180 это верхняя граница (совпадает с
# 360p качеством самого видео, превью не имеет смысла хранить крупнее),
# лестница ниже — под менее сжимаемый контент (шум, мелкий текст и т.п.).
_ATTEMPTS: list[tuple[tuple[int, int], int]] = [
    ((320, 180), 78),
    ((320, 180), 60),
    ((320, 180), 42),
    ((240, 135), 50),
    ((180, 101), 45),
]


def _encode_webp(image: Image.Image, box: tuple[int, int], quality: int) -> bytes:
    resized = ImageOps.contain(image, box, method=Image.LANCZOS)
    buf = io.BytesIO()
    # method=6 — самый медленный, но самый плотный из пресетов кодека
    # (одноразовая операция на публикацию, не в горячем пути) — экономия
    # по размеру при том же quality обычно заметная.
    resized.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue()


def compress_thumbnail(image_path: Path, max_bytes: int) -> bytes | None:
    """
    Пытается сжать изображение по image_path в WebP, укладываясь в
    max_bytes, перебирая _ATTEMPTS по убыванию качества. Возвращает байты
    первой попытки, уместившейся в лимит, либо None, если ни одна не
    подошла (вызывающий код должен трактовать это как "без превью", а не
    как ошибку публикации в целом — см. authz.py/publisher.py).

    Бросает ThumbnailError, только если файл вообще не открывается как
    изображение (пользователь выбрал не картинку) — это отдельная от
    "не влезло по размеру" ситуация, о ней стоит сообщить явно.
    """
    try:
        with Image.open(image_path) as raw:
            raw.load()
            # EXIF-ориентация (частый случай для фото с телефона) — иначе
            # превью может оказаться повёрнутым на 90°/270° после сжатия,
            # хотя в оригинале ориентация была верной за счёт EXIF-тега.
            image = ImageOps.exif_transpose(raw)
            if image.mode not in ("RGB", "L"):
                # WebP-энкодер Pillow ожидает RGB (или RGBA — но альфа-канал
                # для фотографического превью не нужен и просто раздувает
                # результат) — прозрачность из PNG/GIF схлопываем на чёрный
                # фон, а не оставляем как есть.
                image = image.convert("RGB")

            for box, quality in _ATTEMPTS:
                encoded = _encode_webp(image, box, quality)
                if len(encoded) <= max_bytes:
                    return encoded

            log.warning(
                "Превью %s не удалось сжать ни одним из %d вариантов до "
                "%d байт — публикация видео пойдёт без превью",
                image_path, len(_ATTEMPTS), max_bytes,
            )
            return None
    except (UnidentifiedImageError, OSError) as e:
        raise ThumbnailError(f"Не удалось прочитать {image_path} как изображение: {e}")
