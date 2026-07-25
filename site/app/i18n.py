"""
i18n.py — локализация сайта. Полностью независима от локализации моста
(bridge/i18n.py) — та настраивается в настройках моста, эта переключается
кнопкой прямо на сайте и хранится в cookie конкретного браузера.

Сайт всегда открывается на английском по умолчанию (независимо от
системной локали сервера/пользователя) — переключить на русский можно
кнопкой в шапке, выбор запоминается в cookie "site_lang".
"""

from __future__ import annotations

from fastapi import Request

SUPPORTED_LANGUAGES = ("en", "ru")
DEFAULT_LANGUAGE = "en"
COOKIE_NAME = "site_lang"


def get_language(request: Request) -> str:
    lang = request.cookies.get(COOKIE_NAME)
    if lang in SUPPORTED_LANGUAGES:
        return lang
    return DEFAULT_LANGUAGE


def get_strings(request: Request) -> dict:
    """Весь словарь текущего языка сайта — для передачи в JS (см.
    static/i18n.js), который переводит строки, генерируемые на клиенте
    (плеер, мастер публикации)."""
    lang = get_language(request)
    return _TRANSLATIONS.get(lang, _TRANSLATIONS[DEFAULT_LANGUAGE])


def get_translator(request: Request):
    """Возвращает функцию t(key) для использования в шаблонах Jinja2."""
    lang = get_language(request)
    table = _TRANSLATIONS.get(lang, {})

    def t(key: str) -> str:
        return table.get(key, _TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key))

    return t


_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "nav.publish": "Publish",
        "nav.search_placeholder": "Search...",
        "nav.search_btn": "Search",
        "nav.account_title": "Account",
        "nav.my_channel": "My channel",
        "nav.studio": "Studio",
        "nav.channel_manager": "Channel manager",
        "nav.bridge_settings": "Bridge settings",
        "nav.rules": "Rules",
        "nav.about": "Contacts and source code",
        "lang.switch_to": "Русский",

        "search.home_title": "Home",
        "search.nothing_found": "Nothing found for \u201c{query}\u201d",
        "search.enter_query": "Enter a search query to find videos",
        "search.sec": "sec",
        "search.recent_heading": "Recent",
        "search.random_heading": "Random",
        "search.no_videos_yet": "No videos have been published on this site yet.",

        "channel.downloads": "downloads",
        "channel.pinned": "Pinned",

        "nochannel.title": "You don't have a channel yet",
        "nochannel.text": "Your channel is created automatically the first time you publish a "
                           "video, or the first time you save changes in the Studio.",

        "studio.title": "Studio",
        "studio.loading": "Connecting to the bridge...",
        "studio.display_name_label": "Channel name on this site",
        "studio.display_name_hint": "Overrides the display name only on this site; doesn't change "
                                      "the channel record itself.",
        "studio.description_label": "Channel description",
        "studio.pinned_label": "Pinned video",
        "studio.pinned_none": "\u2014 none \u2014",
        "studio.videos_heading": "Your videos",
        "studio.access_label": "Access",
        "studio.access_public": "Public",
        "studio.access_unlisted": "Unlisted (link only)",
        "studio.access_private": "Private (not served by the site)",
        "studio.save_button": "Save changes",
        "studio.status_saving": "Saving...",
        "studio.status_saved": "Saved",
        "studio.status_error": "Error: ",
        "studio.no_videos": "No published videos yet.",
        "studio.error_pairing": "Failed to pair with the bridge",

        "channels.title": "Channel manager",
        "channels.text": "The channel manager (switching between and managing several channels/"
                          "identities) is implemented on the bridge side and is coming in a future "
                          "update.",

        "about.title": "Contacts and source code",
        "about.source_heading": "Source code",
        "about.source_text": "ITubeP is free software. Site and bridge source code, issues, and "
                              "pull requests:",
        "about.contact_heading": "Contact",
        "about.contact_text": "Bug reports, ideas, and pull requests are welcome via the project "
                               "repository above.",

        "rules.title": "Content rules",
        "rules.intro": "Guidelines for publishing on this site — video and channel titles, "
                        "descriptions, and general content rules.",
        "rules.item_legal": "Content must not violate the laws applicable to the site operator.",
        "rules.item_description": "Descriptions should be relevant to the video — no unrelated "
                                    "spam, repeated mass keyword stuffing or links to illegal materials.",
        "rules.item_harm": "No content that promotes violence, or clearly illegal "
                            "activity.",
        "rules.item_moderation": "Violations may result in a video being removed or a channel "
                                  "being banned by the site operator.",

        "publish.title": "Publish a video",
        "publish.description": "Publishing is done through the local ITubeP Bridge — several "
                                 "windows will open: confirmation, file selection, title and description.",
        "publish.button": "Publish video",
        "publish.status_pairing": "Pairing with the bridge...",
        "publish.status_wizard": "Publish wizard started — switch to the ITubeP Bridge windows...",
        "publish.status_published": "Published! ",
        "publish.open_video": "Open video",
        "publish.status_error": "Error: ",
        "publish.enter_code_prompt": "Enter the confirmation code from the ITubeP Bridge window:",
        "publish.error_no_code": "No code entered",
        "publish.error_bad_code": "Invalid code or denied by the bridge",
        "publish.error_unknown": "Unknown publishing error",

        "video.nojs_needs_js": "Viewing directly on the site requires JavaScript and an installed",
        "video.nojs_bridge_link": "ITubeP Bridge",
        "video.nojs_or_download": "Or download the torrent file and watch locally with any "
                                    "BitTorrent client that supports I2P:",
        "video.download_quality": "Download {quality} (.torrent)",

        "comments.heading": "Comments",
        "comments.placeholder": "Add a comment...",
        "comments.submit": "Comment",
        "comments.load_more": "Show more comments",
        "comments.status_sending": "Sending...",
        "comments.status_error": "Error: ",
        "comments.too_long": "Comment is too long (max 2000 characters, not counting whitespace)",

        "player.status_pairing_done": "Pairing complete, adding torrent...",
        "player.status_adding_torrent": "Adding torrent...",
        "player.status_torrent_added": "Torrent added, starting player...",
        "player.status_ready": "Ready to play",
        "player.status_loading_fragments": "Some video fragments are still loading...",
        "player.status_playback_error": "Playback error: ",
        "player.status_reauth": "Bridge pairing was revoked, re-confirmation required...",
        "player.status_seeking": "Seeking to segment {index}, waiting for download...",
        "player.error_no_hls": "Browser doesn't support HLS",
        "player.error_no_torrent_fetch": "Failed to fetch .torrent from the site",
        "player.error_no_stream_token": "Failed to get a playback token",
        "player.error_bridge_rejected": "Bridge rejected adding the torrent: ",
        "player.error_blocked": "This site is blocked in the bridge settings — contact the "
                                 "bridge owner to unblock it.",
        "player.error_cooldown": "Pairing attempts too frequent — please wait a bit and refresh the page.",
        "player.error_token_revoked": "Token revoked by the bridge, pairing needs to be redone",
        "player.prompt_code": "Enter the confirmation code from the ITubeP Bridge window.\n"
                                "(the bridge window may have opened separately — switch to it "
                                "if the code hasn't been generated yet, wait a couple seconds)",
        "player.error_no_code": "No code entered",
        "player.error_bad_code": "Invalid code or denied by the bridge",
    },
    "ru": {
        "nav.publish": "Опубликовать",
        "nav.search_placeholder": "Поиск...",
        "nav.search_btn": "Найти",
        "nav.account_title": "Аккаунт",
        "nav.my_channel": "Мой канал",
        "nav.studio": "Студия",
        "nav.channel_manager": "Менеджер каналов",
        "nav.bridge_settings": "Настройки",
        "nav.rules": "Правила",
        "nav.about": "Контакты и исходный код",
        "lang.switch_to": "English",

        "search.home_title": "Главная",
        "search.nothing_found": "Ничего не найдено по запросу «{query}»",
        "search.enter_query": "Введите запрос для поиска видео",
        "search.sec": "сек",
        "search.recent_heading": "Недавние",
        "search.random_heading": "Случайные",
        "search.no_videos_yet": "На сайте пока не опубликовано ни одного видео.",

        "channel.downloads": "скачиваний",
        "channel.pinned": "Закреплено",

        "nochannel.title": "У вас пока нет канала",
        "nochannel.text": "Канал создаётся автоматически при первой публикации видео, либо при "
                           "первом сохранении изменений в Студии.",

        "studio.title": "Студия",
        "studio.loading": "Соединение с мостом...",
        "studio.display_name_label": "Название канала на этом сайте",
        "studio.display_name_hint": "Переопределяет отображаемое имя только на этом сайте, не "
                                      "меняет саму запись канала.",
        "studio.description_label": "Описание канала",
        "studio.pinned_label": "Закреплённое видео",
        "studio.pinned_none": "— нет —",
        "studio.videos_heading": "Ваши видео",
        "studio.access_label": "Доступ",
        "studio.access_public": "Открытый",
        "studio.access_unlisted": "По ссылке",
        "studio.access_private": "Ограниченный (не отдаётся сайтом)",
        "studio.save_button": "Сохранить изменения",
        "studio.status_saving": "Сохранение...",
        "studio.status_saved": "Сохранено",
        "studio.status_error": "Ошибка: ",
        "studio.no_videos": "Опубликованных видео пока нет.",
        "studio.error_pairing": "Не удалось сопрячься с мостом",

        "channels.title": "Менеджер каналов",
        "channels.text": "Менеджер каналов (переключение между несколькими каналами/личностями "
                          "и управление ими) реализуется на стороне моста и появится в одном из "
                          "следующих обновлений.",

        "about.title": "Контакты и исходный код",
        "about.source_heading": "Исходный код",
        "about.source_text": "ITubeP — проект с открытым исходным кодом. Код сайта и моста, "
                              "баг-репорты и пул-реквесты:",
        "about.contact_heading": "Контакты",
        "about.contact_text": "Багрепорты, идеи и пул-реквесты приветствуются через репозиторий "
                               "проекта выше.",

        "rules.title": "Правила публикации",
        "rules.intro": "Правила публикации на этом сайте — названия видео и каналов, описания "
                        "под видео и общие правила по контенту.",
        "rules.item_legal": "Контент не должен нарушать законодательство, применимое к оператору сайта.",
        "rules.item_description": "Описания должны относиться к видео — без постороннего спама, "
                                    "массового повторения ключевых слов и ссылок на незаконные материалы.",
        "rules.item_harm": "Запрещён контент, пропагандирующий насилие, или явно "
                            "незаконную деятельность.",
        "rules.item_moderation": "Нарушения могут привести к удалению видео или блокировке канала "
                                  "держателем сайта.",

        "publish.title": "Опубликовать видео",
        "publish.description": "Публикация выполняется через локальный ITubeP Bridge — откроется "
                                 "несколько окон: подтверждение, выбор файла, название и описание.",
        "publish.button": "Опубликовать видео",
        "publish.status_pairing": "Сопряжение с мостом...",
        "publish.status_wizard": "Запущен мастер публикации — переключитесь на окна ITubeP Bridge...",
        "publish.status_published": "Опубликовано! ",
        "publish.open_video": "Открыть видео",
        "publish.status_error": "Ошибка: ",
        "publish.enter_code_prompt": "Введите код подтверждения из окна ITubeP Bridge:",
        "publish.error_no_code": "Код не введён",
        "publish.error_bad_code": "Неверный код или отклонено на мосте",
        "publish.error_unknown": "Неизвестная ошибка публикации",

        "video.nojs_needs_js": "Для просмотра прямо на сайте нужен JavaScript и установленный",
        "video.nojs_bridge_link": "ITubeP Bridge",
        "video.nojs_or_download": "Либо скачайте торрент-файл и смотрите локально любым "
                                    "BitTorrent-клиентом с поддержкой I2P:",
        "video.download_quality": "Скачать {quality} (.torrent)",

        "comments.heading": "Комментарии",
        "comments.placeholder": "Написать комментарий...",
        "comments.submit": "Отправить",
        "comments.load_more": "Показать ещё комментарии",
        "comments.status_sending": "Отправка...",
        "comments.status_error": "Ошибка: ",
        "comments.too_long": "Комментарий слишком длинный (максимум 2000 символов без учёта пробелов)",

        "player.status_pairing_done": "Сопряжение выполнено, добавляю торрент...",
        "player.status_adding_torrent": "Добавляю торрент...",
        "player.status_torrent_added": "Торрент добавлен, запускаю плеер...",
        "player.status_ready": "Готово к воспроизведению",
        "player.status_loading_fragments": "Некоторые фрагменты видео ещё загружаются...",
        "player.status_playback_error": "Ошибка воспроизведения: ",
        "player.status_reauth": "Сопряжение с мостом было отозвано, требуется повторное подтверждение...",
        "player.status_seeking": "Перемотка на сегмент {index}, ожидаю докачку...",
        "player.error_no_hls": "Браузер не поддерживает HLS",
        "player.error_no_torrent_fetch": "Не удалось получить .torrent с сайта",
        "player.error_no_stream_token": "Не удалось получить токен для воспроизведения",
        "player.error_bridge_rejected": "Мост отклонил добавление торрента: ",
        "player.error_blocked": "Этот сайт заблокирован в настройках моста — обратитесь к владельцу "
                                 "моста для разблокировки.",
        "player.error_cooldown": "Слишком частые попытки сопряжения — подождите немного и обновите страницу.",
        "player.error_token_revoked": "Токен отозван на стороне моста, требуется повторное сопряжение",
        "player.prompt_code": "Введите код подтверждения из окна ITubeP Bridge.\n"
                                "(окно моста могло появиться отдельно — переключитесь на него,\n"
                                "если код ещё не сгенерирован, подождите пару секунд)",
        "player.error_no_code": "Код не введён",
        "player.error_bad_code": "Неверный код или отклонено на мосте",
    },
}
