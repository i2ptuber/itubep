"""
pairing.py — протокол сопряжения (device pairing) сайт <-> мост.

Порядок действий пользователя на двух сторонах не фиксирован. request_pairing()
не блокируется (диалог отрисовывается в выделенном GUI-потоке, см. gui_thread.py),
confirm_pairing() дожидается результата поллингом, независимо от порядка.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid

from ui.tkinter_dialog import TkinterPairingDialog

from .storage import PolicyStorage

log = logging.getLogger(__name__)


class PairingManager:
    # Сколько попыток ввода кода даём на ОДИН сгенерированный код (2 —
    # чтобы опечатка не убивала легитимную попытку, но не больше, иначе это
    # просто медленный перебор с шагом >1 за cooldown-цикл).
    MAX_ATTEMPTS_PER_CODE = 2

    # Раз в сколько НАКОПИТЕЛЬНЫХ (за всё время, не за один код) неудачных
    # попыток показывать пользователю предупреждение с предложением
    # заблокировать источник. Срабатывает один раз на каждый пройденный
    # порог (5, 10, 15, ...), не на каждую попытку после первого срабатывания.
    WARN_EVERY_N_INVALID_ATTEMPTS = 5

    def __init__(self, storage: PolicyStorage, dialog: TkinterPairingDialog | None = None):
        self.storage = storage
        self.dialog = dialog or TkinterPairingDialog()

    def request_pairing(self, origin: str) -> dict:
        log.debug("request_pairing вызван")

        if self.storage.is_blocked(origin):
            log.debug("origin заблокирован")
            return {"status": "blocked"}

        if not self.storage.can_request_pairing(origin):
            log.debug("origin на cooldown")
            return {"status": "cooldown"}

        # ВАЖНО: сам код (code) — единственное, что подтверждает, что
        # человек реально увидел GUI-диалог и ввёл именно то, что там
        # показано, а не кликнул "Approve" вслепую. Поэтому его НИКОГДА не
        # пишем в лог, даже на уровне DEBUG — лог может быть скопирован
        # (например, для саппорта) в течение TTL кода, и это лишний путь
        # его раскрытия сверх задуманного (см. также send-to-support сценарий).
        code = f"{secrets.randbelow(1_000_000):06d}"
        log.debug("код сгенерирован, запускаю поток диалога")
        self.storage.save_pairing_code(origin, code)

        def show_dialog_and_update_status():
            log.debug("поток диалога стартовал")
            try:
                approved = self.dialog.show_pairing_request(origin, code)
                log.debug("show_pairing_request вернул: %s", approved)
                self.storage.set_pairing_status(origin, "approved" if approved else "rejected")
            except BaseException:
                log.exception("исключение в потоке диалога сопряжения")

        t = threading.Thread(target=show_dialog_and_update_status, daemon=True)
        t.start()
        log.debug("поток запущен, is_alive=%s", t.is_alive())

        return {"status": "shown"}

    def confirm_pairing(self, origin: str, code: str, wait_timeout: float = 120.0) -> str | None:
        state = self.storage.get_pairing_state(origin)
        if state is None:
            return None

        if state["attempts"] >= self.MAX_ATTEMPTS_PER_CODE:
            # Лимит попыток для ЭТОГО кода уже исчерпан — даже если сейчас
            # прислали правильный код, он больше не принимается. Новый цикл
            # возможен только через новый request_pairing() после cooldown
            # (last_request_at этой строки мы не трогаем, так что cooldown
            # отсчитывается честно от момента выдачи текущего кода).
            return None

        if state["code"] != code:
            self.storage.increment_pairing_attempts(origin)
            abuse_count = self.storage.record_invalid_attempt(origin)
            self._maybe_warn_abuse(origin, abuse_count)
            return None

        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            state = self.storage.get_pairing_state(origin)
            if state is None:
                return None

            if state["status"] == "approved":
                token = uuid.uuid4().hex
                self.storage.save_token(token, origin)
                self.storage.clear_pairing_code(origin)
                # Легитимный сайт успешно сопрягся — не держим на нём
                # клеймо "подозрительный" из-за пары неудачных попыток
                # до этого (опечатки в коде — это нормально).
                self.storage.reset_invalid_attempts(origin)
                return token

            if state["status"] == "rejected":
                self.storage.clear_pairing_code(origin)
                return None

            time.sleep(0.5)

        return None

    def _maybe_warn_abuse(self, origin: str, abuse_count: int):
        if abuse_count == 0 or abuse_count % self.WARN_EVERY_N_INVALID_ATTEMPTS != 0:
            return
        if not self.storage.get_and_mark_warn_threshold(origin, abuse_count):
            return  # этот порог уже показывали раньше — не спамим повторно

        def show_warning_and_maybe_block():
            try:
                should_block = self.dialog.show_brute_force_warning(origin, abuse_count)
                if should_block:
                    self.storage.add_to_blocklist(
                        origin,
                        reason=f"автоматическая блокировка: {abuse_count} неудачных попыток подбора кода сопряжения",
                    )
            except BaseException:
                import traceback
                traceback.print_exc()

        threading.Thread(target=show_warning_and_maybe_block, daemon=True).start()
