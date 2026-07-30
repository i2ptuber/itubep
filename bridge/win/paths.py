"""
win/paths.py — общие константы путей установки для Windows.

Вынесено отдельно, чтобы installer.py (ставит) и tray_app.py (работает
после установки) смотрели на одни и те же пути без дублирования и риска
рассинхронизации при правках одного из файлов.
"""

from __future__ import annotations

import os
from pathlib import Path

INSTALL_ROOT = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ITubeP"
BRIDGE_DIR = INSTALL_ROOT / "bridge"
BRIDGE_EXE = BRIDGE_DIR / "itubep-bridge.exe"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ITubeP"
RUN_DIR = DATA_DIR / "run"
LOG_DIR = DATA_DIR / "logs"

# i2psnark-standalone НАМЕРЕННО ставится в %LOCALAPPDATA%, а не в Program
# Files рядом с bridge — в отличие от bridge (только читает свой .exe и
# bridge.env), i2psnark на каждом запуске пишет прямо в свой каталог
# (i2psnark/ — данные торрентов, logs/ — логи, i2psnark.config.d/ —
# настройки), а Program Files доступен на запись только с правами
# администратора. Установщик поднимает UAC один раз на само копирование
# файлов при установке, но повторные обычные (неэлевейтед) запуски
# i2psnark потом падали с "Access is denied" / "Data directory cannot be
# created" — см. обсуждение фикса. LOCALAPPDATA пишется без UAC всегда.
SNARK_DIR = DATA_DIR / "i2psnark-standalone"

BRIDGE_ENV_FILE = BRIDGE_DIR / "bridge.env"
CTL_SCRIPT = INSTALL_ROOT / "itubep-ctl.bat"

DEFAULT_SNARK_RPC_PORT = "8002"
