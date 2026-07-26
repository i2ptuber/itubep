"""
gui_thread.py — единственный выделенный поток для всех tkinter-вызовов.

tkinter не потокобезопасен: создавать окна можно надёжно только из одного и
того же потока на протяжении всей жизни процесса. Вместо того чтобы гадать,
из какого потока (main/event-loop/background) вызывается диалог, все вызовы
диспетчеризуются через единую очередь в этот выделенный поток.
"""

from __future__ import annotations

import glob
import os
import queue
import threading

_task_queue: queue.Queue = queue.Queue()


def ensure_display() -> None:
    """
    Публичная обёртка над _ensure_display() — для кода, который создаёт
    tkinter-окна НЕ через call_in_gui_thread (см. ui/publish_dialogs.py:
    вызовы диалогов публикации идут туда напрямую из policy/authz.py, а не
    через очередь этого модуля, в отличие от ui/tkinter_dialog.py). Нужно
    вызывать явно в начале каждого такого метода, иначе автоопределение
    DISPLAY (см. docstring _ensure_display ниже) для них не сработает.
    """
    _ensure_display()


def _ensure_display() -> None:
    """
    При автозапуске через systemd --user (особенно с `loginctl enable-linger`
    — см. install.sh) юнит моста может стартовать РАНЬШЕ, чем у пользователя
    вообще появится графическая сессия: systemd-пользовательский менеджер
    запускается при загрузке ОС (linger), не дожидаясь входа в систему, а
    $DISPLAY попадает в его окружение только позже, когда стартует X/Wayland-
    сессия (импорт через systemd-environment-d-generator / graphical-
    session.target / вручную из xinitrc — по-разному в разных окружениях).

    Раз процесс моста уже успел запуститься к этому моменту без DISPLAY в
    окружении, он никогда сам его не увидит: переменные окружения читаются
    один раз при старте процесса и не обновляются заново из systemd задним
    числом (env, унаследованный при fork/exec, — не то же самое, что "текущее
    окружение systemd user manager"). Именно поэтому `systemctl --user
    restart itubep-bridge.service` после входа в систему чинит ситуацию (при
    рестарте процесс форкается заново и подхватывает уже актуальное
    окружение), а перезагрузка ПК её снова ломает: сервис при следующей
    загрузке опять стартует раньше сессии.

    Чтобы не зависеть от порядка стартов systemd-таргетов (который вдобавок
    по-разному реализован в GNOME/KDE/минимальных WM), проверяем и, если
    нужно, донастраиваем DISPLAY/XAUTHORITY заново перед КАЖДЫМ диалогом —
    а не один раз при старте процесса. tkinter читает os.environ["DISPLAY"]
    заново при каждом создании Tk() (через Xlib), так что подмена в
    os.environ уже запущенного процесса работает: это чинит проблему
    системно, без необходимости переустанавливать мост или менять юнит.
    """
    if os.environ.get("DISPLAY"):
        return  # уже есть валидное значение — ничего не трогаем

    # /tmp/.X11-unix/X<N> — сокет N-го X-дисплея (:N). Сортируем численно,
    # а не лексикографически ("X10" не должен оказаться раньше "X2").
    sockets = glob.glob("/tmp/.X11-unix/X*")

    def _display_num(path: str) -> int | None:
        suffix = path.rsplit("X", 1)[-1]
        return int(suffix) if suffix.isdigit() else None

    numbers = sorted(n for n in (_display_num(s) for s in sockets) if n is not None)
    if not numbers:
        return  # нет ни одного живого X-сокета — Wayland-only без XWayland
                 # или сессии вообще ещё нет; оставляем как есть, дальше
                 # tkinter упадёт с тем же понятным сообщением, что и раньше

    os.environ["DISPLAY"] = f":{numbers[0]}"

    # Без корректного XAUTHORITY соединение с DISPLAY есть, но не авторизовано
    # ("Authorization required" вместо "no display name") — пробуем
    # стандартное расположение, если явно ничего не задано.
    if not os.environ.get("XAUTHORITY"):
        default_xauth = os.path.expanduser("~/.Xauthority")
        if os.path.exists(default_xauth):
            os.environ["XAUTHORITY"] = default_xauth


def _worker():
    while True:
        fn, result_box, done_event = _task_queue.get()
        try:
            _ensure_display()
            result_box["value"] = fn()
        except Exception as e:
            result_box["error"] = e
        finally:
            done_event.set()


_worker_thread = threading.Thread(target=_worker, daemon=True, name="itubep-gui-thread")
_worker_thread.start()


def call_in_gui_thread(fn):
    """
    Выполняет fn() в выделенном GUI-потоке, блокирует вызывающий поток до
    получения результата. Безопасно вызывать из любого потока (event loop,
    background pairing-поток и т.п.) — реальный tkinter-код всегда исполняется
    в одном и том же потоке.
    """
    result_box: dict = {}
    done_event = threading.Event()
    _task_queue.put((fn, result_box, done_event))
    done_event.wait()

    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("value")
