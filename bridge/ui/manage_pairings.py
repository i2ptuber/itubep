"""
manage_pairings.py — окно управления разрешёнными сайтами (список, отзыв,
блеклист, удаление всех торрентов сайта).

Может запускаться как отдельная команда (python3 -m ui.manage_pairings) или
вызываться из settings_window.py как Toplevel поверх существующего root.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, ".")
from policy.storage import PolicyStorage
from snark import SnarkIntegration


class ManagePairingsWindow:
    def __init__(self, parent: tk.Misc | None = None):
        self.storage = PolicyStorage()
        self.snark = SnarkIntegration()

        # Toplevel, если открыто из другого окна (settings_window), иначе — свой Tk()
        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title("ITubeP Bridge — разрешённые сайты")
        self.root.geometry("650x420")

        columns = ("origin", "status", "created")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings")
        self.tree.heading("origin", text="Origin")
        self.tree.heading("status", text="Статус")
        self.tree.heading("created", text="Добавлен")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(btn_frame, text="Отозвать", command=self.revoke_selected).pack(side="left")
        ttk.Button(btn_frame, text="Добавить в блеклист", command=self.block_selected).pack(side="left", padx=5)
        ttk.Button(
            btn_frame, text="Удалить все торренты сайта",
            command=self.delete_all_torrents_selected,
        ).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Обновить", command=self.refresh).pack(side="right")

        self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for entry in self.storage.list_paired_origins():
            status = "Отозван" if entry["revoked"] else "Активен"
            created = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["created_at"]))
            self.tree.insert("", "end", values=(entry["origin"], status, created))

    def _get_selected_origin(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Ничего не выбрано", "Выберите сайт в списке")
            return None
        return self.tree.item(sel[0])["values"][0]

    def revoke_selected(self):
        origin = self._get_selected_origin()
        if origin:
            self.storage.revoke_origin(origin)
            self.refresh()

    def block_selected(self):
        origin = self._get_selected_origin()
        if origin and messagebox.askyesno(
            "Подтверждение",
            f"Добавить {origin} в блеклист?\n"
            f"Будущие запросы на сопряжение будут отклоняться без диалога.",
        ):
            self.storage.add_to_blocklist(origin, reason="manual")
            self.refresh()

    def delete_all_torrents_selected(self):
        origin = self._get_selected_origin()
        if origin is None:
            return

        torrents = self.storage.get_torrents_by_owner(origin)
        if not torrents:
            messagebox.showinfo("Нет торрентов", f"У сайта {origin} нет зарегистрированных торрентов")
            return

        confirmed = messagebox.askyesno(
            "Подтверждение",
            f"Удалить {len(torrents)} торрент(ов) сайта {origin}?\n"
            f"Это удалит и скачанные данные с диска.",
        )
        if not confirmed:
            return

        errors = []
        for t in torrents:
            try:
                self.snark.remove_video(t["torrent_id"], delete_local_data=True)
            except Exception as e:
                errors.append(f"id={t['torrent_id']}: {e}")
            finally:
                self.storage.unregister_torrent(t["torrent_id"])

        if errors:
            messagebox.showwarning(
                "Частичная ошибка",
                f"Удалено с ошибками:\n" + "\n".join(errors),
            )
        else:
            messagebox.showinfo("Готово", f"Удалено {len(torrents)} торрент(ов)")

        self.refresh()

    def run(self):
        """Только для standalone-запуска (свой mainloop)."""
        self.root.mainloop()


if __name__ == "__main__":
    ManagePairingsWindow().run()
