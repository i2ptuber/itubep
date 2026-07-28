"""
update_installer.py — применяет уже скачанное и проверенное по SHA256
(см. updater.download_update) обновление: распаковывает архив, подменяет
код моста в корне репозитория (родитель BRIDGE_DIR) и заново прогоняет
install.sh, чтобы подтянуть новые python-зависимости и перегенерировать
itubep-ctl/systemd-юниты под новый код.

ПОЧЕМУ ЧЕРЕЗ ОТДЕЛЬНЫЙ ТЕРМИНАЛ, А НЕ ТИХО В ФОНЕ ИЗ ЭТОГО ПРОЦЕССА:
  - install.sh при необходимости вызывает sudo (apt-get install пакетов,
    JDK, копирование webapps/ для Java I2P и т.п., см. install.sh) — sudo
    должен уметь спросить пароль в интерактивном терминале (TTY). Из
    фонового потока tkinter-процесса, у которого обычно нет собственного
    TTY, это либо не сработает вообще, либо потребовало бы GUI-askpass
    поверх sudo — а это лишний непрозрачный слой между пользователем и
    тем, что реально происходит с правами root.
  - Сам install.sh может задавать интерактивные вопросы (выбор I2P-
    роутера и т.п., см. install.sh шаг 4) — их тоже нужно куда-то
    показать и откуда-то читать ответ. В обычном случае (роутер уже
    выбран при первой установке) install.sh их не задаст, но полагаться
    на это молча не стоит.
  - Это совпадает с общим принципом проекта (см. docstring updater.py):
    НИКАКОГО автоматического скачивания/установки без явного действия
    пользователя, и ничего важное не происходит молча за спиной — весь
    процесс обновления виден целиком, в отдельном окне терминала, а не
    прячется в логах фонового потока.

Мост НЕ пытается сам стать root и не хранит/не запрашивает пароль
пользователя напрямую — паролем управляет sudo в открытом терминале, как
обычно при ручном запуске install.sh.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from updater import UpdateCheckError

# Порядок — по распространённости. "x-terminal-emulator" — debian-alias,
# указывающий на то, что пользователь/дистрибутив уже настроил как терминал
# по умолчанию, поэтому стоит первым.
TERMINAL_CANDIDATES: list[tuple[str, list[str]]] = [
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("mate-terminal", ["-x"]),
    ("lxterminal", ["-e"]),
    ("tilix", ["-e"]),
    ("xterm", ["-e"]),
]


def find_terminal_emulator() -> tuple[str, list[str]] | None:
    """Возвращает (путь_к_бинарнику, аргументы_перед_командой) для первого
    найденного в PATH эмулятора терминала, или None, если не нашли ни
    одного — тогда установка не может быть проведена автоматически (см.
    launch_update_installer)."""
    for name, args in TERMINAL_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path, args
    return None


def _safe_extract_tar(tf: tarfile.TarFile, dest_dir: Path) -> None:
    """
    Защита от path traversal (например, файл в архиве с именем
    "../../../.bashrc") — SHA256, которым уже проверен сам файл архива
    (см. updater.download_update), защищает только от порчи/подмены ЦЕЛОГО
    файла, который мы ожидали получить, а не от вредоносного содержимого
    ВНУТРИ него, если сам манифест или сервер публикации были бы
    скомпрометированы. Не доверяем путям из архива вслепую.
    """
    dest_resolved = dest_dir.resolve()
    for member in tf.getmembers():
        member_path = (dest_dir / member.name).resolve()
        if member_path != dest_resolved and not str(member_path).startswith(str(dest_resolved) + "/"):
            raise UpdateCheckError(
                f"Архив обновления содержит подозрительный путь ({member.name!r}) — "
                f"установка остановлена, ничего не распаковано и не подменено."
            )
    tf.extractall(dest_dir)  # пути уже провалидированы выше


def _extract_archive(archive_path: Path, dest_dir: Path) -> Path:
    """
    Распаковывает архив с обновлением в dest_dir (пересоздаёт его заново,
    если он уже существует — например, от предыдущей неудачной попытки).
    Пока поддерживается только .tar.gz — сейчас это единственный формат
    linux-артефактов в этом проекте (windows-артефакты — .exe-инсталляторы,
    для них автоустановка не предлагается, см. _current_platform_key в
    updater.py и то, что вся эта логика опирается на bridge/install.sh,
    который есть только для linux).
    """
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)

    name = archive_path.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tf:
            _safe_extract_tar(tf, dest_dir)
    else:
        raise UpdateCheckError(
            f"Неизвестный формат архива обновления: {archive_path.name!r} "
            f"(автоустановка сейчас умеет только .tar.gz)."
        )
    return dest_dir


def _find_new_bridge_root(extracted_dir: Path) -> Path:
    """
    Ищет install.sh внутри распакованного архива и возвращает корень
    нового дерева репозитория (родитель папки bridge/, т.е. то же самое,
    что сейчас является родителем текущего BRIDGE_DIR). Архив может
    содержать как сам репозиторий "как есть" (bridge/install.sh в корне
    архива), так и одну обёрточную директорию сверху (типичное поведение
    архивов-снапшотов репозитория вида itubep-<версия>/bridge/install.sh)
    — поэтому ищем install.sh на любой глубине, а не полагаемся на
    фиксированную структуру архива.
    """
    matches = list(extracted_dir.rglob("bridge/install.sh"))
    if not matches:
        raise UpdateCheckError(
            "В скачанном архиве не найден bridge/install.sh — не могу "
            "автоматически установить обновление. Установите вручную из "
            f"{extracted_dir}."
        )
    # .../<repo_root>/bridge/install.sh -> repo_root это на два уровня выше
    return matches[0].parent.parent


def build_apply_script(new_repo_root: Path, old_repo_root: Path, ctl_script: Path) -> str:
    """
    Генерирует bash-скрипт, который реально проводит обновление — для
    запуска в отдельном терминале (см. docstring модуля выше, почему не в
    фоне этого процесса). Скрипт:
      1) останавливает мост/i2psnark через itubep-ctl, если он вообще
         существует (на самой первой установке, до install.sh, его ещё
         может не быть — это не ошибка);
      2) переносит текущий репозиторий в бэкап рядом (на случай отката);
      3) переносит распакованную новую версию на место старой — ВАЖНО:
         ровно по тому же пути old_repo_root, что и раньше, потому что на
         этот путь ссылаются уже сгенерированные itubep-ctl и
         systemd-юниты (см. install.sh, BRIDGE_DIR);
      4) заново прогоняет install.sh новой версии — он идемпотентен, не
         трогает уже накопленные пользовательские данные (i2psnark config,
         venv переиспользуется/обновляется) и сам перегенерирует
         itubep-ctl/systemd-юниты под новый код;
      5) при успехе — запускает всё обратно и удаляет бэкап; при ошибке —
         откатывается на бэкап и поднимает старую версию обратно, чтобы
         мост не остался в нерабочем состоянии посреди обновления.
    Каждый шаг echo'ится в терминал — процесс виден целиком, ничего не
    происходит молча.
    """
    new_repo_root_q = shlex.quote(str(new_repo_root))
    old_repo_root_q = shlex.quote(str(old_repo_root))
    ctl_q = shlex.quote(str(ctl_script))
    backup_dir_q = shlex.quote(f"{old_repo_root}.backup-{int(time.time())}")

    return f"""#!/usr/bin/env bash
# Автосгенерировано update_installer.py — можно смотреть, но не нужно
# редактировать вручную, файл перезаписывается при каждой попытке
# установки обновления.
set -uo pipefail

CTL={ctl_q}
OLD_ROOT={old_repo_root_q}
NEW_ROOT={new_repo_root_q}
BACKUP_DIR={backup_dir_q}

echo "=== ITubeP: установка обновления ==="
echo "Текущая версия:    $OLD_ROOT"
echo "Новая версия из:   $NEW_ROOT"
echo ""

if [ -x "$CTL" ]; then
    echo "--- Останавливаю мост и i2psnark перед обновлением ---"
    "$CTL" stop-all || true
else
    echo "--- itubep-ctl не найден ($CTL) — пропускаю остановку сервисов ---"
fi

echo "--- Делаю резервную копию текущей версии ---"
if ! mv "$OLD_ROOT" "$BACKUP_DIR"; then
    echo "ОШИБКА: не удалось создать резервную копию ($OLD_ROOT -> $BACKUP_DIR)."
    echo "Обновление остановлено, старая версия НЕ тронута."
    read -rp "Нажмите Enter, чтобы закрыть это окно..." _
    exit 1
fi

echo "--- Переношу новую версию на место старой ---"
if ! mv "$NEW_ROOT" "$OLD_ROOT"; then
    echo "ОШИБКА: не удалось перенести новую версию на место ($OLD_ROOT)."
    echo "Откатываюсь на резервную копию..."
    mv "$BACKUP_DIR" "$OLD_ROOT"
    [ -x "$CTL" ] && "$CTL" start-all
    echo "Старая версия восстановлена и перезапущена."
    read -rp "Нажмите Enter, чтобы закрыть это окно..." _
    exit 1
fi

echo "--- Запускаю install.sh новой версии (может спросить пароль sudo) ---"
echo ""
if ( cd "$OLD_ROOT/bridge" && ./install.sh ); then
    echo ""
    echo "=== Обновление установлено успешно ==="
    rm -rf "$BACKUP_DIR"
    echo "(резервная копия $BACKUP_DIR удалена)"
else
    echo ""
    echo "=== install.sh завершился с ошибкой — откатываюсь на предыдущую версию ==="
    rm -rf "$OLD_ROOT"
    mv "$BACKUP_DIR" "$OLD_ROOT"
    if [ -x "$CTL" ]; then
        ( cd "$OLD_ROOT/bridge" && ./install.sh ) || true
        "$CTL" start-all
    fi
    echo "Старая версия восстановлена и перезапущена. Ничего не потеряно."
    echo "Сообщите об ошибке выше разработчику (это не должно происходить)."
fi

echo ""
read -rp "Нажмите Enter, чтобы закрыть это окно..." _
"""


def launch_update_installer(archive_path: Path, bridge_dir: Path) -> Path:
    """
    Точка входа из UI (settings_window.py). Синхронная (быстрая, чисто
    локальная) часть — распаковка архива и поиск нового bridge/ внутри
    него — выполняется здесь и может бросить UpdateCheckError, если что-то
    не так ЕЩЁ ДО запуска терминала (архив битый/неизвестного формата,
    install.sh не найден внутри, не нашлось ни одного эмулятора терминала).

    Сам процесс обновления (остановка сервисов, подмена файлов, повторный
    install.sh) запускается В ОТДЕЛЬНОМ ТЕРМИНАЛЕ и продолжается уже
    независимо от этого процесса — в том числе переживёт закрытие окна
    настроек, что важно, поскольку install.sh перезапускает и сам мост,
    то есть процесс, из которого это окно настроек вообще было открыто.

    Возвращает путь к сгенерированному скрипту (используется только для
    сообщения пользователю в случае, если запустить терминал всё же не
    удалось — тогда можно предложить команду для запуска вручную).
    """
    old_repo_root = bridge_dir.parent
    work_dir = Path.home() / ".local" / "share" / "itubep-bridge"
    staging_dir = work_dir / "update-staging"
    work_dir.mkdir(parents=True, exist_ok=True)

    extracted = _extract_archive(archive_path, staging_dir)
    new_repo_root = _find_new_bridge_root(extracted)

    ctl_script = Path.home() / ".local" / "bin" / "itubep-ctl"

    script = build_apply_script(new_repo_root, old_repo_root, ctl_script)
    script_path = work_dir / "apply_update.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    terminal = find_terminal_emulator()
    if terminal is None:
        raise UpdateCheckError(
            "Не нашёл ни одного эмулятора терминала (gnome-terminal/konsole/"
            "xterm/...) в PATH, а install.sh может спросить пароль sudo — "
            "это нужно делать в интерактивном терминале, не в фоне. "
            f"Запустите вручную в своём терминале:\n  bash {script_path}"
        )

    terminal_bin, terminal_args = terminal
    subprocess.Popen(
        [terminal_bin, *terminal_args, "bash", str(script_path)],
        start_new_session=True,  # не привязываем жизненный цикл к процессу
        # настроек — он может закрыться (или быть убит перезапускаемым
        # install.sh мостом) раньше, чем терминал закончит обновление.
    )
    return script_path
