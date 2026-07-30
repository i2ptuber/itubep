"""
win/tray_app.py — точка входа itubep-bridge.exe на Windows.

Заменяет прямой запуск transport/http_server.py: теперь itubep-bridge.exe —
это (1) сам HTTP-сервер моста, запущенный в фоновом потоке, плюс (2)
иконка в системном трее с меню управления, вместо голого консольного
процесса, которым раньше можно было управлять только через itubep-ctl.bat.

Режимы запуска (см. main() внизу):
  itubep-bridge.exe                 — обычный запуск: сервер + трей (то,
                                       что кладётся в автозагрузку/ярлыки).
  itubep-bridge.exe --show-settings — открыть окно настроек и выйти
                                       (используется и напрямую с ярлыка, и
                                       как то, что дергает пункт трей-меню
                                       "Настройки" — отдельным процессом,
                                       чтобы не мешать циклу событий трея).
  itubep-bridge.exe --show-pairings — то же самое для окна управления
                                       подключёнными сайтами/устройствами.

Почему настройки/пейринги открываются в ОТДЕЛЬНОМ процессе, а не как
tkinter-окно внутри уже работающего процесса трея: у pystray на Windows
свой блокирующий цикл обработки сообщений в потоке, где вызван icon.run();
tkinter тоже требует свой mainloop и по-хорошему должен жить в главном
потоке. Городить общий event loop на двоих — источник трудноуловимых
зависаний. Отдельный процесс — то же самое, что было раньше при запуске
"python3 -m ui.settings_window" как отдельной команды (см. install.sh) —
только вместо python-модуля запускается тот же самый .exe с флагом.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from PIL import Image, ImageDraw

import pystray

from win.paths import BRIDGE_EXE, CTL_SCRIPT, LOG_DIR

try:
    from i18n import get_language
    from policy.storage import PolicyStorage
    LANG = get_language(PolicyStorage())
except Exception:
    LANG = "en"

TXT = {
    "ru": {
        "open_settings": "Настройки",
        "manage_pairings": "Управление подключениями",
        "restart_bridge": "Перезапустить мост",
        "restart_snark": "Перезапустить i2psnark",
        "stop_snark": "Остановить i2psnark",
        "restart_all": "Перезапустить всё",
        "quit": "Выход",
        "tooltip_running": "ITubeP Bridge — работает",
    },
    "en": {
        "open_settings": "Settings",
        "manage_pairings": "Manage connections",
        "restart_bridge": "Restart bridge",
        "restart_snark": "Restart i2psnark",
        "stop_snark": "Stop i2psnark",
        "restart_all": "Restart everything",
        "quit": "Quit",
        "tooltip_running": "ITubeP Bridge — running",
    },
}[LANG if LANG in ("ru", "en") else "en"]


def _run_ctl(*args: str) -> None:
    """Запускает itubep-ctl.bat с заданными аргументами. ВАЖНО: сюда
    никогда не передаётся "stop-all"/"start-all" из этого модуля — эти
    команды внутри ctl.bat останавливают/поднимают в том числе сам
    itubep-bridge.exe через taskkill по имени образа, а трей — это И ЕСТЬ
    itubep-bridge.exe. Раньше отсюда так и вызывался "stop-all", что било
    taskkill'ом по самому себе прямо посреди обработки клика в меню —
    процесс падал вместо перезапуска. Для i2psnark (отдельный процесс,
    javaw.exe) это безопасно — используем "stop-snark"/"start-snark".
    Себя самого мост перезапускает только через _restart_self()."""
    if not CTL_SCRIPT.exists():
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cmd", "/c", str(CTL_SCRIPT), *args],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=60,
    )


def _make_icon_image() -> Image.Image:
    """Простая программная иконка (кружок), чтобы не тащить .ico как
    отдельный ассет и не усложнять сборку PyInstaller путями к ресурсам —
    рисуется на лету через PIL (уже обязательная зависимость моста)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(90, 60, 200, 255))
    draw.polygon(
        [(size * 0.4, size * 0.3), (size * 0.4, size * 0.7), (size * 0.72, size * 0.5)],
        fill=(255, 255, 255, 255),
    )
    return img


def _fresh_child_env() -> dict:
    """
    Окружение для запуска НОВОГО экземпляра этого же PyInstaller onefile
    .exe из-под себя самого.

    PyInstaller onefile при первом запуске распаковывает себя во временный
    каталог (%TEMP%\\_MEIxxxx) и прописывает его в собственных переменных
    окружения процесса, в т.ч. _MEIPASS2 — это то, как бутлоадер узнаёт
    "я уже распакован, вот где". Если наследовать это окружение как есть в
    дочерний процесс (обычное поведение subprocess.Popen), дочерний
    бутлоадер решает, что ему тоже не нужно распаковываться заново, и
    пытается работать из _MEIxxxx РОДИТЕЛЯ — который тот удаляет при
    выходе. Отсюда и была ошибка "Tcl data directory ..._MEI14322\\_tcl_data
    not found" при "Перезапустить мост": новый процесс всё ещё стартовал,
    когда старый уже подчистил свой временный каталог.
    Официальный воркэраунд PyInstaller — убрать _MEIPASS2 (и на всякий
    случай сопутствующие TCL/TK_LIBRARY, которые бутлоадер тоже мог
    выставить, указывая на тот же временный каталог) из окружения перед
    запуском дочернего процесса, тогда он распакуется в СВОЙ отдельный
    каталог самостоятельно.
    """
    env = os.environ.copy()
    for key in ("_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(key, None)
    return env


def _open_window(flag: str) -> None:
    """Открывает settings/pairings окно отдельным процессом того же .exe
    (см. docstring модуля — почему не в этом же процессе)."""
    subprocess.Popen([str(BRIDGE_EXE), flag], env=_fresh_child_env())


def _restart_self(then_ctl_args: list[tuple[str, ...]] = ()) -> None:
    """
    Перезапускает сам процесс моста (сервер+трей): поднимает новый
    экземпляр .exe (с чистым окружением, см. _fresh_child_env) и
    завершает текущий процесс. then_ctl_args — список наборов аргументов
    для itubep-ctl.bat, выполняемых ДО спауна нового экземпляра
    (используется "Перезапустить всё": сначала стоп/старт i2psnark, потом
    уже самоперезапуск моста — предсказуемый порядок операций).
    Пример: then_ctl_args=[("stop-snark",), ("start-snark",)]
    """
    for args in then_ctl_args:
        _run_ctl(*args)
    subprocess.Popen(
        [str(BRIDGE_EXE)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=_fresh_child_env(),
    )
    # os._exit — не sys.exit(): нужно завершиться немедленно, не дожидаясь
    # штатной остановки aiohttp-сервера в фоновом потоке (специального
    # graceful-shutdown пути из этого контекста нет, а ждать смысла нет —
    # новый процесс уже поднимается).
    os._exit(0)


def build_menu(icon: "pystray.Icon") -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(TXT["open_settings"], lambda: _open_window("--show-settings")),
        pystray.MenuItem(TXT["manage_pairings"], lambda: _open_window("--show-pairings")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(TXT["restart_bridge"], lambda: threading.Thread(
            target=lambda: _restart_self(), daemon=True
        ).start()),
        pystray.MenuItem(TXT["restart_snark"], lambda: threading.Thread(
            target=lambda: (_run_ctl("stop-snark"), _run_ctl("start-snark")), daemon=True
        ).start()),
        pystray.MenuItem(TXT["stop_snark"], lambda: threading.Thread(
            target=lambda: _run_ctl("stop-snark"), daemon=True
        ).start()),
        pystray.MenuItem(TXT["restart_all"], lambda: threading.Thread(
            target=lambda: _restart_self(then_ctl_args=[("stop-snark",), ("start-snark",)]), daemon=True
        ).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(TXT["quit"], lambda: _quit(icon)),
    )


def _quit(icon: "pystray.Icon") -> None:
    # Мост (себя) НЕ трогаем через ctl/taskkill — просто выходим сами ниже;
    # i2psnark — отдельный процесс, его на выходе останавливаем явно, иначе
    # пользователь решит, что "закрыл программу", а javaw.exe продолжит
    # висеть в фоне.
    _run_ctl("stop-snark")
    icon.stop()
    os._exit(0)


def _run_server_thread() -> None:
    """Поднимает сам HTTP-сервер моста (transport/http_server.py:run()) в
    фоновом потоке — тот же код, что раньше был единственным содержимым
    itubep-bridge.exe, теперь просто перенесён из главного потока в
    дочерний, чтобы главный поток был свободен под pystray.Icon.run()."""
    from transport.http_server import run as run_bridge_server
    run_bridge_server()


def main() -> int:
    args = sys.argv[1:]
    if "--show-settings" in args:
        from ui.settings_window import main as settings_main
        settings_main()
        return 0
    if "--show-pairings" in args:
        from ui.manage_pairings import ManagePairingsWindow
        ManagePairingsWindow().run()
        return 0

    server_thread = threading.Thread(target=_run_server_thread, daemon=True)
    server_thread.start()

    icon = pystray.Icon("itubep-bridge", _make_icon_image(), TXT["tooltip_running"])
    icon.menu = build_menu(icon)
    icon.run()  # блокирует главный поток до icon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
