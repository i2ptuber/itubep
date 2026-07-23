"""
settings_window.py — standalone-окно настроек: режим (тихий/подтверждение).
Запускается отдельной командой, читает/пишет ту же SQLite БД, что и сервер.

Запуск: python3 -m ui.settings_window
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, ".")
from policy.storage import PolicyStorage
from ui.manage_pairings import ManagePairingsWindow

MODE_SILENT = "silent"
MODE_CONFIRM = "confirm"


def main():
    storage = PolicyStorage()

    root = tk.Tk()
    root.title("ITubeP Bridge — настройки")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Режим работы", font=("Sans", 11, "bold")).pack(anchor="w")

    current_mode = storage.get_setting("mode", MODE_SILENT)
    mode_var = tk.StringVar(value=current_mode)

    def on_mode_change():
        storage.set_setting("mode", mode_var.get())

    ttk.Radiobutton(
        frame, text="Тихий (без подтверждения каждого действия)",
        variable=mode_var, value=MODE_SILENT, command=on_mode_change,
    ).pack(anchor="w", pady=(5, 0))
    ttk.Radiobutton(
        frame, text="С подтверждением (каждое действие требует разрешения)",
        variable=mode_var, value=MODE_CONFIRM, command=on_mode_change,
    ).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text="Сопряжение сайтов всегда требует ручного",
              font=("Sans", 9, "italic")).pack(anchor="w")
    ttk.Label(frame, text="подтверждения независимо от режима выше.",
              font=("Sans", 9, "italic")).pack(anchor="w")

    ttk.Label(frame, text="Изменения применяются сразу — сервер моста",
              font=("Sans", 9, "italic")).pack(anchor="w", pady=(10, 0))
    ttk.Label(frame, text="читает настройки при каждом действии.",
              font=("Sans", 9, "italic")).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text="HTTP-прокси I2P (для публикации на .i2p-сайты)",
              font=("Sans", 11, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Через этот прокси мост ходит к сайту при публикации видео\n"
             "(регистрация канала, отправка манифеста и торрента) — requests\n"
             "не умеет резолвить .i2p-адреса напрямую, это не DNS. Для сайтов\n"
             "на localhost/127.0.0.1 (локальное тестирование) прокси не\n"
             "используется, независимо от этого поля. Дефолт 127.0.0.1:4444\n"
             "подходит и для i2pd, и для Java I2P со стандартными настройками.",
        font=("Sans", 9, "italic"), justify="left",
    ).pack(anchor="w", pady=(0, 5))

    proxy_var = tk.StringVar(value=storage.get_i2p_http_proxy())
    proxy_entry = ttk.Entry(frame, textvariable=proxy_var, width=40)
    proxy_entry.pack(anchor="w")

    def on_save_proxy():
        storage.set_i2p_http_proxy(proxy_var.get().strip())

    ttk.Button(frame, text="Сохранить прокси", command=on_save_proxy).pack(anchor="w", pady=(5, 0))

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text="Трекеры (announce), по одному URL на строку",
              font=("Sans", 11, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Без них поиск пиров идёт только через DHT/PEX i2psnark и может\n"
             "занимать долгое время. Возьмите живой список со страницы\n"
             "http://127.0.0.1:8002/i2psnark/configure (\"Trackers\") того же\n"
             "i2psnark, которым пользуется этот мост.",
        font=("Sans", 9, "italic"), justify="left",
    ).pack(anchor="w", pady=(0, 5))

    trackers_text = tk.Text(frame, width=55, height=5)
    trackers_text.insert("1.0", "\n".join(storage.get_trackers()))
    trackers_text.pack(anchor="w")

    def on_save_trackers():
        raw = trackers_text.get("1.0", "end")
        trackers = [line.strip() for line in raw.splitlines() if line.strip()]
        storage.set_trackers(trackers)

    ttk.Button(frame, text="Сохранить трекеры", command=on_save_trackers).pack(anchor="w", pady=(5, 0))
    ttk.Label(
        frame, text="Требуется перезапуск сервера моста, чтобы изменения применились",
        font=("Sans", 9, "italic"),
    ).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=15)

    def open_manage_pairings():
        ManagePairingsWindow(parent=root)

    ttk.Button(frame, text="Разрешённые сайты...", command=open_manage_pairings).pack(anchor="w")
    
    ttk.Button(frame, text="Закрыть", command=root.destroy).pack(pady=(15, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
