"""
settings_window.py — standalone-окно настроек: язык, режим (тихий/подтверждение).
Запускается отдельной командой, читает/пишет ту же SQLite БД, что и сервер.

Запуск: python3 -m ui.settings_window
"""

from __future__ import annotations

import platform
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

# update_installer (Linux, накатывает .tar.gz поверх install.sh) и
# win.update_apply (Windows, запускает скачанный itubep-bridge-windows-*.exe
# через UAC) — платформозависимые реализации одного шага: "установить уже
# скачанное и проверенное по SHA256 обновление" (см. on_install_update ниже).
IS_WINDOWS = platform.system().lower() == "windows"
if IS_WINDOWS:
    from win import update_apply as update_installer_platform
else:
    import update_installer as update_installer_platform

MODE_SILENT = "silent"
MODE_CONFIRM = "confirm"


def main():
    from ui.gui_thread import ensure_display
    ensure_display()
    storage = PolicyStorage()

    root = tk.Tk()
    root.title(t("settings.window_title"))
    # Окну разрешаем ресайзиться (было resizable(False, False) — из-за
    # этого при добавлении новых секций типа "Обновления" контент банально
    # переставал влезать на экран, и достать до нижних кнопок было нечем).
    # Дополнительно оборачиваем содержимое в Canvas+Scrollbar — так даже
    # если контента больше, чем помещается по высоте на конкретном экране,
    # до него всегда можно долистать колёсиком/скроллбаром, а не только
    # руками менять размер окна.
    root.resizable(True, True)

    # Стартовая высота — с запасом под контент, но не выше экрана (на
    # маленьких экранах/при мелком масштабировании ОС окно и так откроется
    # ужатым по высоте экрана, а не будет вылезать за его пределы).
    screen_height = root.winfo_screenheight()
    initial_height = min(760, screen_height - 100)
    root.geometry(f"520x{initial_height}")
    root.minsize(420, 300)

    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)

    canvas = tk.Canvas(container, highlightthickness=0)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    frame = ttk.Frame(canvas, padding=20)
    frame_window = canvas.create_window((0, 0), window=frame, anchor="nw")

    def _on_frame_configure(event=None):
        # Область прокрутки — весь фактический размер содержимого frame.
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(event):
        # Растягиваем frame по ширине canvas, чтобы контент не оставался
        # уже, чем видимая область, при ресайзе окна вбок.
        canvas.itemconfig(frame_window, width=event.width)

    frame.bind("<Configure>", _on_frame_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _on_mousewheel(event):
        # event.delta: Windows/macOS — кратно 120; трактуем как есть.
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_scroll_up(event):
        canvas.yview_scroll(-1, "units")

    def _on_scroll_down(event):
        canvas.yview_scroll(1, "units")

    def _bind_mousewheel(event):
        # Привязываем колесо мыши только пока курсор над этим окном —
        # bind_all("<MouseWheel>") без ограничения по времени жизни мог бы
        # тихо перехватывать скролл и в других открытых окнах приложения
        # (например ManagePairingsWindow), если они окажутся под курсором
        # позже. Отвязываем в _unbind_mousewheel при уходе курсора.
        canvas.bind_all("<MouseWheel>", _on_mousewheel)   # Windows/macOS
        canvas.bind_all("<Button-4>", _on_scroll_up)       # Linux, колесо вверх
        canvas.bind_all("<Button-5>", _on_scroll_down)     # Linux, колесо вниз

    def _unbind_mousewheel(event):
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    canvas.bind("<Enter>", _bind_mousewheel)
    canvas.bind("<Leave>", _unbind_mousewheel)

    def _on_window_destroy(event):
        # Подстраховка: если окно закрывается, пока курсор ещё "внутри"
        # canvas (значит _bind_mousewheel уже сработал), обычный <Leave>
        # может не успеть вызваться — тогда bind_all("<MouseWheel>"/...)
        # остались бы висеть на уровне интерпретатора и ссылаться на уже
        # уничтоженный canvas, ломая скролл в других открытых окнах моста
        # (например ManagePairingsWindow — тот же Tk-интерпретатор, см.
        # tk.Toplevel(parent) в manage_pairings.py).
        if event.widget is root:
            _unbind_mousewheel(event)

    root.bind("<Destroy>", _on_window_destroy)

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
        # Результат предыдущей проверки относится к другому каналу — прячем
        # кнопку "Скачать" и просим проверить заново, чтобы не оставлять
        # устаревшую (для другого канала) информацию видимой в UI.
        _last_update_info["value"] = None
        _last_update_info["channel"] = None
        _last_downloaded_archive["path"] = None
        download_button.pack_forget()
        install_button.pack_forget()
        update_status_var.set("")

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

    install_button = ttk.Button(frame, text=t("settings.update_install_btn"))
    # install_button.pack() вызывается только после успешного скачивания —
    # см. _on_download_done ниже. Отдельная кнопка (а не автозапуск сразу
    # после скачивания) — установка останавливает работающие сервисы и
    # может спросить пароль sudo, это должно быть явным действием
    # пользователя, а не следствием клика по "Скачать".

    _last_update_info = {"value": None, "channel": None}
    _last_downloaded_archive = {"path": None}

    def _on_check_done(info=None, error=None, channel=None):
        check_button.state(["!disabled"])
        install_button.pack_forget()
        _last_downloaded_archive["path"] = None
        if error is not None:
            update_status_var.set(t("settings.update_error") % str(error))
            return
        # Запоминаем канал, с которым реально была сделана проверка (и
        # получен info.download_url), а не читаем его заново из
        # update_channel_var — пользователь мог успеть переключить радио-
        # кнопку между "Check for updates" и "Download" (например, сначала
        # проверить через i2p, потом переключиться на clearnet и нажать
        # Download) — тогда download_url всё ещё вёл бы на .i2p-адрес, а
        # сессия для скачивания создавалась бы уже под clearnet (без
        # прокси), и запрос на .i2p-хост уходил бы напрямую, в обход I2P.
        _last_update_info["value"] = info
        _last_update_info["channel"] = channel
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

        checked_channel = update_channel_var.get()

        def worker():
            try:
                info = updater.check_for_updates(storage, channel=checked_channel)
                root.after(0, lambda info=info: _on_check_done(info=info, channel=checked_channel))
            except Exception as e:
                root.after(0, lambda e=e: _on_check_done(error=e, channel=checked_channel))

        threading.Thread(target=worker, daemon=True).start()

    check_button.configure(command=on_check_updates)

    def on_download_update():
        info = _last_update_info["value"]
        download_channel = _last_update_info["channel"]
        if info is None or download_channel is None:
            return
        download_button.state(["disabled"])
        update_status_var.set(t("settings.update_downloading"))

        def worker():
            try:
                dest_dir = Path.home() / "Downloads"
                if not dest_dir.exists():
                    dest_dir = Path.home()
                # channel фиксирован тем, что было при проверке (см.
                # _on_check_done) — намеренно НЕ update_channel_var.get(),
                # иначе при переключении радиокнопки между проверкой и
                # скачиванием запрос уйдёт не туда (см. баг с i2p-адресом
                # напрямую без прокси).
                path = updater.download_update(info, dest_dir, storage, channel=download_channel)
                root.after(0, lambda path=path: _on_download_done(path=path))
            except Exception as e:
                root.after(0, lambda e=e: _on_download_done(error=e))

        def _on_download_done(path=None, error=None):
            download_button.state(["!disabled"])
            if error is not None:
                update_status_var.set(t("settings.update_error") % str(error))
                messagebox.showerror(t("settings.update_heading"), str(error))
                return
            update_status_var.set(t("settings.update_downloaded") % str(path))
            _last_downloaded_archive["path"] = path
            install_button.pack(anchor="w", pady=(5, 0))
            if info.changelog_url:
                webbrowser.open(info.changelog_url)

        threading.Thread(target=worker, daemon=True).start()

    download_button.configure(command=on_download_update)

    def on_install_update():
        archive_path = _last_downloaded_archive["path"]
        if archive_path is None:
            return
        # Явное подтверждение: установка останавливает работающие сервисы
        # моста/i2psnark и может спросить пароль sudo (см. install.sh) —
        # ничего из этого не должно происходить без явного "да" от
        # пользователя, даже если он уже нажал "Скачать" ранее.
        if not messagebox.askyesno(
            t("settings.update_heading"),
            t("settings.update_install_confirm"),
        ):
            return

        install_button.state(["disabled"])
        update_status_var.set(t("settings.update_installing"))

        # BRIDGE_DIR — папка, где реально лежит этот settings_window.py
        # (bridge/ui/settings_window.py -> bridge/), а не CWD: скрипт может
        # быть запущен и через полный путь из itubep-ctl, не обязательно
        # из уже находящейся в bridge/ рабочей директории.
        bridge_dir = Path(__file__).resolve().parent.parent

        def worker():
            try:
                if IS_WINDOWS:
                    # Windows: скачанный файл — это сам итоговый установщик
                    # (itubep-bridge-windows-vX.X.X.exe), просто запускаем
                    # его с UAC-повышением; script_path здесь не применим.
                    update_installer_platform.launch_update_installer_windows(
                        archive_path, lang=get_language(storage)
                    )
                    root.after(0, lambda: _on_install_launched(script_path=archive_path))
                else:
                    script_path = update_installer_platform.launch_update_installer(
                        archive_path, bridge_dir, lang=get_language(storage)
                    )
                    root.after(0, lambda: _on_install_launched(script_path=script_path))
            except Exception as e:
                root.after(0, lambda e=e: _on_install_launched(error=e))

        def _on_install_launched(script_path=None, error=None):
            install_button.state(["!disabled"])
            if error is not None:
                update_status_var.set(t("settings.update_error") % str(error))
                messagebox.showerror(t("settings.update_heading"), str(error))
                return
            update_status_var.set(t("settings.update_install_launched"))
            # Дальше мост, скорее всего, сам перезапустится (install.sh в
            # конце поднимает сервисы заново) — это окно настроек тоже
            # может быть убито вместе со старым процессом моста, закрываем
            # его сами заранее, чтобы не остаться зомби-окном поверх уже
            # перезапущенного моста.
            root.destroy()

        threading.Thread(target=worker, daemon=True).start()

    install_button.configure(command=on_install_update)

    ttk.Button(frame, text=t("settings.close"), command=root.destroy).pack(pady=(15, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
