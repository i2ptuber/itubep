"""
updater.py — ручная проверка обновлений моста itubep-bridge.

Дизайн (см. обсуждение в README/issue про доставку обновлений):
  - НИКАКОГО автозапуска в фоне. Проверка только по явному действию
    пользователя (кнопка "Проверить обновления" в settings_window.py).
  - НИКАКОГО автоматического скачивания/установки. Мы максимум скачиваем
    и проверяем хэш артефакта, дальше пользователь сам открывает и
    запускает инсталлятор/тарбол.
  - Канал проверки выбирается пользователем: "i2p" (по умолчанию) —
    манифест и артефакты лежат на git.community.i2p (Gitea-инстанс
    в самой сети I2P), запрос идёт через уже настроенный HTTP-прокси
    I2P-роутера; или "clearnet" — манифест и артефакты на GitHub Releases,
    запрос идёт напрямую, в обход I2P. Мы не решаем это молча за
    пользователя, т.к. у этих двух путей разная модель приватности (см.
    is_i2p_host() в snark/publisher.py — та же осторожность применяется
    и здесь).
  - Манифест ОДИН файл (updates/latest.json в репозитории на GitHub — то
    есть сам JSON всегда читается с GitHub, даже для канала "i2p"; см.
    объяснение ниже), но внутри у него РАЗНЫЕ ссылки на артефакты под
    разные каналы — потому что GitHub Actions физически не может ни
    скачать, ни выложить файлы на .i2p-хосты (раннеры не имеют I2P-
    коннективности), а публикация на git.community.i2p — отдельный
    процесс с отдельной машины, у которой I2P-доступ есть (см.
    scripts/publish_i2p_release.py).

    ВАЖНО про "manifest_url": сам JSON-манифест для канала "i2p" тоже
    планируется зеркалировать на git.community.i2p отдельным шагом (см.
    I2P_MANIFEST_URL) — так канал "i2p" не делает НИ ОДНОГО запроса на
    clearnet, от первого запроса до скачивания файла. Пока зеркало не
    развёрнуто, I2P_MANIFEST_URL будет честно возвращать ошибку сети,
    а не тихо падать на GitHub.

Формат манифеста (JSON), см. updates/latest.json.example в корне репо:
{
  "version": "1.3.0",
  "changelog_short": "Короткое описание изменений",
  "changelog_url": "https://github.com/i2ptuber/itubep/releases/tag/v1.3.0",
  "min_supported_version": "1.0.0",
  "artifacts": {
    "clearnet": {
      "linux":   {"url": "https://github.com/.../itubep-bridge-linux-v1.3.0.tar.gz", "sha256": "..."},
      "windows": {"url": "https://github.com/.../itubep-bridge-windows-v1.3.0.exe", "sha256": "..."}
    },
    "i2p": {
      "linux":   {"url": "http://git.community.i2p/tuber/itubep/releases/download/v1.3.0/itubep-bridge-linux-v1.3.0.tar.gz", "sha256": "..."},
      "windows": {"url": "http://git.community.i2p/tuber/itubep/releases/download/v1.3.0/itubep-bridge-windows-v1.3.0.exe", "sha256": "..."}
    }
  }
}

Хэши под clearnet и i2p для одной и той же версии ДОЛЖНЫ совпадать, если
это один и тот же файл, зеркалированный в две системы — если они вдруг
разошлись, это сигнал, что что-то не так с одной из копий, и это стоит
проверять при публикации (см. scripts/publish_i2p_release.py: он сверяет
хэш локально посчитанного файла с тем, что уже стоит в clearnet-секции
манифеста, и предупреждает при несовпадении).
"""

from __future__ import annotations

import hashlib
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from __version__ import VERSION

CHANNEL_I2P = "i2p"
CHANNEL_CLEARNET = "clearnet"
DEFAULT_CHANNEL = CHANNEL_I2P

# Манифест на eepsite/Gitea-зеркале проекта. Замените на реальный адрес,
# когда будет развёрнуто зеркало updates/latest.json на git.community.i2p
# (например через "raw"-ссылку Gitea: .../raw/branch/main/updates/latest.json)
# — до тех пор канал "i2p" будет честно возвращать ошибку "не удалось
# подключиться", а не тихо падать на clearnet у пользователя за спиной.
I2P_MANIFEST_URL = "http://git.community.i2p/tuber/itubep/raw/branch/main/updates/latest.json"

# Манифест в самом репозитории на GitHub — источник истины для clearnet-
# канала, и единственный источник истины вообще (пока нет i2p-зеркала
# самого JSON-файла, см. docstring выше) — raw.githubusercontent.com
# отдаёт файл как есть, без обёртки GitHub API.
CLEARNET_MANIFEST_URL = (
    "https://raw.githubusercontent.com/i2ptuber/itubep/main/updates/latest.json"
)

REQUEST_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 600.0
DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 МиБ


class UpdateCheckError(Exception):
    """Ошибка при проверке/скачивании обновления — сеть, формат манифеста,
    несовпадение хэша и т.п. Всегда с человекочитаемым текстом на русском/
    английском (вызывающий код просто показывает str(e) в UI)."""


@dataclass
class UpdateInfo:
    version: str
    changelog_short: str
    changelog_url: str
    download_url: str
    sha256: str
    is_newer: bool


def _parse_version(v: str) -> tuple[int, ...]:
    """
    Простой парсер семвера без внешних зависимостей: "1.3.0" -> (1, 3, 0).
    Не пытается поддержать полный semver (пререлизы, билд-метаданные) —
    для схемы версионирования этого проекта (простые X.Y.Z-тэги) этого
    достаточно. Некорректный формат — явная ошибка, а не тихое "0.0.0",
    чтобы не сравнивать версии неправильно молча.
    """
    if not re.fullmatch(r"\d+(\.\d+){0,3}", v.strip()):
        raise UpdateCheckError(f"Некорректный формат версии в манифесте: {v!r}")
    return tuple(int(part) for part in v.strip().split("."))


def _current_platform_key() -> str:
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    if system == "windows":
        return "windows"
    raise UpdateCheckError(
        f"Автообновление пока не поддерживает эту ОС ({platform.system()}). "
        f"Проверьте страницу релизов вручную: "
        f"https://github.com/i2ptuber/itubep/releases"
    )


def _session_for_channel(channel: str, storage) -> requests.Session:
    """
    Возвращает requests.Session с правильной маршрутизацией под канал.

    Канал "i2p": манифест и артефакты — .i2p-адреса, requests не резолвит
    такие домены напрямую (это не DNS), поэтому запрос обязан идти через
    HTTP-прокси I2P-роутера. Без настроенного прокси — явная ошибка, а не
    тихий fallback на clearnet (это была бы утечка того, что пользователь
    вообще проверяет обновления, в обход выбранного им канала).

    Канал "clearnet": пользователь осознанно выбрал этот канал в настройках
    (см. settings_window.py) — запрос идёт напрямую, без прокси.
    """
    session = requests.Session()
    if channel == CHANNEL_I2P:
        http_proxy = storage.get_i2p_http_proxy() if storage else None
        if not http_proxy:
            raise UpdateCheckError(
                "Выбран канал обновлений через I2P, но HTTP-прокси I2P "
                "не настроен (см. настройки моста)."
            )
        session.proxies = {"http": http_proxy, "https": http_proxy}
        return session
    if channel == CHANNEL_CLEARNET:
        return session
    raise UpdateCheckError(f"Неизвестный канал обновлений: {channel!r}")


def _manifest_url_for_channel(channel: str) -> str:
    if channel == CHANNEL_I2P:
        return I2P_MANIFEST_URL
    if channel == CHANNEL_CLEARNET:
        return CLEARNET_MANIFEST_URL
    raise UpdateCheckError(f"Неизвестный канал обновлений: {channel!r}")


def check_for_updates(storage, channel: str | None = None) -> UpdateInfo:
    """
    Синхронно скачивает манифест и возвращает UpdateInfo. Вызывать из UI
    ТОЛЬКО по явному клику пользователя (кнопка "Проверить обновления") —
    в отдельном потоке, чтобы не подвешивать tkinter mainloop на время
    сетевого запроса (см. ui/gui_thread.py — там уже есть паттерн для
    запуска фоновых операций из tkinter-колбэков).

    Бросает UpdateCheckError с человекочитаемым сообщением при любой
    проблеме (сеть, формат манифеста, неподдерживаемая ОС, отсутствие
    артефакта под выбранный канал+платформу).
    """
    channel = channel or (storage.get_setting("update_channel", DEFAULT_CHANNEL) if storage else DEFAULT_CHANNEL)
    url = _manifest_url_for_channel(channel)
    session = _session_for_channel(channel, storage)

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise UpdateCheckError(
            f"Не удалось получить манифест обновлений ({channel}): {e}"
        ) from e

    try:
        manifest = resp.json()
    except ValueError as e:
        raise UpdateCheckError(f"Манифест обновлений повреждён (не JSON): {e}") from e

    for required_field in ("version", "artifacts"):
        if required_field not in manifest:
            raise UpdateCheckError(f"В манифесте отсутствует поле {required_field!r}")

    platform_key = _current_platform_key()
    channel_artifacts = manifest["artifacts"].get(channel)
    if not channel_artifacts:
        raise UpdateCheckError(
            f"В манифесте нет раздела артефактов для канала {channel!r} "
            f"(есть только: {list(manifest['artifacts'].keys())})"
        )
    artifact = channel_artifacts.get(platform_key)
    if not artifact or "url" not in artifact or "sha256" not in artifact:
        raise UpdateCheckError(
            f"В манифесте нет корректного артефакта для платформы "
            f"{platform_key!r} в канале {channel!r}"
        )

    remote_version = manifest["version"]
    is_newer = _parse_version(remote_version) > _parse_version(VERSION)

    return UpdateInfo(
        version=remote_version,
        changelog_short=manifest.get("changelog_short", ""),
        changelog_url=manifest.get("changelog_url", ""),
        download_url=artifact["url"],
        sha256=artifact["sha256"].lower(),
        is_newer=is_newer,
    )


def download_update(info: UpdateInfo, dest_dir: Path, storage, channel: str | None = None) -> Path:
    """
    Скачивает артефакт из info.download_url в dest_dir, проверяет SHA256.
    Возвращает путь к скачанному файлу. НЕ запускает его — это осознанно
    оставлено пользователю (см. модульный docstring).

    Маршрутизация запроса — тот же канал, что и для манифеста (если
    артефакт лежит на .i2p-адресе на i2p-канале, скачивание тоже пойдёт
    через прокси; для clearnet-канала — напрямую).

    Бросает UpdateCheckError, если скачанный файл не совпадает по SHA256
    с тем, что указан в манифесте — это единственная защита от подмены
    артефакта, обязательно не игнорировать эту ошибку в UI.
    """
    channel = channel or (storage.get_setting("update_channel", DEFAULT_CHANNEL) if storage else DEFAULT_CHANNEL)
    session = _session_for_channel(channel, storage)

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(urlparse(info.download_url).path).name or f"itubep-bridge-{info.version}"
    dest_path = dest_dir / filename

    try:
        with session.get(info.download_url, stream=True, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp:
            resp.raise_for_status()
            hasher = hashlib.sha256()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    hasher.update(chunk)
    except requests.RequestException as e:
        dest_path.unlink(missing_ok=True)
        raise UpdateCheckError(f"Не удалось скачать обновление: {e}") from e

    actual_sha256 = hasher.hexdigest().lower()
    if actual_sha256 != info.sha256:
        dest_path.unlink(missing_ok=True)
        raise UpdateCheckError(
            "Скачанный файл не прошёл проверку контрольной суммы "
            "(SHA256 не совпадает с указанным в манифесте) — файл удалён. "
            "Это может значить, что манифест или сам файл были подменены; "
            "не пытайтесь запускать этот файл, скачанный вручную из "
            "недоверенного источника."
        )

    return dest_path
