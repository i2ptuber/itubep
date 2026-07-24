"""
tkinter_dialog.py — нативные диалоги подтверждения для BridgePolicy.
Все методы диспетчеризуют реальную отрисовку в единый GUI-поток (см.
gui_thread.py) — безопасно вызывать из любого потока приложения.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

from .gui_thread import call_in_gui_thread

sys.path.insert(0, ".")
from i18n import t


class TkinterPairingDialog:
    def show_pairing_request(self, origin: str, code: str) -> bool:
        return call_in_gui_thread(lambda: self._show_pairing_request_impl(origin, code))

    def show_confirm_action(self, origin: str, action_description: str) -> bool:
        return call_in_gui_thread(lambda: self._show_confirm_action_impl(origin, action_description))

    def show_brute_force_warning(self, origin: str, attempt_count: int) -> bool:
        """
        Возвращает True, если пользователь выбрал добавить origin в блеклист.
        Показывается один раз на каждый пройденный порог попыток (см.
        PairingManager.WARN_EVERY_N_INVALID_ATTEMPTS) — то есть это не
        "у вас проблема", а "у вас ПРОДОЛЖАЮЩАЯСЯ проблема, посмотрите ещё раз".
        """
        return call_in_gui_thread(lambda: self._show_brute_force_warning_impl(origin, attempt_count))

    def _show_brute_force_warning_impl(self, origin: str, attempt_count: int) -> bool:
        result = {"block": False}

        root = tk.Tk()
        root.title(t("dialog.suspicious_activity_title"))
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        lines = [
            t("dialog.brute_force_line1"),
            origin,
            "",
            t("dialog.brute_force_attempts", count=attempt_count),
            "",
            t("dialog.brute_force_line2"),
            t("dialog.brute_force_line3"),
            t("dialog.brute_force_line4"),
            "",
            t("dialog.brute_force_line5"),
            t("dialog.brute_force_line6"),
        ]
        for line in lines:
            kwargs = {"foreground": "#a00"} if line == origin else {}
            ttk.Label(frame, text=line, font=("Sans", 10), **kwargs).pack(anchor="w")

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(15, 0), fill="x")

        def on_block():
            result["block"] = True
            root.destroy()

        def on_ignore():
            result["block"] = False
            root.destroy()

        ttk.Button(btn_frame, text=t("dialog.ignore"), command=on_ignore).pack(side="right", padx=5)
        ttk.Button(btn_frame, text=t("dialog.add_to_blocklist"), command=on_block).pack(side="right")

        root.protocol("WM_DELETE_WINDOW", on_ignore)

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        root.mainloop()
        return result["block"]

    def _show_pairing_request_impl(self, origin: str, code: str) -> bool:
        return self._modal(
            title=t("dialog.pairing_request_title"),
            lines=[
                t("dialog.pairing_requests_access"),
                origin,
                "",
                t("dialog.pairing_can_heading"),
                t("dialog.pairing_can_1"),
                t("dialog.pairing_can_2"),
                t("dialog.pairing_can_2b"),
                "",
                t("dialog.pairing_cannot_heading"),
                t("dialog.pairing_cannot_1"),
                t("dialog.pairing_cannot_2"),
                "",
                t("dialog.pairing_code", code=code),
                t("dialog.pairing_code_hint"),
            ],
            approve_text=t("dialog.approve"),
            deny_text=t("dialog.deny"),
            require_code_checkbox=True,
        )

    def _show_confirm_action_impl(self, origin: str, action_description: str) -> bool:
        return self._modal(
            title=t("dialog.confirm_action_title"),
            lines=[origin, "", action_description],
            approve_text=t("dialog.approve"),
            deny_text=t("dialog.deny"),
        )

    def _modal(self, title: str, lines: list[str], approve_text: str, deny_text: str,
               require_code_checkbox: bool = False) -> bool:
        result = {"approved": False}

        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)

        for line in lines:
            ttk.Label(frame, text=line, font=("Sans", 10)).pack(anchor="w")

        if require_code_checkbox:
            checkbox_var = tk.BooleanVar(value=False)

            def on_checkbox_toggle():
                approve_btn.config(state="normal" if checkbox_var.get() else "disabled")

            ttk.Checkbutton(
                frame, text=t("dialog.code_entered_checkbox"),
                variable=checkbox_var, command=on_checkbox_toggle,
            ).pack(anchor="w", pady=(10, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(15, 0), fill="x")

        def on_approve():
            result["approved"] = True
            root.destroy()

        def on_deny():
            result["approved"] = False
            root.destroy()

        ttk.Button(btn_frame, text=deny_text, command=on_deny).pack(side="right", padx=5)
        initial_state = "disabled" if require_code_checkbox else "normal"
        approve_btn = ttk.Button(btn_frame, text=approve_text, command=on_approve, state=initial_state)
        approve_btn.pack(side="right")

        root.protocol("WM_DELETE_WINDOW", on_deny)

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        root.mainloop()
        return result["approved"]
