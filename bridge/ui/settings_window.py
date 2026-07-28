"""
settings_window.py — standalone-окно настроек: язык, режим (тихий/подтверждение).
Запускается отдельной командой, читает/пишет ту же SQLite БД, что и сервер.

Запуск: python3 -m ui.settings_window
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, ".")
from policy.storage import PolicyStorage
from ui.manage_pairings import ManagePairingsWindow
from i18n import t, set_language, get_language
import updater
from __version__ import VERSION

MODE_SILENT = "silent"
MODE_CONFIRM = "confirm"


def main():
    from ui.gui_thread import ensure_display
    ensure_display()
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

    # --- Показ NSFW-контента ---
    # Не влияет на публикацию (отметка при публикации на сайте всегда
    # обязательна, см. site/templates/publish.html) — только на то, будет
    # ли этот конкретный человек ВИДЕТЬ отмеченный контент в поиске/на
    # главной/на страницах каналов сайтов, к которым подключён этот мост
    # (см. bridge/transport/http_server.py:nsfw_preference и
    # site/app/main.py:_show_nsfw). Собственные NSFW-видео автор всегда
    # видит в своей студии независимо от этого переключателя.
    ttk.Label(frame, text=t("settings.nsfw_heading"), font=("Sans", 11, "bold")).pack(anchor="w")

    show_nsfw_var = tk.BooleanVar(value=storage.get_show_nsfw())

    def on_nsfw_change():
        storage.set_show_nsfw(show_nsfw_var.get())

    ttk.Checkbutton(
        frame, text=t("settings.nsfw_checkbox"),
        variable=show_nsfw_var, command=on_nsfw_change,
    ).pack(anchor="w", pady=(5, 0))

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

    ttk.Separator(frame).pack(fill="x", pady=15)

    # --- Обновления ---
    # Только ручная проверка по кнопке, никакого автоматического скачивания
    # или запуска — см. docstring updater.py. Канал (i2p/clearnet) выбирает
    # сам пользователь: у этих двух путей разная модель приватности, решать
    # молча за него не наше дело.
    ttk.Label(frame, text=t("settings.update_heading"), font=("Sans", 11, "bold")).pack(anchor="w")
    ttk.Label(
        frame, text=t("settings.update_current_version") % VERSION,
        font=("Sans", 9),
    ).pack(anchor="w", pady=(2, 5))

    update_channel_var = tk.StringVar(
        value=storage.get_setting("update_channel", updater.DEFAULT_CHANNEL)
    )

    def on_update_channel_change():
        storage.set_setting("update_channel", update_channel_var.get())

    channel_row = ttk.Frame(frame)
    channel_row.pack(anchor="w")
    ttk.Radiobutton(
        channel_row, text=t("settings.update_channel_i2p"),
        variable=update_channel_var, value=updater.CHANNEL_I2P,
        command=on_update_channel_change,
    ).pack(side="left")
    ttk.Radiobutton(
        channel_row, text=t("settings.update_channel_clearnet"),
        variable=update_channel_var, value=updater.CHANNEL_CLEARNET,
        command=on_update_channel_change,
    ).pack(side="left", padx=(15, 0))

    update_status_var = tk.StringVar(value="")
    update_status_label = ttk.Label(
        frame, textvariable=update_status_var, font=("Sans", 9, "italic"),
    )
    update_status_label.pack(anchor="w", pady=(5, 0))

    check_button = ttk.Button(frame, text=t("settings.update_check_btn"))
    check_button.pack(anchor="w", pady=(5, 0))

    download_button = ttk.Button(frame, text=t("settings.update_download_btn"))
    # download_button.pack() вызывается только когда есть что скачивать —
    # см. _on_check_done ниже.

    _last_update_info = {"value": None}

    def _on_check_done(info=None, error=None):
        check_button.state(["!disabled"])
        if error is not None:
            update_status_var.set(t("settings.update_error") % str(error))
            return
        _last_update_info["value"] = info
        if info.is_newer:
            status = t("settings.update_available") % info.version
            if info.changelog_short:
                status += "\n" + info.changelog_short
            update_status_var.set(status)
            download_button.pack(anchor="w", pady=(5, 0))
        else:
            update_status_var.set(t("settings.update_up_to_date"))
            download_button.pack_forget()

    def on_check_updates():
        check_button.state(["disabled"])
        update_status_var.set(t("settings.update_checking"))
        download_button.pack_forget()

        def worker():
            try:
                info = updater.check_for_updates(storage, channel=update_channel_var.get())
                root.after(0, lambda: _on_check_done(info=info))
            except Exception as e:
                root.after(0, lambda: _on_check_done(error=e))

        threading.Thread(target=worker, daemon=True).start()

    check_button.configure(command=on_check_updates)

    def on_download_update():
        info = _last_update_info["value"]
        if info is None:
            return
        download_button.state(["disabled"])
        update_status_var.set(t("settings.update_downloading"))

        def worker():
            try:
                dest_dir = Path.home() / "Downloads"
                if not dest_dir.exists():
                    dest_dir = Path.home()
                path = updater.download_update(info, dest_dir, storage, channel=update_channel_var.get())
                root.after(0, lambda: _on_download_done(path=path))
            except Exception as e:
                root.after(0, lambda: _on_download_done(error=e))

        def _on_download_done(path=None, error=None):
            download_button.state(["!disabled"])
            if error is not None:
                update_status_var.set(t("settings.update_error") % str(error))
                messagebox.showerror(t("settings.update_heading"), str(error))
                return
            update_status_var.set(t("settings.update_downloaded") % str(path))
            if info.changelog_url:
                webbrowser.open(info.changelog_url)

        threading.Thread(target=worker, daemon=True).start()

    download_button.configure(command=on_download_update)

    ttk.Button(frame, text=t("settings.close"), command=root.destroy).pack(pady=(15, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
