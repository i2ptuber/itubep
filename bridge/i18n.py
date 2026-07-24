"""
i18n.py — простая система локализации моста.

Логика выбора языка по умолчанию: если системная локаль русская — по
умолчанию "ru", иначе (английская или любая другая) — по умолчанию "en".
Выбор явно сохранённый пользователем в настройках (таблица `settings` в
той же SQLite БД, что и остальные настройки моста) всегда имеет приоритет
над автоопределением. Меняется на странице настроек моста
(ui/settings_window.py) — язык сайта при этом никак не затрагивается,
это полностью независимая система (см. site/app/i18n.py).

Использование:
    from i18n import t
    ttk.Label(frame, text=t("settings.title"))
"""

from __future__ import annotations

import locale

SUPPORTED_LANGUAGES = ("ru", "en")
DEFAULT_FALLBACK = "en"

_current_lang: str | None = None  # кэш на процесс, см. get_language()


def detect_system_language() -> str:
    """Системная локаль ru* -> 'ru', иначе (en/любая другая/неизвестная) -> 'en'."""
    try:
        lang_code, _ = locale.getdefaultlocale()
    except Exception:
        lang_code = None
    if lang_code and lang_code.lower().startswith("ru"):
        return "ru"
    return "en"


def get_language(storage=None) -> str:
    """
    Возвращает текущий язык интерфейса моста. Порядок приоритета:
    1) уже закэшированный в этом процессе выбор (см. set_language),
    2) явно сохранённый пользователем в настройках моста,
    3) автоопределение по системной локали.
    """
    global _current_lang
    if _current_lang is not None:
        return _current_lang

    saved = None
    if storage is None:
        try:
            from policy.storage import PolicyStorage
            storage = PolicyStorage()
        except Exception:
            storage = None
    if storage is not None:
        saved = storage.get_setting("language")

    if saved in SUPPORTED_LANGUAGES:
        _current_lang = saved
    else:
        _current_lang = detect_system_language()
    return _current_lang


def set_language(lang: str, storage=None) -> None:
    """Сохраняет выбор языка в настройках моста и обновляет кэш процесса."""
    global _current_lang
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {lang}")
    if storage is None:
        from policy.storage import PolicyStorage
        storage = PolicyStorage()
    storage.set_setting("language", lang)
    _current_lang = lang


def t(key: str, **kwargs) -> str:
    """Возвращает переведённую строку по ключу для текущего языка моста."""
    lang = get_language()
    table = _TRANSLATIONS.get(lang, {})
    text = table.get(key)
    if text is None:
        text = _TRANSLATIONS[DEFAULT_FALLBACK].get(key, key)
    return text.format(**kwargs) if kwargs else text


_TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru": {
        # --- settings_window.py ---
        "settings.window_title": "ITubeP Bridge — настройки",
        "settings.language_heading": "Язык интерфейса",
        "settings.language_ru": "Русский",
        "settings.language_en": "English",
        "settings.mode_heading": "Режим работы",
        "settings.mode_silent": "Тихий (без подтверждения каждого действия)",
        "settings.mode_confirm": "С подтверждением (каждое действие требует разрешения)",
        "settings.pairing_note_1": "Сопряжение сайтов всегда требует ручного",
        "settings.pairing_note_2": "подтверждения независимо от режима выше.",
        "settings.apply_note_1": "Изменения применяются сразу — сервер моста",
        "settings.apply_note_2": "читает настройки при каждом действии.",
        "settings.proxy_heading": "HTTP-прокси I2P (для публикации на .i2p-сайты)",
        "settings.proxy_description": (
            "Через этот прокси мост ходит к сайту при публикации видео\n"
            "(регистрация канала, отправка манифеста и торрента) — requests\n"
            "не умеет резолвить .i2p-адреса напрямую, это не DNS. Для сайтов\n"
            "на localhost/127.0.0.1 (локальное тестирование) прокси не\n"
            "используется, независимо от этого поля. Дефолт 127.0.0.1:4444\n"
            "подходит и для i2pd, и для Java I2P со стандартными настройками."
        ),
        "settings.save_proxy": "Сохранить прокси",
        "settings.trackers_heading": "Трекеры (announce), по одному URL на строку",
        "settings.trackers_description": (
            "Без них поиск пиров идёт только через DHT/PEX i2psnark и может\n"
            "занимать долгое время. Возьмите живой список со страницы\n"
            "http://127.0.0.1:8002/i2psnark/configure (\"Trackers\") того же\n"
            "i2psnark, которым пользуется этот мост."
        ),
        "settings.save_trackers": "Сохранить трекеры",
        "settings.restart_required": "Требуется перезапуск сервера моста, чтобы изменения применились",
        "settings.manage_pairings_btn": "Разрешённые сайты...",
        "settings.close": "Закрыть",

        # --- manage_pairings.py ---
        "pairings.window_title": "ITubeP Bridge — разрешённые сайты",
        "pairings.col_origin": "Origin",
        "pairings.col_status": "Статус",
        "pairings.col_created": "Добавлен",
        "pairings.revoke": "Отозвать",
        "pairings.block": "Добавить в блеклист",
        "pairings.delete_all_torrents": "Удалить все торренты сайта",
        "pairings.refresh": "Обновить",
        "pairings.status_revoked": "Отозван",
        "pairings.status_active": "Активен",
        "pairings.nothing_selected_title": "Ничего не выбрано",
        "pairings.nothing_selected_body": "Выберите сайт в списке",
        "pairings.confirm_title": "Подтверждение",
        "pairings.confirm_block_body": "Добавить {origin} в блеклист?\nБудущие запросы на сопряжение будут отклоняться без диалога.",
        "pairings.no_torrents_title": "Нет торрентов",
        "pairings.no_torrents_body": "У сайта {origin} нет зарегистрированных торрентов",
        "pairings.confirm_delete_body": "Удалить {count} торрент(ов) сайта {origin}?\nЭто удалит и скачанные данные с диска.",
        "pairings.partial_error_title": "Частичная ошибка",
        "pairings.partial_error_body": "Удалено с ошибками:\n{errors}",
        "pairings.done_title": "Готово",
        "pairings.done_body": "Удалено {count} торрент(ов)",

        # --- tkinter_dialog.py ---
        "dialog.suspicious_activity_title": "Подозрительная активность",
        "dialog.brute_force_line1": "Сайт прислал много неверных кодов подтверждения подряд:",
        "dialog.brute_force_attempts": "Неудачных попыток: {count}",
        "dialog.brute_force_line2": "Это похоже на попытку подобрать код сопряжения программно,",
        "dialog.brute_force_line3": "а не на человека, который просто ошибся при вводе. Обычно",
        "dialog.brute_force_line4": "легитимному сайту не нужно присылать код больше пары раз.",
        "dialog.brute_force_line5": "Если вы не пытались сопрячься с этим сайтом сейчас —",
        "dialog.brute_force_line6": "рекомендуем заблокировать его.",
        "dialog.ignore": "Игнорировать",
        "dialog.add_to_blocklist": "Добавить в блеклист",
        "dialog.pairing_request_title": "Запрос доступа к торрент-клиенту",
        "dialog.pairing_requests_access": "Сайт запрашивает доступ:",
        "dialog.pairing_can_heading": "Сможет:",
        "dialog.pairing_can_1": "  • Добавлять новые торренты для скачивания",
        "dialog.pairing_can_2": "  • Просматривать/удалять ТОЛЬКО те торренты,",
        "dialog.pairing_can_2b": "    которые сам же добавил",
        "dialog.pairing_cannot_heading": "НЕ сможет:",
        "dialog.pairing_cannot_1": "  • Видеть или трогать торренты других сайтов",
        "dialog.pairing_cannot_2": "  • Видеть список остальных ваших загрузок",
        "dialog.pairing_code": "Код подтверждения: {code}",
        "dialog.pairing_code_hint": "(введите этот код на странице сайта)",
        "dialog.approve": "Разрешить",
        "dialog.deny": "Отклонить",
        "dialog.confirm_action_title": "Подтверждение действия",
        "dialog.code_entered_checkbox": "Код введён на странице сайта",

        # --- publish_dialogs.py ---
        "publish.create_channel_title": "Создание канала",
        "publish.no_channel_line1": "У вас ещё нет канала для публикации.",
        "publish.no_channel_line2": "Сейчас будет создан новый криптографический ключ канала.",
        "publish.no_channel_warn1": "ВАЖНО: если вы потеряете этот ключ, канал будет",
        "publish.no_channel_warn2": "утрачен НАВСЕГДА — восстановить его никаким",
        "publish.no_channel_warn3": "способом нельзя.",
        "publish.understand_checkbox": "Я понимаю и хочу продолжить",
        "publish.cancel": "Отмена",
        "publish.create_channel_btn": "Создать канал",
        "publish.channel_name_title": "Название канала",
        "publish.channel_name_prompt": "Введите название вашего канала:",
        "publish.password_title": "Защита ключа канала паролем",
        "publish.password_migration_1": "Ключ вашего канала сейчас хранится на диске без шифрования.",
        "publish.password_migration_2": "Задайте пароль, чтобы защитить его — с этого момента ключ",
        "publish.password_migration_3": "будет храниться зашифрованным, и мост будет спрашивать этот",
        "publish.password_migration_4": "пароль при каждом запуске (один раз за сессию).",
        "publish.password_new_1": "Этот пароль будет защищать ваш ключ канала на диске.",
        "publish.password_new_2": "Мост будет спрашивать его один раз при первой публикации",
        "publish.password_new_3": "после каждого запуска моста.",
        "publish.password_warning": (
            "ВАЖНО: этот пароль нигде не сохраняется. Если вы его\n"
            "забудете — расшифровать ключ канала будет невозможно,\n"
            "канал будет потерян так же необратимо, как при потере\n"
            "самого ключа."
        ),
        "publish.password_label": "Пароль:",
        "publish.password_repeat_label": "Повторите пароль:",
        "publish.password_empty_error": "Пароль не может быть пустым",
        "publish.password_mismatch_error": "Пароли не совпадают",
        "publish.password_short_error": "Пароль слишком короткий (минимум 8 символов)",
        "publish.confirm_btn": "Подтвердить",
        "publish.unlock_title": "Разблокировка ключа канала",
        "publish.unlock_wrong_password": "Неверный пароль (попытка {attempt}/3). Попробуйте снова:",
        "publish.unlock_prompt": "Введите пароль для разблокировки ключа канала:",
        "publish.unlock_btn": "Разблокировать",
        "publish.request_title": "Запрос на публикацию",
        "publish.request_body": "Сайт {origin} запрашивает публикацию нового видео.",
        "publish.continue_question": "Продолжить?",
        "publish.continue_btn": "Продолжить",
        "publish.choose_file_title": "Выберите видеофайл",
        "publish.filetype_video": "Видео",
        "publish.filetype_all": "Все файлы",
        "publish.video_data_title": "Данные видео",
        "publish.title_label": "Название:",
        "publish.description_label": "Описание:",
        "publish.publish_btn": "Опубликовать",
        "publish.ok_btn": "OK",

        # --- assemble_video.py (CLI) ---
        "assemble.dir_not_found": "Директория не найдена: {dir}",
        "assemble.no_segments": "В {dir} не найдено файлов вида segment_NNNN.ts — это точно директория докачанного видео-торрента?",
        "assemble.gap_msg": "Пропуск в последовательности сегментов: ожидался индекс {expected}, найден {found}. Торрент докачан не полностью — будет собрано только {usable} сегмент(ов) до пропуска.",
        "assemble.gap_hint": " Используйте --allow-partial, если это ожидаемо.",
        "assemble.no_usable_segments": "Нет ни одного пригодного для сборки сегмента (пропуск на самом первом).",
        "assemble.assembling": "Собираю {usable} из {total} докачанных сегментов...",
        "assemble.ffmpeg_error": "ffmpeg завершился с ошибкой:\n{stderr}",
        "assemble.no_ffmpeg": "ffmpeg не найден в PATH — делаю сырую конкатенацию .ts (менее надёжно)",
        "assemble.error_prefix": "Ошибка: {error}",
        "assemble.done": "Готово: {output}",
        "assemble.arg_description": "Собрать докачанные HLS-сегменты в один видеофайл (для просмотра без плеера сайта/JS).",
        "assemble.arg_torrent_name": "Имя торрента (папка с сегментами в storage-директории i2psnark)",
        "assemble.arg_storage_dir": "Storage-директория i2psnark (по умолчанию берётся из настроек моста)",
        "assemble.arg_output": "Путь к результату (по умолчанию <storage-dir>/<torrent_name>.mp4)",
        "assemble.arg_allow_partial": "Не падать с ошибкой, если торрент докачан не полностью — собрать что есть",
        "assemble.arg_keep_ts": "(зарезервировано) ничего не удаляет — исходные сегменты не трогаются в любом случае",

        # --- http_server.py ---
        "server.resuming_torrents": "Возобновляю раздачу ранее добавленных торрентов...",
        "server.resuming_done": "Готово.",
    },
    "en": {
        # --- settings_window.py ---
        "settings.window_title": "ITubeP Bridge — Settings",
        "settings.language_heading": "Interface language",
        "settings.language_ru": "Русский",
        "settings.language_en": "English",
        "settings.mode_heading": "Operating mode",
        "settings.mode_silent": "Silent (no confirmation for each action)",
        "settings.mode_confirm": "Confirm (every action requires approval)",
        "settings.pairing_note_1": "Pairing with sites always requires manual",
        "settings.pairing_note_2": "confirmation regardless of the mode above.",
        "settings.apply_note_1": "Changes apply immediately — the bridge server",
        "settings.apply_note_2": "reads settings on every action.",
        "settings.proxy_heading": "I2P HTTP proxy (for publishing to .i2p sites)",
        "settings.proxy_description": (
            "The bridge uses this proxy to reach the site when publishing a video\n"
            "(channel registration, sending the manifest and torrent) — requests\n"
            "can't resolve .i2p addresses directly, it's not DNS. For sites on\n"
            "localhost/127.0.0.1 (local testing) the proxy is not used, regardless\n"
            "of this field. The default 127.0.0.1:4444 works for both i2pd and\n"
            "Java I2P with standard settings."
        ),
        "settings.save_proxy": "Save proxy",
        "settings.trackers_heading": "Trackers (announce), one URL per line",
        "settings.trackers_description": (
            "Without them, peer discovery only goes through i2psnark's DHT/PEX\n"
            "and can take a long time. Copy a live list from\n"
            "http://127.0.0.1:8002/i2psnark/configure (\"Trackers\") of the same\n"
            "i2psnark instance this bridge uses."
        ),
        "settings.save_trackers": "Save trackers",
        "settings.restart_required": "Restarting the bridge server is required for changes to take effect",
        "settings.manage_pairings_btn": "Allowed sites...",
        "settings.close": "Close",

        # --- manage_pairings.py ---
        "pairings.window_title": "ITubeP Bridge — Allowed Sites",
        "pairings.col_origin": "Origin",
        "pairings.col_status": "Status",
        "pairings.col_created": "Added",
        "pairings.revoke": "Revoke",
        "pairings.block": "Add to blocklist",
        "pairings.delete_all_torrents": "Delete all site torrents",
        "pairings.refresh": "Refresh",
        "pairings.status_revoked": "Revoked",
        "pairings.status_active": "Active",
        "pairings.nothing_selected_title": "Nothing selected",
        "pairings.nothing_selected_body": "Select a site in the list",
        "pairings.confirm_title": "Confirmation",
        "pairings.confirm_block_body": "Add {origin} to the blocklist?\nFuture pairing requests will be rejected without a dialog.",
        "pairings.no_torrents_title": "No torrents",
        "pairings.no_torrents_body": "Site {origin} has no registered torrents",
        "pairings.confirm_delete_body": "Delete {count} torrent(s) belonging to {origin}?\nThis will also delete downloaded data from disk.",
        "pairings.partial_error_title": "Partial error",
        "pairings.partial_error_body": "Deleted with errors:\n{errors}",
        "pairings.done_title": "Done",
        "pairings.done_body": "Deleted {count} torrent(s)",

        # --- tkinter_dialog.py ---
        "dialog.suspicious_activity_title": "Suspicious activity",
        "dialog.brute_force_line1": "The site sent many invalid confirmation codes in a row:",
        "dialog.brute_force_attempts": "Failed attempts: {count}",
        "dialog.brute_force_line2": "This looks like an attempt to brute-force the pairing code",
        "dialog.brute_force_line3": "programmatically, rather than a human who simply mistyped it.",
        "dialog.brute_force_line4": "A legitimate site usually shouldn't need to send the code more than a couple of times.",
        "dialog.brute_force_line5": "If you weren't trying to pair with this site right now —",
        "dialog.brute_force_line6": "we recommend blocking it.",
        "dialog.ignore": "Ignore",
        "dialog.add_to_blocklist": "Add to blocklist",
        "dialog.pairing_request_title": "Torrent client access request",
        "dialog.pairing_requests_access": "The site is requesting access:",
        "dialog.pairing_can_heading": "It will be able to:",
        "dialog.pairing_can_1": "  • Add new torrents to download",
        "dialog.pairing_can_2": "  • View/remove ONLY the torrents",
        "dialog.pairing_can_2b": "    it added itself",
        "dialog.pairing_cannot_heading": "It will NOT be able to:",
        "dialog.pairing_cannot_1": "  • See or touch torrents from other sites",
        "dialog.pairing_cannot_2": "  • See the list of your other downloads",
        "dialog.pairing_code": "Confirmation code: {code}",
        "dialog.pairing_code_hint": "(enter this code on the site's page)",
        "dialog.approve": "Allow",
        "dialog.deny": "Deny",
        "dialog.confirm_action_title": "Confirm action",
        "dialog.code_entered_checkbox": "Code entered on the site's page",

        # --- publish_dialogs.py ---
        "publish.create_channel_title": "Create channel",
        "publish.no_channel_line1": "You don't have a channel to publish to yet.",
        "publish.no_channel_line2": "A new cryptographic channel key is about to be created.",
        "publish.no_channel_warn1": "IMPORTANT: if you lose this key, the channel will be",
        "publish.no_channel_warn2": "lost FOREVER — it cannot be recovered by any",
        "publish.no_channel_warn3": "means.",
        "publish.understand_checkbox": "I understand and want to continue",
        "publish.cancel": "Cancel",
        "publish.create_channel_btn": "Create channel",
        "publish.channel_name_title": "Channel name",
        "publish.channel_name_prompt": "Enter your channel name:",
        "publish.password_title": "Protect channel key with a password",
        "publish.password_migration_1": "Your channel key is currently stored on disk unencrypted.",
        "publish.password_migration_2": "Set a password to protect it — from now on the key will",
        "publish.password_migration_3": "be stored encrypted, and the bridge will ask for this",
        "publish.password_migration_4": "password once per session on every launch.",
        "publish.password_new_1": "This password will protect your channel key on disk.",
        "publish.password_new_2": "The bridge will ask for it once, on the first publish",
        "publish.password_new_3": "after each bridge launch.",
        "publish.password_warning": (
            "IMPORTANT: this password is never saved anywhere. If you\n"
            "forget it, decrypting the channel key will be impossible —\n"
            "the channel will be lost just as irreversibly as if you\n"
            "lost the key itself."
        ),
        "publish.password_label": "Password:",
        "publish.password_repeat_label": "Repeat password:",
        "publish.password_empty_error": "Password cannot be empty",
        "publish.password_mismatch_error": "Passwords don't match",
        "publish.password_short_error": "Password is too short (minimum 8 characters)",
        "publish.confirm_btn": "Confirm",
        "publish.unlock_title": "Unlock channel key",
        "publish.unlock_wrong_password": "Wrong password (attempt {attempt}/3). Try again:",
        "publish.unlock_prompt": "Enter the password to unlock the channel key:",
        "publish.unlock_btn": "Unlock",
        "publish.request_title": "Publish request",
        "publish.request_body": "Site {origin} is requesting to publish a new video.",
        "publish.continue_question": "Continue?",
        "publish.continue_btn": "Continue",
        "publish.choose_file_title": "Choose a video file",
        "publish.filetype_video": "Video",
        "publish.filetype_all": "All files",
        "publish.video_data_title": "Video details",
        "publish.title_label": "Title:",
        "publish.description_label": "Description:",
        "publish.publish_btn": "Publish",
        "publish.ok_btn": "OK",

        # --- assemble_video.py (CLI) ---
        "assemble.dir_not_found": "Directory not found: {dir}",
        "assemble.no_segments": "No segment_NNNN.ts files found in {dir} — is this really a downloaded video torrent directory?",
        "assemble.gap_msg": "Gap in segment sequence: expected index {expected}, found {found}. The torrent isn't fully downloaded — only {usable} segment(s) before the gap will be assembled.",
        "assemble.gap_hint": " Use --allow-partial if this is expected.",
        "assemble.no_usable_segments": "No usable segments to assemble (gap at the very first one).",
        "assemble.assembling": "Assembling {usable} of {total} downloaded segments...",
        "assemble.ffmpeg_error": "ffmpeg exited with an error:\n{stderr}",
        "assemble.no_ffmpeg": "ffmpeg not found in PATH — falling back to raw .ts concatenation (less reliable)",
        "assemble.error_prefix": "Error: {error}",
        "assemble.done": "Done: {output}",
        "assemble.arg_description": "Assemble downloaded HLS segments into a single video file (for viewing without the site's player/JS).",
        "assemble.arg_torrent_name": "Torrent name (folder with segments inside i2psnark's storage directory)",
        "assemble.arg_storage_dir": "i2psnark storage directory (defaults to the value from bridge settings)",
        "assemble.arg_output": "Output path (defaults to <storage-dir>/<torrent_name>.mp4)",
        "assemble.arg_allow_partial": "Don't fail if the torrent isn't fully downloaded — assemble what's available",
        "assemble.arg_keep_ts": "(reserved) doesn't delete anything — source segments are never touched",

        # --- http_server.py ---
        "server.resuming_torrents": "Resuming previously added torrents...",
        "server.resuming_done": "Done.",
    },
}
