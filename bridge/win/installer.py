"""
win/installer.py — установщик ITubeP Bridge для Windows.

Windows-аналог bridge/install.sh. Собирается через PyInstaller в
itubep-bridge-windows-vX.X.X.exe (см. scripts/build_windows_release.sh) —
именно этот .exe пользователь скачивает и запускает.

В отличие от install.sh, этот установщик НЕ собирает i2psnark из исходников
на машине пользователя (ant/JDK на Windows — лишняя тяжёлая зависимость для
конечного пользователя). Вместо этого он использует уже готовый бандл
(core jar/war + свой embedded JRE, см. scripts/build-i2psnark-bundle.sh),
который либо лежит рядом с установщиком, либо скачивается с GitHub Releases.

Шаги (в целом соответствуют шагам install.sh 4/5/7/8/9, но без 1/2/3/6,
которые для Windows не нужны — нет системного пакетного менеджера и сборки
из исходников):
  1) Проверка прав администратора (нужны для записи в Program Files и,
     если используется, для системного сервиса i2pd) — если их нет,
     установщик перезапускает себя через UAC (ShellExecuteW "runas").
  2) Определение I2P-роутера (i2pd / Java I2P) — по стандартным путям
     установки и запущенным процессам/службам.
  3) Если роутер не найден — предложить пользователю поставить i2pd или
     Java I2P (открываем официальную страницу загрузки; сами официальные
     инсталляторы EXE не запускаем в фоне без спроса — как и на Linux,
     ничего не должно ставиться молча) либо указать путь вручную.
  4) Проверка i2psnark standalone (%LOCALAPPDATA%\\ITubeP\\i2psnark-standalone
     — НЕ Program Files, см. win/paths.py: i2psnark сам пишет туда данные/
     логи на каждом запуске, для Program Files для этого нужны права
     администратора на каждый обычный запуск, что не годится).
     Если его нет — ищем архив бандла (папка установщика, родительская
     папка, Downloads пользователя), либо просим указать вручную (через
     проводник), либо предлагаем скачать с GitHub.
  5) Установка i2psnark в %LOCALAPPDATA%\\ITubeP\\i2psnark-standalone и моста
     в Program Files\\ITubeP\\bridge (сам itubep-bridge.exe уже собран
     PyInstaller'ом заранее и просто копируется, см. build_windows_release.sh).
  6) bridge.env-эквивалент (см. install.sh) — записываются
     ITUBEP_SNARK_RPC_URL/ITUBEP_SNARK_WEB_URL под нужный порт.
  7) Автозапуск: ярлык в Startup-папке текущего пользователя + генерация
     itubep-ctl.bat (Windows-аналог itubep-ctl из install.sh).
  8) Запуск сейчас.

Идемпотентность: как и install.sh, повторный запуск безопасен — уже
установленный i2psnark (со своими i2psnark.config.d/ и данными) не
перезаписывается, повторно спрашивается только то, чего не хватает.
"""

from __future__ import annotations

import ctypes
import locale
import os
import shutil
import subprocess
import sys
import time
import webbrowser
import winreg
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# ---------------------------------------------------------------------------
# i18n — то же правило, что и в install.sh: русский, если системная
# локаль русская, иначе английский.
# ---------------------------------------------------------------------------
def _detect_lang() -> str:
    try:
        loc = locale.getdefaultlocale()[0] or ""
    except Exception:
        loc = ""
    env_lang = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
    probe = (loc + env_lang).lower()
    return "ru" if probe.startswith("ru") or "_ru_" in probe or probe.startswith("ru_") else "en"


LANG = _detect_lang()

MSG_RU = {
    "title": "Установка ITubeP Bridge",
    "need_admin": "Для установки нужны права администратора (запись в Program Files, служба i2pd).",
    "elevating": "Перезапускаю установщик с правами администратора...",
    "step_router": "Шаг 1/5: определение I2P-роутера",
    "router_javai2p": "Обнаружен Java I2P router.",
    "router_i2pd": "Обнаружен i2pd.",
    "router_both_title": "Обнаружены оба роутера: Java I2P и i2pd.",
    "router_both_prompt": "Какой использовать?",
    "router_none_title": "I2P-роутер не найден",
    "router_none_prompt": (
        "Не нашёл ни i2pd, ни Java I2P.\n\n"
        "Да — открыть страницу загрузки i2pd, поставить его вручную, затем нажать «Проверить снова».\n"
        "Нет — открыть страницу загрузки Java I2P.\n"
        "Отмена — указать путь к уже установленному роутеру вручную."
    ),
    "router_recheck": "Проверить снова",
    "router_manual_i2pd": "Укажите папку установки i2pd (где лежит i2pd.exe):",
    "router_manual_javai2p": "Укажите папку установки Java I2P (где лежит i2p.exe / router.config):",
    "router_cancelled": "Установка отменена — настройте роутер и запустите установщик снова.",
    "step_snark": "Шаг 2/5: i2psnark standalone",
    "snark_already": "i2psnark уже установлен в %s — пропускаю (данные и config.d не трогаю).",
    "snark_search_local": "Ищу архив бандла рядом с установщиком...",
    "snark_found_local": "Нашёл: %s",
    "snark_not_found_title": "Бандл i2psnark не найден",
    "snark_not_found_prompt": (
        "Не нашёл архив i2psnark-bundle (ни рядом с установщиком, ни в Downloads).\n\n"
        "Да — скачать бандл с GitHub Releases автоматически.\n"
        "Нет — указать файл архива вручную (через проводник).\n"
        "Отмена — пропустить установку i2psnark (мост можно доустановить это позже)."
    ),
    "snark_downloading": "Скачиваю бандл i2psnark с GitHub...",
    "snark_download_fail": "Не удалось скачать бандл автоматически: %s",
    "snark_pick_archive": "Выберите архив i2psnark-bundle (.zip)",
    "snark_extracting": "Распаковываю бандл в %s...",
    "snark_installed": "i2psnark standalone установлен в %s",
    "snark_skipped": "Установка i2psnark пропущена по выбору пользователя.",
    "step_bridge": "Шаг 3/5: установка моста",
    "bridge_installed": "Мост установлен в %s",
    "step_env": "Шаг 4/5: настройка подключения моста к i2psnark",
    "env_written": "Настройки RPC записаны в %s (порт %s)",
    "step_autostart": "Шаг 5/5: автозапуск",
    "autostart_done": "Ярлык автозапуска создан: %s",
    "shortcuts_done": "Ярлыки созданы: %s (Рабочий стол), %s (Пуск)",
    "ctl_written": "Управляющий скрипт (запасной вариант, без GUI): %s",
    "starting_now": "Запускаю сейчас...",
    "done_title": "Готово",
    "done_text": (
        "ITubeP Bridge установлен и запущен.\n\n"
        "Иконка в системном трее (обычно рядом с часами) — оттуда: настройки, "
        "управление подключёнными сайтами, перезапуск моста/i2psnark/всего, выход.\n"
        "Ярлык для повторного запуска — на Рабочем столе и в меню Пуск "
        "(«ITubeP Bridge»).\n\n"
        "Если иконка в трее не нужна/недоступна — тот же набор команд без GUI: "
        "%s status | start-all | stop-all"
    ),
    "error_title": "Ошибка установки",
    "yes": "Да", "no": "Нет", "cancel": "Отмена", "browse": "Обзор...",
}

MSG_EN = {
    "title": "ITubeP Bridge Setup",
    "need_admin": "Administrator rights are required (writing to Program Files, the i2pd service).",
    "elevating": "Relaunching the installer with administrator rights...",
    "step_router": "Step 1/5: detecting I2P router",
    "router_javai2p": "Java I2P router detected.",
    "router_i2pd": "i2pd detected.",
    "router_both_title": "Both routers detected: Java I2P and i2pd.",
    "router_both_prompt": "Which one should be used?",
    "router_none_title": "No I2P router found",
    "router_none_prompt": (
        "Could not find i2pd or Java I2P.\n\n"
        "Yes — open the i2pd download page, install it manually, then click Recheck.\n"
        "No — open the Java I2P download page.\n"
        "Cancel — point to an already-installed router manually."
    ),
    "router_recheck": "Recheck",
    "router_manual_i2pd": "Point to the i2pd install folder (where i2pd.exe lives):",
    "router_manual_javai2p": "Point to the Java I2P install folder (where i2p.exe / router.config lives):",
    "router_cancelled": "Installation cancelled — set up the router and run the installer again.",
    "step_snark": "Step 2/5: i2psnark standalone",
    "snark_already": "i2psnark is already installed at %s — skipping (not touching data/config.d).",
    "snark_search_local": "Looking for a bundle archive next to the installer...",
    "snark_found_local": "Found: %s",
    "snark_not_found_title": "i2psnark bundle not found",
    "snark_not_found_prompt": (
        "Could not find an i2psnark-bundle archive (next to the installer or in Downloads).\n\n"
        "Yes — download the bundle from GitHub Releases automatically.\n"
        "No — pick the archive file manually (via Explorer).\n"
        "Cancel — skip installing i2psnark (you can install it later)."
    ),
    "snark_downloading": "Downloading the i2psnark bundle from GitHub...",
    "snark_download_fail": "Could not download the bundle automatically: %s",
    "snark_pick_archive": "Select the i2psnark-bundle archive (.zip)",
    "snark_extracting": "Extracting the bundle to %s...",
    "snark_installed": "i2psnark standalone installed at %s",
    "snark_skipped": "i2psnark installation skipped by user choice.",
    "step_bridge": "Step 3/5: installing the bridge",
    "bridge_installed": "Bridge installed at %s",
    "step_env": "Step 4/5: connecting the bridge to i2psnark",
    "env_written": "RPC settings written to %s (port %s)",
    "step_autostart": "Step 5/5: autostart",
    "autostart_done": "Autostart shortcut created: %s",
    "shortcuts_done": "Shortcuts created: %s (Desktop), %s (Start Menu)",
    "ctl_written": "Control script (no-GUI fallback): %s",
    "starting_now": "Starting now...",
    "done_title": "Done",
    "done_text": (
        "ITubeP Bridge is installed and running.\n\n"
        "System tray icon (usually near the clock) — from there: settings, "
        "manage connected sites, restart bridge/i2psnark/everything, quit.\n"
        "A shortcut to relaunch it is on the Desktop and in the Start Menu "
        "(\"ITubeP Bridge\").\n\n"
        "If the tray icon isn't available — the same controls without a GUI: "
        "%s status | start-all | stop-all"
    ),
    "error_title": "Installation error",
    "yes": "Yes", "no": "No", "cancel": "Cancel", "browse": "Browse...",
}

M = MSG_RU if LANG == "ru" else MSG_EN

GITHUB_REPO = "i2ptuber/itubep"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

from win.paths import (
    INSTALL_ROOT, SNARK_DIR, BRIDGE_DIR, BRIDGE_EXE as INSTALLED_BRIDGE_EXE,
    DATA_DIR, RUN_DIR, LOG_DIR, BRIDGE_ENV_FILE, CTL_SCRIPT, DEFAULT_SNARK_RPC_PORT,
)


# ============================================================================
# Утилиты
# ============================================================================
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> None:
    """Перезапускает текущий exe/скрипт через UAC и завершает этот процесс."""
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    exe = sys.executable
    script = sys.argv[0]
    if getattr(sys, "frozen", False):
        # Собранный PyInstaller-EXE — sys.executable это он сам.
        target, args = exe, params
    else:
        target, args = exe, f'"{script}" {params}'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", target, args, None, 1)
    sys.exit(0)


def bundled_resource(name: str) -> Path:
    """
    Путь к файлу, вшитому в PyInstaller-сборку через --add-data (см.
    scripts/build_windows_release.sh) — например, itubep-bridge.exe и
    (опционально) сам i2psnark-bundle.zip, если он был доступен на момент
    сборки установщика. При запуске НЕ из PyInstaller (напр. для отладки
    из исходников) ищем рядом со скриптом.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "payload" / name


def which_service(name: str) -> bool:
    try:
        out = subprocess.run(
            ["sc", "query", name], capture_output=True, text=True, timeout=10
        )
        return out.returncode == 0
    except Exception:
        return False


def tasklist_has(image_name: str) -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
            capture_output=True, text=True, timeout=10,
        )
        return image_name.lower() in out.stdout.lower()
    except Exception:
        return False


# ============================================================================
# GUI shell — простое консольно-подобное окно лога + диалоги подтверждения.
# Не пытаемся сделать полноценный wizard — задача установщика вторична по
# отношению к самому мосту, где UI уже сложнее (см. bridge/ui/*).
# ============================================================================
class InstallerWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(M["title"])
        self.root.geometry("720x480")
        self.text = tk.Text(self.root, wrap="word", state="disabled", font=("Consolas", 10))
        self.text.pack(fill="both", expand=True, padx=8, pady=8)
        self.root.update()

    def log(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")
        self.root.update()

    def ask_yesnocancel(self, title: str, prompt: str):
        return messagebox.askyesnocancel(title, prompt, parent=self.root)

    def ask_yesno(self, title: str, prompt: str) -> bool:
        return messagebox.askyesno(title, prompt, parent=self.root)

    def error(self, text: str) -> None:
        messagebox.showerror(M["error_title"], text, parent=self.root)

    def info(self, title: str, text: str) -> None:
        messagebox.showinfo(title, text, parent=self.root)

    def pick_folder(self, title: str) -> str | None:
        return filedialog.askdirectory(title=title, parent=self.root) or None

    def pick_file(self, title: str, filetypes) -> str | None:
        return filedialog.askopenfilename(title=title, filetypes=filetypes, parent=self.root) or None

    def close(self) -> None:
        self.root.destroy()


# ============================================================================
# Шаг 1: определение I2P-роутера
# ============================================================================
@dataclass
class RouterInfo:
    mode: str  # "i2pd" | "javai2p"
    console_port: str = "7657"  # только для javai2p


def _find_i2pd() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "i2pd" / "i2pd.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "i2pd" / "i2pd.exe",
    ]
    for c in candidates:
        if c.exists():
            return c.parent
    if which_service("i2pd") or tasklist_has("i2pd.exe"):
        return Path(os.environ.get("ProgramFiles", "")) / "i2pd"
    return None


def _find_javai2p() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "i2p",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "i2p",
        Path.home() / "i2p",
    ]
    for c in candidates:
        if (c / "i2p.exe").exists() or (c / "router.config").exists():
            return c
    if which_service("I2PSvc") or tasklist_has("i2p.exe") or tasklist_has("javaw.exe"):
        # javaw.exe слишком общий (любой Java-процесс) — не считаем это
        # достаточным сигналом само по себе, только вместе со службой.
        if which_service("I2PSvc"):
            return Path(os.environ.get("ProgramFiles", "")) / "i2p"
    return None


def _guess_javai2p_console_port(install_dir: Path) -> str:
    cfg = install_dir / "router.config"
    if cfg.exists():
        try:
            for line in cfg.read_text(errors="ignore").splitlines():
                if line.strip().startswith("clientApp.0.args") and "port=" in line:
                    for part in line.split():
                        if part.startswith("port="):
                            return part.split("=", 1)[1].strip()
                if line.strip().startswith("routerconsole.port"):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return "7657"


def _enable_i2pd_i2cp(i2pd_dir: Path, win: InstallerWindow) -> None:
    """Как и install.sh шаг 5 — включает i2cp.enabled в i2pd.conf, если ещё
    не включён. Не рестартит службу сама — просит перезапустить вручную
    (i2pd на Windows чаще запущен как обычный процесс/трей-иконка, а не
    служба, останавливать чужой процесс без спроса не будем)."""
    conf_candidates = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "i2pd" / "i2pd.conf",
        i2pd_dir / "i2pd.conf",
    ]
    conf = next((c for c in conf_candidates if c.exists()), None)
    if conf is None:
        win.log(f"[!] i2pd.conf {'не найден' if LANG=='ru' else 'not found'} — "
                 f"{'проверьте вручную (i2cp.enabled=true) и перезапустите роутер.' if LANG=='ru' else 'check manually (i2cp.enabled=true) and restart the router.'}")
        return
    text = conf.read_text(errors="ignore")
    if "i2cp.enabled" in text and "i2cp.enabled = false" not in text.replace(" ", ""):
        win.log("i2cp.enabled OK")
        return
    try:
        with open(conf, "a", encoding="utf-8") as f:
            f.write("\n[i2cp]\nenabled = true\n")
        win.log(("Включил i2cp.enabled в " if LANG == "ru" else "Enabled i2cp.enabled in ") + str(conf) +
                 (" — перезапустите i2pd." if LANG == "ru" else " — restart i2pd."))
    except PermissionError:
        win.log(f"[!] {'Нет прав на запись в' if LANG=='ru' else 'No write access to'} {conf} — "
                 f"{'включите i2cp.enabled вручную.' if LANG=='ru' else 'enable i2cp.enabled manually.'}")


def detect_router(win: InstallerWindow) -> RouterInfo | None:
    win.log(M["step_router"])
    i2pd_dir = _find_i2pd()
    javai2p_dir = _find_javai2p()

    if i2pd_dir and javai2p_dir:
        win.log(M["router_both_title"])
        choice = win.ask_yesnocancel(M["router_both_title"], M["router_both_prompt"] +
                                      f"\n\n{M['yes']} = Java I2P   {M['no']} = i2pd")
        if choice is None:
            return None
        if choice:
            port = _guess_javai2p_console_port(javai2p_dir)
            return RouterInfo(mode="javai2p", console_port=port)
        _enable_i2pd_i2cp(i2pd_dir, win)
        return RouterInfo(mode="i2pd")

    if javai2p_dir:
        win.log(M["router_javai2p"])
        port = _guess_javai2p_console_port(javai2p_dir)
        return RouterInfo(mode="javai2p", console_port=port)

    if i2pd_dir:
        win.log(M["router_i2pd"])
        _enable_i2pd_i2cp(i2pd_dir, win)
        return RouterInfo(mode="i2pd")

    # Ничего не нашли
    while True:
        choice = win.ask_yesnocancel(M["router_none_title"], M["router_none_prompt"])
        if choice is True:
            webbrowser.open("https://i2pd.website/")
            if win.ask_yesno(M["router_none_title"], M["router_recheck"] + "?"):
                i2pd_dir = _find_i2pd()
                if i2pd_dir:
                    _enable_i2pd_i2cp(i2pd_dir, win)
                    return RouterInfo(mode="i2pd")
                continue
            return None
        if choice is False:
            webbrowser.open("https://geti2p.net/en/download")
            folder = win.pick_folder(M["router_manual_javai2p"])
            if folder:
                port = _guess_javai2p_console_port(Path(folder))
                return RouterInfo(mode="javai2p", console_port=port)
            continue
        win.log(M["router_cancelled"])
        return None


# ============================================================================
# Шаг 2: i2psnark standalone
# ============================================================================
def _search_bundle_locally() -> Path | None:
    win_exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    search_dirs = [win_exe_dir, win_exe_dir.parent, Path.home() / "Downloads"]
    patterns = ["i2psnark-bundle*.zip", "itubep-i2psnark*.zip", "i2psnark-bundle*.tar.gz"]
    for d in search_dirs:
        if not d.exists():
            continue
        for pat in patterns:
            hits = sorted(d.glob(pat))
            if hits:
                return hits[0]
    return None


def _download_bundle(dest_dir: Path, win: InstallerWindow) -> Path | None:
    import requests  # локальный импорт — не нужен, если пользователь сам указал файл

    win.log(M["snark_downloading"])
    try:
        resp = requests.get(GITHUB_API_LATEST, timeout=30)
        resp.raise_for_status()
        release = resp.json()
        asset = next(
            (a for a in release.get("assets", [])
             if "i2psnark-bundle" in a["name"].lower() and a["name"].lower().endswith(".zip")),
            None,
        )
        if not asset:
            raise RuntimeError("i2psnark-bundle*.zip not found in latest GitHub release assets")
        url = asset["browser_download_url"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(urlparse(url).path).name
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest
    except Exception as e:
        win.error(M["snark_download_fail"] % str(e))
        return None


def install_i2psnark(win: InstallerWindow) -> bool:
    """Возвращает True, если i2psnark установлен (или уже был), False — если
    пользователь осознанно пропустил этот шаг."""
    win.log(M["step_snark"])

    if (SNARK_DIR / "i2psnark.war").exists() or (SNARK_DIR / "i2psnark.jar").exists() or list(SNARK_DIR.glob("i2psnark*.jar")):
        win.log(M["snark_already"] % SNARK_DIR)
        return True

    win.log(M["snark_search_local"])
    archive = _search_bundle_locally()
    if archive:
        win.log(M["snark_found_local"] % archive)
    else:
        choice = win.ask_yesnocancel(M["snark_not_found_title"], M["snark_not_found_prompt"])
        if choice is True:
            archive = _download_bundle(DATA_DIR / "downloads", win)
            if archive is None:
                return False
        elif choice is False:
            picked = win.pick_file(M["snark_pick_archive"], [("ZIP archives", "*.zip"), ("All files", "*.*")])
            if not picked:
                win.log(M["snark_skipped"])
                return False
            archive = Path(picked)
        else:
            win.log(M["snark_skipped"])
            return False

    win.log(M["snark_extracting"] % SNARK_DIR)
    SNARK_DIR.mkdir(parents=True, exist_ok=True)
    if str(archive).lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            # Архив может содержать обёрточную папку i2psnark-bundle/ сверху —
            # разворачиваем на один уровень, если она единственная в корне.
            names = zf.namelist()
            top_dirs = {n.split("/")[0] for n in names if "/" in n}
            if len(top_dirs) == 1:
                prefix = top_dirs.pop() + "/"
                for member in zf.infolist():
                    if member.filename == prefix:
                        continue
                    target_name = member.filename[len(prefix):] if member.filename.startswith(prefix) else member.filename
                    if not target_name:
                        continue
                    target_path = SNARK_DIR / target_name
                    if member.is_dir():
                        target_path.mkdir(parents=True, exist_ok=True)
                        continue
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            else:
                zf.extractall(SNARK_DIR)
    else:
        import tarfile
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(SNARK_DIR)

    win.log(M["snark_installed"] % SNARK_DIR)
    return True


# ============================================================================
# Шаг 3: установка самого моста
# ============================================================================
def install_bridge(win: InstallerWindow) -> None:
    win.log(M["step_bridge"])
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    src = bundled_resource("itubep-bridge.exe")
    if not src.exists():
        raise RuntimeError(f"itubep-bridge.exe not found in installer payload ({src})")
    shutil.copy2(src, BRIDGE_DIR / "itubep-bridge.exe")
    win.log(M["bridge_installed"] % BRIDGE_DIR)


# ============================================================================
# Шаг 4: bridge.env — аналог bridge.env из install.sh
# ============================================================================
def write_bridge_env(router: RouterInfo | None, snark_installed: bool, win: InstallerWindow) -> None:
    win.log(M["step_env"])
    if not snark_installed:
        return
    port = DEFAULT_SNARK_RPC_PORT if (router is None or router.mode == "i2pd") else router.console_port
    BRIDGE_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_ENV_FILE.write_text(
        "# Generated by the ITubeP Windows installer — do not edit by hand, re-run the installer instead.\n"
        f"ITUBEP_SNARK_RPC_URL=http://127.0.0.1:{port}/transmission/rpc\n"
        f"ITUBEP_SNARK_WEB_URL=http://127.0.0.1:{port}/i2psnark/\n",
        encoding="utf-8",
    )
    win.log(M["env_written"] % (BRIDGE_ENV_FILE, port))


# ============================================================================
# Шаг 5: автозапуск + control script (Windows-аналог itubep-ctl)
# ============================================================================
CTL_BAT_TEMPLATE = r"""@echo off
REM Auto-generated by the ITubeP Windows installer — do not edit by hand,
REM re-run the installer to regenerate.
setlocal enabledelayedexpansion

set "BRIDGE_EXE={bridge_exe}"
set "SNARK_LAUNCH={snark_launch}"
set "BRIDGE_ENV={bridge_env}"
set "RUN_DIR={run_dir}"
set "LOG_DIR={log_dir}"

if not exist "%RUN_DIR%" mkdir "%RUN_DIR%" >nul 2>&1
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1

if "%1"=="start-all" goto start_all
if "%1"=="stop-all" goto stop_all
if "%1"=="status" goto status
if "%1"=="start-bridge" goto start_bridge
if "%1"=="stop-bridge" goto stop_bridge
if "%1"=="start-snark" goto start_snark
if "%1"=="stop-snark" goto stop_snark
goto usage

:start_all
call :start_snark
call :start_bridge
goto :eof

:stop_all
call :stop_bridge
call :stop_snark
goto :eof

:start_snark
if "%SNARK_LAUNCH%"=="" goto :eof
tasklist /FI "IMAGENAME eq javaw.exe" | find /I "javaw.exe" >nul
if "%errorlevel%"=="0" (echo i2psnark: already running & goto :eof)
REM ВАЖНО: "start ... > file" перенаправляет вывод самого start.exe
REM (пустой, он сразу возвращает управление), а НЕ вывод запущенного
REM процесса — start создаёт отдельный процесс со своими хендлами.
REM Перенаправление нужно завернуть ВНУТРЬ команды, которую start
REM запускает (через cmd /c "... > file 2>&1"), иначе log всегда пустой.
start "" /D "{snark_dir}" /B cmd /c ""%SNARK_LAUNCH%" > "%LOG_DIR%\snark.log" 2>&1"
echo i2psnark: started
goto :eof

:stop_snark
taskkill /IM javaw.exe /F >nul 2>&1
echo i2psnark: stopped
goto :eof

:start_bridge
tasklist /FI "IMAGENAME eq itubep-bridge.exe" | find /I "itubep-bridge.exe" >nul
if "%errorlevel%"=="0" (echo bridge: already running & goto :eof)
for /f "usebackq tokens=*" %%A in ("%BRIDGE_ENV%") do (
    echo %%A | findstr /B "#" >nul || set "%%A"
)
REM см. комментарий у :start_snark — тот же фикс перенаправления.
start "" /B cmd /c ""%BRIDGE_EXE%" > "%LOG_DIR%\bridge.log" 2>&1"
echo bridge: started
goto :eof

:stop_bridge
taskkill /IM itubep-bridge.exe /F >nul 2>&1
echo bridge: stopped
goto :eof

:status
tasklist /FI "IMAGENAME eq itubep-bridge.exe" | find /I "itubep-bridge.exe" >nul && echo bridge: running || echo bridge: stopped
tasklist /FI "IMAGENAME eq javaw.exe" | find /I "javaw.exe" >nul && echo i2psnark: running (javaw.exe) || echo i2psnark: stopped
goto :eof

:usage
echo Usage: itubep-ctl.bat {{start-all^|stop-all^|status^|start-bridge^|stop-bridge^|start-snark^|stop-snark}}
goto :eof
"""


def _find_snark_launcher() -> str:
    for name in ("launch-i2psnark.bat", "i2psnark.bat"):
        if (SNARK_DIR / name).exists():
            return name
    # Фолбэк — запуск через bundled runtime напрямую, если готового батника
    # в бандле нет (см. build-i2psnark-bundle.sh: раздел про Windows-лаунчер,
    # который скрипт создаёт сам при сборке под Windows).
    return ""


def write_ctl_script() -> Path:
    snark_launch = _find_snark_launcher()
    content = CTL_BAT_TEMPLATE.format(
        bridge_exe=BRIDGE_DIR / "itubep-bridge.exe",
        snark_launch=snark_launch,
        snark_dir=SNARK_DIR,
        bridge_env=BRIDGE_ENV_FILE,
        run_dir=RUN_DIR,
        log_dir=LOG_DIR,
    )
    CTL_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    CTL_SCRIPT.write_text(content, encoding="utf-8")
    return CTL_SCRIPT


def _create_lnk_shortcut(lnk_path: Path, target: Path, workdir: Path, description: str = "") -> None:
    """
    Создаёт настоящий .lnk-ярлык через WScript.Shell COM-объект, вызванный
    из PowerShell (powershell.exe есть в любой Windows из коробки) — чтобы
    не тащить pywin32 только ради этого. Используется и для ярлыка на
    Рабочем столе, и в меню Пуск.
    """
    lnk_path.parent.mkdir(parents=True, exist_ok=True)
    ps_script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{target}';"
        "$s.WorkingDirectory = '{workdir}';"
        "$s.Description = '{desc}';"
        "$s.Save()"
    ).format(
        lnk=str(lnk_path).replace("'", "''"),
        target=str(target).replace("'", "''"),
        workdir=str(workdir).replace("'", "''"),
        desc=description.replace("'", "''"),
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        check=True, capture_output=True, text=True, timeout=30,
    )


def create_user_shortcuts() -> tuple[Path, Path]:
    """Ярлык на Рабочем столе и в меню Пуск, указывающие на сам
    itubep-bridge.exe (трей+сервер, см. win/tray_app.py) — чтобы
    пользователь мог запустить/переоткрыть мост вручную, а не только
    полагаться на автозапуск."""
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "ITubeP Bridge.lnk"
    start_menu = (
        Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        / "ITubeP Bridge.lnk"
    )
    for lnk in (desktop, start_menu):
        _create_lnk_shortcut(lnk, INSTALLED_BRIDGE_EXE, BRIDGE_DIR, "ITubeP Bridge")
    return desktop, start_menu


def create_startup_shortcut() -> Path:
    """Ярлык в Startup-папке текущего пользователя — Windows-эквивалент
    systemd --user autostart / cron @reboot из install.sh. Используем
    простейший .bat-ярлык вместо COM-объекта WScript.Shell (избегаем
    зависимости от pywin32 в установщике)."""
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    bat = startup_dir / "itubep-bridge-autostart.bat"
    bat.write_text(f'@echo off\r\nstart "" /MIN "{CTL_SCRIPT}" start-all\r\n', encoding="utf-8")
    return bat


# ============================================================================
# main
# ============================================================================
def main() -> int:
    if not is_admin():
        relaunch_as_admin()
        return 0

    win = InstallerWindow()
    try:
        router = detect_router(win)
        if router is None:
            win.info(M["title"], M["router_cancelled"])
            return 1

        snark_ok = install_i2psnark(win)
        install_bridge(win)
        write_bridge_env(router, snark_ok, win)

        win.log(M["step_autostart"])
        ctl = write_ctl_script()
        win.log(M["ctl_written"] % ctl)
        shortcut = create_startup_shortcut()
        win.log(M["autostart_done"] % shortcut)
        try:
            desktop_lnk, start_menu_lnk = create_user_shortcuts()
            win.log(M["shortcuts_done"] % (desktop_lnk, start_menu_lnk))
        except Exception as e:
            # Не фатально — PowerShell/WScript.Shell недоступен в
            # экзотических урезанных окружениях; ctl.bat всё ещё работает
            # как запасной путь управления, поэтому не прерываем установку.
            win.log(f"[!] {'Не удалось создать ярлыки' if LANG=='ru' else 'Could not create shortcuts'}: {e}")

        win.log(M["starting_now"])
        subprocess.Popen(["cmd", "/c", str(ctl), "start-all"], creationflags=subprocess.CREATE_NO_WINDOW)

        win.info(M["done_title"], M["done_text"] % ctl)
        return 0
    except Exception as e:
        win.error(str(e))
        return 1
    finally:
        win.close()


if __name__ == "__main__":
    sys.exit(main())
