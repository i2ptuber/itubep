"""
storage.py — SQLite-хранилище Слоя 2: токены сопряжения, владение торрентами
по origin, блеклист. Все данные локальные, никогда не покидают машину.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


DEFAULT_DB_PATH = Path.home() / ".config" / "itubep-bridge" / "policy.db"


class PolicyStorage:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            origin TEXT NOT NULL UNIQUE,
            created_at REAL,
            revoked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS torrent_ownership (
            torrent_id INTEGER PRIMARY KEY,
            owner_origin TEXT NOT NULL,
            video_id TEXT,
            added_at REAL
        );

        CREATE TABLE IF NOT EXISTS blocklist (
            origin TEXT PRIMARY KEY,
            added_at REAL,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS pairing_requests (
            origin TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL,
            last_request_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS pairing_abuse (
            origin TEXT PRIMARY KEY,
            invalid_attempts INTEGER NOT NULL DEFAULT 0,
            last_warned_at_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        self.conn.commit()

        # Лёгкая миграция для БД, созданных до этого патча: CREATE TABLE IF
        # NOT EXISTS не добавляет новую колонку в уже существующую таблицу
        # pairing_requests. try/except вместо проверки PRAGMA table_info —
        # проще и для sqlite это стандартный лёгкий паттерн миграции.
        try:
            self.conn.execute("ALTER TABLE pairing_requests ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # колонка уже есть — база создана уже этой версией схемы

    # --- Токены ---

    def save_token(self, token: str, origin: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO tokens (token, origin, created_at, revoked) "
            "VALUES (?, ?, ?, 0)",
            (token, origin, time.time()),
        )
        self.conn.commit()

    def get_origin_for_token(self, token: str) -> str | None:
        row = self.conn.execute(
            "SELECT origin FROM tokens WHERE token = ? AND revoked = 0", (token,)
        ).fetchone()
        return row[0] if row else None

    def revoke_origin(self, origin: str):
        self.conn.execute("UPDATE tokens SET revoked = 1 WHERE origin = ?", (origin,))
        self.conn.commit()

    def origin_has_active_token(self, origin: str) -> bool:
        """
        Есть ли у origin хотя бы один НЕотозванный основной токен. Нужен
        отдельно от get_origin_for_token — там поиск по конкретному
        токену, здесь по origin, чтобы scoped stream-токен (см.
        authz.py:_validate_stream_token) мгновенно переставал работать
        сразу после revoke, а не только по истечении своего отдельного TTL.
        """
        row = self.conn.execute(
            "SELECT 1 FROM tokens WHERE origin = ? AND revoked = 0", (origin,)
        ).fetchone()
        return row is not None

    def list_paired_origins(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT origin, created_at, revoked FROM tokens ORDER BY created_at DESC"
        ).fetchall()
        return [{"origin": r[0], "created_at": r[1], "revoked": bool(r[2])} for r in rows]

    # --- Владение торрентами ---

    def register_torrent(self, torrent_id: int, owner_origin: str, video_id: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO torrent_ownership "
            "(torrent_id, owner_origin, video_id, added_at) VALUES (?, ?, ?, ?)",
            (torrent_id, owner_origin, video_id, time.time()),
        )
        self.conn.commit()

    def get_owner(self, torrent_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT owner_origin FROM torrent_ownership WHERE torrent_id = ?",
            (torrent_id,),
        ).fetchone()
        return row[0] if row else None

    def unregister_torrent(self, torrent_id: int):
        self.conn.execute("DELETE FROM torrent_ownership WHERE torrent_id = ?", (torrent_id,))
        self.conn.commit()

    # --- Блеклист ---

    def is_blocked(self, origin: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM blocklist WHERE origin = ?", (origin,)
        ).fetchone()
        return row is not None

    def add_to_blocklist(self, origin: str, reason: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO blocklist (origin, added_at, reason) VALUES (?, ?, ?)",
            (origin, time.time(), reason),
        )
        self.revoke_origin(origin)
        self.conn.commit()

    def remove_from_blocklist(self, origin: str):
        self.conn.execute("DELETE FROM blocklist WHERE origin = ?", (origin,))
        self.conn.commit()

    # --- Anti-spam для pairing-запросов ---

    def can_request_pairing(self, origin: str, cooldown_seconds: float = 120.0) -> bool:
        row = self.conn.execute(
            "SELECT last_request_at FROM pairing_requests WHERE origin = ?", (origin,)
        ).fetchone()
        if row is None:
            return True
        return (time.time() - row[0]) >= cooldown_seconds

    def save_pairing_code(self, origin: str, code: str):
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO pairing_requests (origin, code, status, created_at, last_request_at, attempts) "
            "VALUES (?, ?, 'pending', ?, ?, 0)",
            (origin, code, now, now),
        )
        self.conn.commit()

    def get_pairing_state(self, origin: str, ttl_seconds: float = 120.0) -> dict | None:
        row = self.conn.execute(
            "SELECT code, status, created_at, attempts FROM pairing_requests WHERE origin = ?", (origin,)
        ).fetchone()
        if row is None:
            return None
        code, status, created_at, attempts = row
        if (time.time() - created_at) > ttl_seconds:
            return None  # протухло
        return {"code": code, "status": status, "attempts": attempts}

    def increment_pairing_attempts(self, origin: str) -> int:
        """
        Увеличивает счётчик неудачных попыток ввода кода в ТЕКУЩЕМ цикле
        (обнуляется каждым новым save_pairing_code, т.е. каждым новым кодом).
        Возвращает новое значение — confirm_pairing сверяет его с лимитом
        попыток на один код (см. pairing.py:MAX_ATTEMPTS_PER_CODE).
        """
        self.conn.execute(
            "UPDATE pairing_requests SET attempts = attempts + 1 WHERE origin = ?", (origin,)
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT attempts FROM pairing_requests WHERE origin = ?", (origin,)
        ).fetchone()
        return row[0] if row else 0

    def set_pairing_status(self, origin: str, status: str):
        self.conn.execute(
            "UPDATE pairing_requests SET status = ? WHERE origin = ?", (status, origin),
        )
        self.conn.commit()

    def clear_pairing_code(self, origin: str):
        self.conn.execute("DELETE FROM pairing_requests WHERE origin = ?", (origin,))
        self.conn.commit()

    # --- Накопительная статистика злоупотреблений (переживает истечение
    # TTL и cooldown отдельных кодов — увеличенный cooldown уже сильно
    # замедляет подбор кода, но злоумышленник может пытаться раз за разом
    # НЕОГРАНИЧЕННО долго; это отдельный счётчик, который не сбрасывается
    # каждым новым кодом, и на основании которого мы предупреждаем
    # пользователя и предлагаем добавить сайт в блеклист) ---

    def record_invalid_attempt(self, origin: str) -> int:
        """Увеличивает НАКОПИТЕЛЬНЫЙ (за всё время, не за один код) счётчик
        неудачных попыток подбора кода для origin. Возвращает новое значение."""
        self.conn.execute(
            "INSERT INTO pairing_abuse (origin, invalid_attempts, last_warned_at_count) "
            "VALUES (?, 1, 0) "
            "ON CONFLICT(origin) DO UPDATE SET invalid_attempts = invalid_attempts + 1",
            (origin,),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT invalid_attempts FROM pairing_abuse WHERE origin = ?", (origin,)
        ).fetchone()
        return row[0] if row else 0

    def get_and_mark_warn_threshold(self, origin: str, count: int) -> bool:
        """
        Возвращает True РОВНО ОДИН РАЗ для каждого нового порога — не даёт
        показывать предупреждение повторно на каждую последующую попытку
        после того как порог уже пройден и пользователь его уже видел (или
        явно проигнорировал). Атомарно (в рамках одного соединения sqlite,
        которое и так single-writer) обновляет last_warned_at_count.
        """
        row = self.conn.execute(
            "SELECT last_warned_at_count FROM pairing_abuse WHERE origin = ?", (origin,)
        ).fetchone()
        last_warned = row[0] if row else 0
        if count <= last_warned:
            return False
        self.conn.execute(
            "UPDATE pairing_abuse SET last_warned_at_count = ? WHERE origin = ?", (count, origin),
        )
        self.conn.commit()
        return True

    def reset_invalid_attempts(self, origin: str):
        """Вызывается при успешном сопряжении — легитимный сайт, который
        просто пару раз ошибся при вводе кода, не должен продолжать
        числиться "подозрительным" вечно после того как всё же сопрягся."""
        self.conn.execute("DELETE FROM pairing_abuse WHERE origin = ?", (origin,))
        self.conn.commit()
    
    # --- Настройки ---

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()
    
    def get_torrents_by_owner(self, owner_origin: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT torrent_id, video_id, added_at FROM torrent_ownership WHERE owner_origin = ?",
            (owner_origin,),
        ).fetchall()
        return [{"torrent_id": r[0], "video_id": r[1], "added_at": r[2]} for r in rows]
        
    def get_i2p_http_proxy(self) -> str:
        """
        Адрес HTTP-прокси I2P-роутера (i2pd/Java I2P), через который мост
        должен ходить к сайту при публикации (регистрация канала,
        отправка манифеста+торрента), если у сайта .i2p-адрес.

        requests.post(site_base_url, ...) не может напрямую резолвить
        .i2p-домены — это не DNS, а имя I2P-назначения, известное только
        через SAM/HTTP-прокси самого роутера. Дефолт — стандартный порт
        HTTP-прокси и у i2pd, и у Java I2P (127.0.0.1:4444), обычно менять
        не требуется, если роутер настроен стандартно.
        """
        return self.get_setting("i2p_http_proxy", "http://127.0.0.1:4444")

    def set_i2p_http_proxy(self, proxy_url: str):
        self.set_setting("i2p_http_proxy", proxy_url)

    def get_trackers(self) -> list[str]:
        """
        Список announce-URL, добавляемых в каждый публикуемый .torrent.

        Пусто по умолчанию (старое поведение — только DHT/PEX). Пользователю
        стоит скопировать сюда живой список из
        http://127.0.0.1:8002/i2psnark/configure ("Trackers") своего же
        i2psnark — это заведомо трекеры, которые роутер уже умеет достигать.
        Хранится как строки, разделённые переводом строки, чтобы было легко
        редактировать через settings_window (обычное multiline-поле).
        """
        raw = self.get_setting("trackers", "")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def set_trackers(self, trackers: list[str]):
        self.set_setting("trackers", "\n".join(trackers))

    def get_snark_storage_dir(self) -> str:
        import os
        default = os.path.expanduser("~/i2psnark-run/i2psnark")
        return self.get_setting("snark_storage_dir", default)

    def set_snark_storage_dir(self, path: str):
        self.set_setting("snark_storage_dir", path)
        
    def find_torrent_for_video(self, owner_origin: str, video_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT torrent_id FROM torrent_ownership WHERE owner_origin = ? AND video_id = ?",
            (owner_origin, video_id),
        ).fetchone()
        return row[0] if row else None
