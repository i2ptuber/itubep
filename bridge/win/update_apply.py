"""
win/update_apply.py — применяет обновление на Windows.

Отличие от bridge/update_installer.py (Linux): на Linux артефакт обновления
— это исходники (tar.gz), которые накатываются поверх старого дерева и
прогоняются через install.sh заново. На Windows артефакт из манифеста
(см. updater.py, ключ artifacts.<channel>.windows) — это ГОТОВЫЙ ЕХЕ той же
природы, что и itubep-bridge-windows-vX.X.X.exe, который пользователь
изначально скачал (см. bridge/win/installer.py) — то есть "обновление" на
Windows это просто повторный запуск установщика новой версии: он сам
идемпотентно переустановит только то, что изменилось (i2psnark, если уже
на месте, не трогается — см. install_i2psnark()), перезапишет
itubep-bridge.exe новой версией и перезапустит сервисы через itubep-ctl.bat.

Как и на Linux (см. docstring update_installer.py) — установка запускается
НЕ тихо в фоне из процесса настроек, а отдельным видимым процессом:
  - установщику всё равно понадобится UAC-повышение (Program Files) —
    из фонового потока tkinter grab токен администратора не запросить
    прозрачно для пользователя, ShellExecuteW("runas") сам покажет диалог
    UAC поверх любого окна, это ожидаемо и нормально;
  - процесс должен пережить закрытие окна настроек (оно, скорее всего,
    будет закрыто/перезапущено вместе с мостом после установки).
"""

from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path

from updater import UpdateCheckError


def launch_update_installer_windows(installer_exe_path: Path, lang: str = "en") -> None:
    """
    Точка входа из UI (settings_window.py) для платформы Windows.

    installer_exe_path — путь к уже скачанному и проверенному по SHA256
    (см. updater.download_update) файлу itubep-bridge-windows-vX.X.X.exe.

    Просто запускает его — сам установщик (bridge/win/installer.py) уже
    умеет запрашивать UAC-повышение самостоятельно (см. relaunch_as_admin())
    и идемпотентно доустанавливает/обновляет то, что нужно. Ничего
    дополнительно останавливать/бэкапить здесь не требуется — это уже
    внутренняя логика самого installer.py (шаг "i2psnark уже установлен —
    пропускаю" и безусловная перезапись itubep-bridge.exe новой версией).
    """
    if not installer_exe_path.exists():
        raise UpdateCheckError(
            "Скачанный файл обновления не найден на диске." if lang == "ru"
            else "The downloaded update file was not found on disk."
        )
    if installer_exe_path.suffix.lower() != ".exe":
        raise UpdateCheckError(
            f"Неожиданный формат файла обновления для Windows: {installer_exe_path.name!r} "
            "(ожидался .exe)." if lang == "ru" else
            f"Unexpected update file format for Windows: {installer_exe_path.name!r} "
            "(expected .exe)."
        )

    try:
        # ShellExecuteW("runas", ...) — как и bridge/win/installer.py сам
        # делает при запуске без прав администратора; здесь запускаем явно
        # с "runas", чтобы UAC-диалог появился сразу, а не после того, как
        # только что скачанный установщик успеет мигнуть и перезапустить
        # себя сам (тот же эффект, но на один шаг короче и без мигания окна).
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(installer_exe_path), "", str(installer_exe_path.parent), 1
        )
        # ShellExecuteW возвращает значение <= 32 при ошибке (см. MSDN).
        if int(rc) <= 32:
            raise OSError(f"ShellExecuteW returned {rc}")
    except Exception as e:
        raise UpdateCheckError(
            f"Не удалось запустить установщик обновления: {e}\n"
            f"Запустите вручную: {installer_exe_path}" if lang == "ru" else
            f"Could not launch the update installer: {e}\n"
            f"Run it manually: {installer_exe_path}"
        ) from e
