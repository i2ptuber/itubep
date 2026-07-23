"""
publish_dialogs.py — GUI-шаги мастера публикации: подтверждение, предупреждение
о ключе, выбор файла, ввод названия/описания.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog


class PublishDialogs:
    def show_key_warning(self) -> bool:
        result = {"approved": False}
        root = tk.Tk()
        root.title("Создание канала")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        for line in [
            "У вас ещё нет канала для публикации.",
            "Сейчас будет создан новый криптографический ключ канала.",
            "",
            "ВАЖНО: если вы потеряете этот ключ, канал будет",
            "утрачен НАВСЕГДА — восстановить его никаким",
            "способом нельзя.",
        ]:
            ttk.Label(frame, text=line).pack(anchor="w")

        checkbox_var = tk.BooleanVar(value=False)

        def toggle():
            approve_btn.config(state="normal" if checkbox_var.get() else "disabled")

        ttk.Checkbutton(
            frame, text="Я понимаю и хочу продолжить", variable=checkbox_var, command=toggle,
        ).pack(anchor="w", pady=(10, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(15, 0), fill="x")

        def on_approve():
            result["approved"] = True
            root.destroy()

        def on_deny():
            root.destroy()

        ttk.Button(btn_frame, text="Отмена", command=on_deny).pack(side="right", padx=5)
        approve_btn = ttk.Button(btn_frame, text="Создать канал", command=on_approve, state="disabled")
        approve_btn.pack(side="right")

        root.mainloop()
        return result["approved"]

    def prompt_channel_name(self) -> str | None:
        return self._prompt_text("Название канала", "Введите название вашего канала:")

    def prompt_new_password(self, migration: bool = False) -> str | None:
        """
        Запрашивает новый пароль для шифрования ключа канала, с
        подтверждением (повторный ввод) — опечатка в пароле для нового
        ключа означала бы, что дальше расшифровать его будет нечем, канал
        потерян точно так же необратимо, как при потере самого ключа.

        migration=True — для миграции уже существующего plaintext-ключа
        (немного другой текст, объясняющий, что происходит и почему).
        """
        result = {"password": None}
        root = tk.Tk()
        root.title("Защита ключа канала паролем")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        if migration:
            intro_lines = [
                "Ключ вашего канала сейчас хранится на диске без шифрования.",
                "Задайте пароль, чтобы защитить его — с этого момента ключ",
                "будет храниться зашифрованным, и мост будет спрашивать этот",
                "пароль при каждом запуске (один раз за сессию).",
            ]
        else:
            intro_lines = [
                "Этот пароль будет защищать ваш ключ канала на диске.",
                "Мост будет спрашивать его один раз при первой публикации",
                "после каждого запуска моста.",
            ]
        for line in intro_lines:
            ttk.Label(frame, text=line).pack(anchor="w")

        ttk.Label(
            frame,
            text="ВАЖНО: этот пароль нигде не сохраняется. Если вы его\n"
                 "забудете — расшифровать ключ канала будет невозможно,\n"
                 "канал будет потерян так же необратимо, как при потере\n"
                 "самого ключа.",
            foreground="#a00",
        ).pack(anchor="w", pady=(10, 10))

        ttk.Label(frame, text="Пароль:").pack(anchor="w")
        pw1_var = tk.StringVar()
        pw1_entry = ttk.Entry(frame, textvariable=pw1_var, show="*", width=40)
        pw1_entry.pack(anchor="w", fill="x")
        pw1_entry.focus()

        ttk.Label(frame, text="Повторите пароль:").pack(anchor="w", pady=(8, 0))
        pw2_var = tk.StringVar()
        pw2_entry = ttk.Entry(frame, textvariable=pw2_var, show="*", width=40)
        pw2_entry.pack(anchor="w", fill="x")

        error_label = ttk.Label(frame, text="", foreground="#a00")
        error_label.pack(anchor="w", pady=(5, 0))

        def on_submit():
            pw1, pw2 = pw1_var.get(), pw2_var.get()
            if not pw1:
                error_label.config(text="Пароль не может быть пустым")
                return
            if pw1 != pw2:
                error_label.config(text="Пароли не совпадают")
                pw2_var.set("")
                return
            if len(pw1) < 8:
                error_label.config(text="Пароль слишком короткий (минимум 8 символов)")
                return
            result["password"] = pw1
            root.destroy()

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(15, 0), fill="x")
        ttk.Button(btn_frame, text="Отмена", command=root.destroy).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Подтвердить", command=on_submit).pack(side="right")

        root.bind("<Return>", lambda e: on_submit())
        root.mainloop()
        return result["password"]

    def prompt_unlock_password(self, attempt: int = 1) -> str | None:
        """Запрашивает пароль для разблокировки уже существующего ключа."""
        result = {"password": None}
        root = tk.Tk()
        root.title("Разблокировка ключа канала")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        if attempt > 1:
            ttk.Label(
                frame, text=f"Неверный пароль (попытка {attempt}/3). Попробуйте снова:",
                foreground="#a00",
            ).pack(anchor="w")
        else:
            ttk.Label(frame, text="Введите пароль для разблокировки ключа канала:").pack(anchor="w")

        pw_var = tk.StringVar()
        pw_entry = ttk.Entry(frame, textvariable=pw_var, show="*", width=40)
        pw_entry.pack(anchor="w", fill="x", pady=(8, 0))
        pw_entry.focus()

        def on_submit():
            result["password"] = pw_var.get()
            root.destroy()

        ttk.Button(frame, text="Разблокировать", command=on_submit).pack(pady=(15, 0))
        root.bind("<Return>", lambda e: on_submit())
        root.mainloop()
        return result["password"] or None

    def confirm_publish_request(self, origin: str) -> bool:
        result = {"approved": False}
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.title("Запрос на публикацию")

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Сайт {origin} запрашивает публикацию нового видео.").pack(anchor="w")
        ttk.Label(frame, text="Продолжить?").pack(anchor="w", pady=(5, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(15, 0), fill="x")

        def on_yes():
            result["approved"] = True
            root.destroy()

        ttk.Button(btn_frame, text="Отмена", command=root.destroy).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Продолжить", command=on_yes).pack(side="right")

        root.mainloop()
        return result["approved"]

    def choose_video_file(self) -> str | None:
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title="Выберите видеофайл",
            filetypes=[("Видео", "*.mp4 *.mkv *.mov *.webm"), ("Все файлы", "*.*")],
        )
        root.destroy()
        return path or None

    def prompt_title_description(self) -> tuple[str, str] | None:
        result = {"title": None, "description": None}
        root = tk.Tk()
        root.title("Данные видео")
        root.attributes("-topmost", True)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Название:").pack(anchor="w")
        title_var = tk.StringVar()
        ttk.Entry(frame, textvariable=title_var, width=50).pack(anchor="w", fill="x")

        ttk.Label(frame, text="Описание:").pack(anchor="w", pady=(10, 0))
        desc_text = tk.Text(frame, width=50, height=6)
        desc_text.pack(anchor="w", fill="x")

        def on_publish():
            result["title"] = title_var.get().strip()
            result["description"] = desc_text.get("1.0", "end").strip()
            root.destroy()

        ttk.Button(frame, text="Опубликовать", command=on_publish).pack(pady=(15, 0))

        root.mainloop()

        if not result["title"]:
            return None
        return result["title"], result["description"]

    def _prompt_text(self, title: str, prompt: str) -> str | None:
        result = {"value": None}
        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=prompt).pack(anchor="w")

        var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=var, width=40)
        entry.pack(fill="x", pady=(5, 10))
        entry.focus()

        def on_ok():
            result["value"] = var.get().strip()
            root.destroy()

        ttk.Button(frame, text="OK", command=on_ok).pack()
        root.mainloop()
        return result["value"]
