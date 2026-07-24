"""
settings_window.py — standalone-окно настроек: язык, режим (тихий/подтверждение).
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
from i18n import t, set_language, get_language

MODE_SILENT = "silent"
MODE_CONFIRM = "confirm"


def main():
    storage = PolicyStorage()

    root = tk.Tk()
    root.title(t("settings.window_title"))
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    # --- Язык интерфейса моста (независим от языка сайта) ---
    ttk.Label(frame, text=t("settings.language_heading"), font=("Sans", 11, "bold")).pack(anchor="w")

    lang_var = tk.StringVar(value=get_language(storage))

    def on_language_change():
        set_language(lang_var.get(), storage)
        # Язык меняется мгновенно — пересоздаём окно, чтобы все подписи
        # перерисовались на новом языке без необходимости перезапуска.
        root.destroy()
        main()

    lang_row = ttk.Frame(frame)
    lang_row.pack(anchor="w", pady=(5, 0))
    ttk.Radiobutton(
        lang_row, text=t("settings.language_ru"),
        variable=lang_var, value="ru", command=on_language_change,
    ).pack(side="left")
    ttk.Radiobutton(
        lang_row, text=t("settings.language_en"),
        variable=lang_var, value="en", command=on_language_change,
    ).pack(side="left", padx=(15, 0))

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text=t("settings.mode_heading"), font=("Sans", 11, "bold")).pack(anchor="w")

    current_mode = storage.get_setting("mode", MODE_SILENT)
    mode_var = tk.StringVar(value=current_mode)

    def on_mode_change():
        storage.set_setting("mode", mode_var.get())

    ttk.Radiobutton(
        frame, text=t("settings.mode_silent"),
        variable=mode_var, value=MODE_SILENT, command=on_mode_change,
    ).pack(anchor="w", pady=(5, 0))
    ttk.Radiobutton(
        frame, text=t("settings.mode_confirm"),
        variable=mode_var, value=MODE_CONFIRM, command=on_mode_change,
    ).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text=t("settings.pairing_note_1"),
              font=("Sans", 9, "italic")).pack(anchor="w")
    ttk.Label(frame, text=t("settings.pairing_note_2"),
              font=("Sans", 9, "italic")).pack(anchor="w")

    ttk.Label(frame, text=t("settings.apply_note_1"),
              font=("Sans", 9, "italic")).pack(anchor="w", pady=(10, 0))
    ttk.Label(frame, text=t("settings.apply_note_2"),
              font=("Sans", 9, "italic")).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text=t("settings.proxy_heading"),
              font=("Sans", 11, "bold")).pack(anchor="w")
    ttk.Label(
        frame, text=t("settings.proxy_description"),
        font=("Sans", 9, "italic"), justify="left",
    ).pack(anchor="w", pady=(0, 5))

    proxy_var = tk.StringVar(value=storage.get_i2p_http_proxy())
    proxy_entry = ttk.Entry(frame, textvariable=proxy_var, width=40)
    proxy_entry.pack(anchor="w")

    def on_save_proxy():
        storage.set_i2p_http_proxy(proxy_var.get().strip())

    ttk.Button(frame, text=t("settings.save_proxy"), command=on_save_proxy).pack(anchor="w", pady=(5, 0))

    ttk.Separator(frame).pack(fill="x", pady=15)

    ttk.Label(frame, text=t("settings.trackers_heading"),
              font=("Sans", 11, "bold")).pack(anchor="w")
    ttk.Label(
        frame, text=t("settings.trackers_description"),
        font=("Sans", 9, "italic"), justify="left",
    ).pack(anchor="w", pady=(0, 5))

    trackers_text = tk.Text(frame, width=55, height=5)
    trackers_text.insert("1.0", "\n".join(storage.get_trackers()))
    trackers_text.pack(anchor="w")

    def on_save_trackers():
        raw = trackers_text.get("1.0", "end")
        trackers = [line.strip() for line in raw.splitlines() if line.strip()]
        storage.set_trackers(trackers)

    ttk.Button(frame, text=t("settings.save_trackers"), command=on_save_trackers).pack(anchor="w", pady=(5, 0))
    ttk.Label(
        frame, text=t("settings.restart_required"),
        font=("Sans", 9, "italic"),
    ).pack(anchor="w")

    ttk.Separator(frame).pack(fill="x", pady=15)

    def open_manage_pairings():
        ManagePairingsWindow(parent=root)

    ttk.Button(frame, text=t("settings.manage_pairings_btn"), command=open_manage_pairings).pack(anchor="w")

    ttk.Button(frame, text=t("settings.close"), command=root.destroy).pack(pady=(15, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
