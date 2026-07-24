"""
web_client.py — клиент к нативному веб-интерфейсу i2psnark (сессия + nonce),
используется исключительно для приоритезации файлов внутри торрента, так как
torrent-set недоступен в RPC (см. план, раздел "Итоговое решение по
приоритезации файлов").

Экспериментально подтверждённый протокол:
  - URL формы: http://host:port/i2psnark/{torrent_name}/  (НЕ ?p=hash, НЕ ?showEdit)
  - torrent_name — имя из метаинформации (.torrent "name"), НЕ info-hash
  - форма приоритета доступна ТОЛЬКО когда торрент остановлен
  - кнопка отправки: name="savepri" value="Save priorities"
  - ОБЯЗАТЕЛЕН полный набор pri.N для ВСЕХ файлов торрента в каждом запросе —
    частичные наборы приводят к непредсказуемому сбросу состояния остальных файлов
  - enableInOrder сохраняется между циклами stop/start — включать один раз

Требует: pip install requests beautifulsoup4
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

PRIORITY_VALUES = {"high": "5", "normal": "0", "skip": "-9"}


class WebClientError(Exception):
    pass


class TorrentMustBeStoppedError(WebClientError):
    pass


class I2PSnarkWebClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8002/i2psnark/"):
        self.base_url = base_url.rstrip("/") + "/"
        self.session = requests.Session()

    def _torrent_dir_url(self, torrent_name: str) -> str:
        return f"{self.base_url}{torrent_name}/"

    def _fetch_priority_form(self, torrent_name: str) -> tuple[str, str]:
        """
        Возвращает (nonce, form_action) из формы приоритета.
        Бросает TorrentMustBeStoppedError, если форма ещё недоступна — как
        правило, потому что торрент ещё не остановлен, но НЕ проверяем это по
        переведённому тексту ошибки (i2psnark поддерживает ~60 языков
        интерфейса, и на любом языке кроме английского такое сравнение
        молча никогда не срабатывало бы — это баг, который был здесь
        раньше). Единственный надёжный, не зависящий от языка сигнал —
        наличие/отсутствие самой формы с полями pri.N. Вызывающий код уже
        рассчитан на ограниченное число повторов при этой ошибке
        (см. SnarkIntegration._stop_apply_start), так что не пытаемся
        различать "ещё не остановлен" от "разметка неожиданно другая" —
        обе причины retryable одинаково.
        """
        url = self._torrent_dir_url(torrent_name)
        resp = self.session.get(url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        target_form = None
        for form in soup.find_all("form"):
            if form.find("input", attrs={"name": re.compile(r"^pri\.\d+$")}):
                target_form = form
                break

        if target_form is None:
            raise TorrentMustBeStoppedError(
                f"Торрент '{torrent_name}': форма приоритета (поля pri.N) "
                f"пока недоступна на {url} — обычно значит, что торрент ещё "
                f"не полностью остановлен (вызовите rpc_client.torrent_stop() "
                f"и дождитесь статуса перед повтором); реже — что разметка "
                f"этой версии i2psnark отличается от ожидаемой."
            )

        nonce_input = target_form.find("input", attrs={"name": "nonce"})
        if nonce_input is None or not nonce_input.get("value"):
            raise WebClientError("Форма найдена, но nonce в ней отсутствует.")

        form_action = target_form.get("action")
        resolved_action = urljoin(url, form_action) if form_action else url

        return nonce_input["value"], resolved_action

    def get_file_count(self, torrent_name: str) -> int:
        """Определяет число файлов в торренте по числу полей pri.N на странице."""
        url = self._torrent_dir_url(torrent_name)
        resp = self.session.get(url)
        resp.raise_for_status()
        indices = set(re.findall(r'name=["\']pri\.(\d+)["\']', resp.text))
        return len(indices)

    def set_file_priorities(self, torrent_name: str, priorities: dict[int, str]) -> None:
        """
        priorities: {file_index: "high"|"normal"|"skip"} — ОБЯЗАН содержать запись
        для КАЖДОГО файла торрента (см. предупреждение в докстринге модуля). Вызывающий
        код (интеграционный слой, см. integration.py) отвечает за дополнение неполных
        наборов значением "normal" перед вызовом этого метода.

        Торрент должен быть уже остановлен на момент вызова — эта функция сама не
        управляет жизненным циклом торрента, только отправляет форму.
        """
        nonce, form_action = self._fetch_priority_form(torrent_name)

        form_data = {"nonce": nonce, "savepri": "Save priorities"}
        for idx, level in priorities.items():
            if level not in PRIORITY_VALUES:
                raise ValueError(f"Неизвестный уровень приоритета: {level!r}")
            form_data[f"pri.{idx}"] = PRIORITY_VALUES[level]

        resp = self.session.post(form_action, data=form_data)
        resp.raise_for_status()
        # Раньше здесь была ещё одна проверка на переведённый текст ошибки
        # ("гонка состояний между GET и POST") — та же проблема с локалью,
        # что и в _fetch_priority_form, плюс не дающая практической пользы:
        # если гонка всё-таки произошла, следующий вызов set_file_priorities
        # из retry-цикла на уровень выше (см. integration.py) всё равно
        # заново пройдёт через _fetch_priority_form и корректно поймает это
        # через language-independent проверку по форме pri.N.

    def set_in_order(self, torrent_name: str, enabled: bool) -> None:
        """
        Включает/выключает enableInOrder (последовательная докачка по индексу файла).
        Подтверждено: сохраняется между stop/start циклами — обычно достаточно
        вызвать один раз при добавлении видео.
        """
        nonce, form_action = self._fetch_priority_form(torrent_name)

        form_data = {"nonce": nonce, "setInOrderEnabled": "Save Preference"}
        if enabled:
            form_data["enableInOrder"] = "on"

        resp = self.session.post(form_action, data=form_data)
        resp.raise_for_status()
