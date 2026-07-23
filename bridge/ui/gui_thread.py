"""
gui_thread.py — единственный выделенный поток для всех tkinter-вызовов.

tkinter не потокобезопасен: создавать окна можно надёжно только из одного и
того же потока на протяжении всей жизни процесса. Вместо того чтобы гадать,
из какого потока (main/event-loop/background) вызывается диалог, все вызовы
диспетчеризуются через единую очередь в этот выделенный поток.
"""

from __future__ import annotations

import queue
import threading

_task_queue: queue.Queue = queue.Queue()


def _worker():
    while True:
        fn, result_box, done_event = _task_queue.get()
        try:
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
