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
from i18n import t


class ManagePairingsWindow:
    def __init__(self, parent: tk.Misc | None = None):
        self.storage = PolicyStorage()
        self.snark = SnarkIntegration()

        # Toplevel, если открыто из другого окна (settings_window), иначе — свой Tk()
        self.root = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.root.title(t("pairings.window_title"))
        self.root.geometry("650x420")

        columns = ("origin", "status", "created")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings")
        self.tree.heading("origin", text=t("pairings.col_origin"))
        self.tree.heading("status", text=t("pairings.col_status"))
        self.tree.heading("created", text=t("pairings.col_created"))
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Button(btn_frame, text=t("pairings.revoke"), command=self.revoke_selected).pack(side="left")
        ttk.Button(btn_frame, text=t("pairings.block"), command=self.block_selected).pack(side="left", padx=5)
        ttk.Button(
            btn_frame, text=t("pairings.delete_all_torrents"),
            command=self.delete_all_torrents_selected,
        ).pack(side="left", padx=5)
        ttk.Button(btn_frame, text=t("pairings.refresh"), command=self.refresh).pack(side="right")

        self.refresh()

    def refresh(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for entry in self.storage.list_paired_origins():
            status = t("pairings.status_revoked") if entry["revoked"] else t("pairings.status_active")
            created = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["created_at"]))
            self.tree.insert("", "end", values=(entry["origin"], status, created))

    def _get_selected_origin(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(t("pairings.nothing_selected_title"), t("pairings.nothing_selected_body"))
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
            t("pairings.confirm_title"),
            t("pairings.confirm_block_body", origin=origin),
        ):
            self.storage.add_to_blocklist(origin, reason="manual")
            self.refresh()

    def delete_all_torrents_selected(self):
        origin = self._get_selected_origin()
        if origin is None:
            return

        torrents = self.storage.get_torrents_by_owner(origin)
        if not torrents:
            messagebox.showinfo(t("pairings.no_torrents_title"), t("pairings.no_torrents_body", origin=origin))
            return

        confirmed = messagebox.askyesno(
            t("pairings.confirm_title"),
            t("pairings.confirm_delete_body", count=len(torrents), origin=origin),
        )
        if not confirmed:
            return

        errors = []
        for tr in torrents:
            try:
                self.snark.remove_video(tr["torrent_id"], delete_local_data=True)
            except Exception as e:
                errors.append(f"id={tr['torrent_id']}: {e}")
            finally:
                self.storage.unregister_torrent(tr["torrent_id"])

        if errors:
            messagebox.showwarning(
                t("pairings.partial_error_title"),
                t("pairings.partial_error_body", errors="\n".join(errors)),
            )
        else:
            messagebox.showinfo(t("pairings.done_title"), t("pairings.done_body", count=len(torrents)))

        self.refresh()

    def run(self):
        """Только для standalone-запуска (свой mainloop)."""
        self.root.mainloop()


if __name__ == "__main__":
    ManagePairingsWindow().run()
