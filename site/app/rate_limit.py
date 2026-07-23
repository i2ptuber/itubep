"""
rate_limit.py — простой sliding-window rate limiter в памяти процесса.

ВАЖНО про I2P: обычный rate-limiting по IP клиента тут не работает вообще —
весь входящий трафик к eepsite идёт через локальный HTTP-туннель i2pd,
поэтому request.client.host для ЛЮБОГО посетителя сайта будет одним и тем же
loopback-адресом. У нас есть только два реальных инструмента:

  1. Для эндпоинтов, где запрос несёт криптографически подтверждённую
     личность (register_channel/publish_video подписаны ed25519-ключом
     канала, channel_id из этого ключа детерминирован) — лимитировать ПО
     channel_id. Это честный per-actor rate limit.

  2. Для остальных публичных read-путей (поиск, скачивание .torrent,
     список видео канала...) — идентичности нет вообще, единственное, что
     можно сделать — общий бюджет запросов на весь сайт разом. Это грубый
     инструмент (общий лимит делится между всеми одновременными
     легитимными зрителями тоже), но лучше, чем ничего — без него один
     скрипт может создать неограниченную нагрузку на БД/диск.

In-memory (не Redis/БД) — осознанно: для масштаба одного процесса uvicorn
этого достаточно, а внешняя зависимость ради rate-limiter была бы overkill.
Ценой этого счётчики сбрасываются при рестарте сайта — не проблема, это не
авторизационные данные, которые нужно помнить между запусками.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_prune = time.time()

    def allow(self, key: str, max_requests: int, window_seconds: float) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            cutoff = now - window_seconds
            while q and q[0] < cutoff:
                q.popleft()

            if len(q) >= max_requests:
                return False

            q.append(now)

            # Периодическая уборка старых ключей, чтобы словарь не рос
            # неограниченно от разовых/уникальных ключей (например, если
            # в будущем сюда добавят лимиты, ключующиеся на что-то с высокой
            # кардинальностью) — раз в ~10 минут, не на каждый запрос.
            if now - self._last_prune > 600:
                self._prune(now)
                self._last_prune = now

            return True

    def _prune(self, now: float, max_age_seconds: float = 3600):
        dead_keys = []
        for k, q in self._hits.items():
            while q and q[0] < now - max_age_seconds:
                q.popleft()
            if not q:
                dead_keys.append(k)
        for k in dead_keys:
            del self._hits[k]


# Единственный на процесс — простые именованные "бюджеты" ниже делят его
# по ключам вида "<bucket_name>:<идентификатор>" или "<bucket_name>:global".
limiter = SlidingWindowLimiter()


# Дефолты "из коробки" — используются, если для bucket нет override'а в БД
# (см. models.py:RateLimitConfig, scripts/configure_limits.py). Формат:
# bucket -> (max_requests | None, window_seconds). max_requests=None значит
# "лимит отключён" для этого bucket по умолчанию.
#
# channel_register_global и video_publish_global выключены по умолчанию —
# per-identity лимиты (channel_register_id, video_publish_id) уже достаточно
# сдерживают спам одним конкретным каналом; глобальный потолок на ВЕСЬ сайт
# добавляется отдельно через configure_limits.py, только если реально
# понадобится (проект вырос настолько, что регистраций/публикаций стало
# много — тогда общий бюджет может начать мешать легитимным пользователям,
# и включать его стоит осознанно, с числом, соответствующим реальной нагрузке,
# а не наугад выбранным до того, как стала известна реальная активность).
DEFAULTS: dict[str, tuple[int | None, int]] = {
    "channel_register_global": (None, 3600),
    "channel_register_id": (20, 3600),
    "video_publish_id": (60, 3600),
    "video_publish_global": (None, 3600),
    "manifest_read": (1000, 60),
    "torrent_download": (1500, 60),
    "search": (300, 60),
    "channel_videos": (1000, 60),
    "channel_page": (1000, 60),
    "video_page": (1000, 60),
}

# Заполняется при старте приложения (main.py:on_startup) из БД — override'ы,
# заданные через scripts/configure_limits.py. Загружается ОДИН РАЗ при
# старте, не на каждый запрос (лишний DB-запрос на каждый hit был бы
# избыточен для того, что меняется редко) — поэтому изменения через
# configure_limits.py требуют рестарта сайта, чтобы примениться, как и
# остальные патчи в этом проекте.
_config_overrides: dict[str, tuple[int | None, int]] = {}


async def load_config_from_db(session) -> None:
    from sqlalchemy import select
    from .models import RateLimitConfig

    result = await session.execute(select(RateLimitConfig))
    _config_overrides.clear()
    for row in result.scalars().all():
        _config_overrides[row.bucket] = (row.max_requests, row.window_seconds)


def _resolve(bucket: str) -> tuple[int | None, int]:
    if bucket in _config_overrides:
        return _config_overrides[bucket]
    if bucket in DEFAULTS:
        return DEFAULTS[bucket]
    # Неизвестный bucket (опечатка в коде?) — лучше упасть громко при
    # разработке, чем молча остаться без лимита или уронить прод.
    raise KeyError(f"Rate limit bucket {bucket!r} не описан ни в DEFAULTS, ни в БД")


def enforce(bucket: str, key: str = "global"):
    """Бросает HTTPException(429), если лимит превышен. key='global' для
    общесайтовых бюджетов без идентичности отправителя (дефолт для
    большинства read-путей); для per-identity бюджетов (channel_id и т.п.)
    передаётся конкретный идентификатор."""
    from fastapi import HTTPException

    max_requests, window_seconds = _resolve(bucket)
    if max_requests is None:
        return  # лимит явно отключён для этого bucket

    if not limiter.allow(f"{bucket}:{key}", max_requests, window_seconds):
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много запросов ({bucket}), попробуйте позже",
            headers={"Retry-After": str(int(window_seconds))},
        )
