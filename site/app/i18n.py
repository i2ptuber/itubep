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
        "studio.nsfw_label": "NSFW",
        "studio.nsfw_checkbox": "Contains NSFW content",
        "studio.save_button": "Save changes",
        "studio.status_saving": "Saving...",
        "studio.status_saved": "Saved",
        "studio.status_error": "Error: ",
        "studio.no_videos": "No published videos yet.",
        "studio.error_pairing": "Failed to pair with the bridge",
        "studio.col_video": "Video",
        "studio.col_notifications": "Notifications",
        "studio.col_access": "Access",
        "studio.col_date": "Date",
        "studio.col_downloads": "Downloads",
        "studio.col_comments": "Comments",
        "studio.tab_content": "Content",
        "studio.tab_channel": "Channel settings",
        "studio.action_thumbnail": "Change thumbnail",
        "studio.action_edit": "Edit details",
        "studio.action_comments": "Comments",
        "studio.action_open": "Open video",
        "studio.notice_removed": "Restricted by site owner",
        "studio.comments_label": "comments",
        "studio.thumbnail_status_updating": "Switch to the ITubeP Bridge window to choose a new thumbnail...",
        "studio.thumbnail_status_updated": "Thumbnail updated",
        "studio.thumbnail_status_error": "Error: ",
        "studio.edit_title": "Video details",
        "studio.edit_back": "← Content",
        "studio.discard_changes": "Discard changes",
        "studio.save_short": "Save",
        "studio.title_field_label": "Title (required)",
        "studio.description_field_label": "Description",
        "studio.thumbnail_section_heading": "Thumbnail",
        "studio.thumbnail_section_hint": "Choose an image that stands out and draws viewers' attention.",
        "studio.thumbnail_upload_box": "Upload file",
        "studio.thumbnail_from_video_box": "Pick from video",
        "studio.thumbnail_ab_box": "A/B test",
        "studio.unavailable_tooltip": "Not available in this build",
        "studio.video_link_label": "Video link",
        "studio.copy_button": "Copy",
        "studio.copied": "Copied!",
        "studio.quality_label": "Video quality",
        "studio.nav_details": "Details",
        "studio.your_video_label": "Your video",
        "studio.not_found": "This video was not found in your channel",
        "studio.status_discarded": "Changes discarded",

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
        "rules.item_nsfw": "Videos with clearly NSFW content (porn and gore) must be marked as NSFW on publication.",
        "rules.item_harm": "No content that promotes violence, or clearly illegal "
                            "activity.",
        "rules.item_moderation": "Violations may result in a video being removed or a channel "
                                  "being banned by the site operator.",

        "publish.title": "Publish a video",
        "publish.description": "First choose a video file (and, optionally, a thumbnail image) through "
                                 "the local ITubeP Bridge — it will ask you to confirm the request and pick "
                                 "the files — then fill in the title and description right here on the site.",
        "publish.choose_file_btn": "Choose video file (via Bridge)",
        "publish.status_pairing": "Pairing with the bridge...",
        "publish.status_choosing": "Switch to the ITubeP Bridge window to confirm and choose a file...",
        "publish.file_chosen": "File chosen: ",
        "publish.thumbnail_chosen": "Thumbnail chosen: ",
        "publish.change_file_btn": "Choose a different file",
        "publish.title_label": "Title",
        "publish.title_placeholder": "Video title",
        "publish.description_label": "Description",
        "publish.description_placeholder": "Video description (optional)",
        "publish.nsfw_label": "NSFW content",
        "publish.nsfw_hint": "Required — mark whether this video contains NSFW content. "
                              "You can change this later from the Studio.",
        "publish.nsfw_no": "No",
        "publish.nsfw_yes": "Yes, this video is NSFW",
        "publish.qualities_label": "Available qualities",
        "publish.qualities_hint": "360p is always included as a safe baseline for slow I2P connections. "
                                   "All selected qualities are downloaded/seeded together as one torrent — "
                                   "viewers just switch between them instantly, no extra download.",
        "publish.quality_360p": "360p (always included)",
        "publish.quality_480p": "480p",
        "publish.quality_720p": "720p",
        "publish.quality_1080p": "1080p",
        "publish.quality_heavy_warning": "Higher qualities take much longer to encode and download over I2P, "
                                          "and use significantly more disk space on both your machine and viewers'.",
        "publish.button": "Publish video",
        "publish.status_publishing": "Publishing — this can take a while depending on the video length...",
        "publish.status_published": "Published! ",
        "publish.open_video": "Open video",
        "publish.status_error": "Error: ",
        "publish.error_no_file": "Choose a video file first",
        "publish.error_no_title": "Enter a title",
        "publish.error_no_nsfw_choice": "Choose whether this video contains NSFW content",
        "publish.enter_code_prompt": "Enter the confirmation code from the ITubeP Bridge window:",
        "publish.error_no_code": "No code entered",
        "publish.error_bad_code": "Invalid code or denied by the bridge",
        "publish.error_unknown": "Unknown publishing error",

        "video.nojs_needs_js": "Viewing directly on the site requires JavaScript and an installed",
        "video.nojs_bridge_link": "ITubeP Bridge",
        "video.nojs_or_download": "Or download the torrent file and watch locally with any "
                                    "BitTorrent client that supports I2P:",
        "video.download_torrent": "Download the video torrent (all qualities, .torrent)",
        "video.quality_label": "Quality:",

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
        "player.status_switching_quality": "Switching quality, waiting for the new segments to download...",
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
        "studio.nsfw_label": "NSFW",
        "studio.nsfw_checkbox": "Содержит NSFW-контент",
        "studio.save_button": "Сохранить изменения",
        "studio.status_saving": "Сохранение...",
        "studio.status_saved": "Сохранено",
        "studio.status_error": "Ошибка: ",
        "studio.no_videos": "Опубликованных видео пока нет.",
        "studio.error_pairing": "Не удалось сопрячься с мостом",
        "studio.col_video": "Видео",
        "studio.col_notifications": "Уведомления",
        "studio.col_access": "Доступ",
        "studio.col_date": "Дата",
        "studio.col_downloads": "Скачивания",
        "studio.col_comments": "Комментарии",
        "studio.tab_content": "Управление контентом",
        "studio.tab_channel": "Управление каналом",
        "studio.action_thumbnail": "Сменить превью",
        "studio.action_edit": "Редактировать сведения",
        "studio.action_comments": "Комментарии",
        "studio.action_open": "Открыть видео",
        "studio.notice_removed": "Ограничено держателем сайта",
        "studio.comments_label": "комментариев",
        "studio.thumbnail_status_updating": "Переключитесь на окно ITubeP Bridge, чтобы выбрать новое превью...",
        "studio.thumbnail_status_updated": "Превью обновлено",
        "studio.thumbnail_status_error": "Ошибка: ",
        "studio.edit_title": "Сведения о видео",
        "studio.edit_back": "← Контент на канале",
        "studio.discard_changes": "Отменить изменения",
        "studio.save_short": "Сохранить",
        "studio.title_field_label": "Название (обязательное поле)",
        "studio.description_field_label": "Описание",
        "studio.thumbnail_section_heading": "Значок",
        "studio.thumbnail_section_hint": "Выберите изображение, которое будет привлекать внимание зрителей.",
        "studio.thumbnail_upload_box": "Загрузить файл",
        "studio.thumbnail_from_video_box": "Выбрать из видео",
        "studio.thumbnail_ab_box": "A/B-тестирование",
        "studio.unavailable_tooltip": "Недоступно в этой версии",
        "studio.video_link_label": "Ссылка на видео",
        "studio.copy_button": "Копировать",
        "studio.copied": "Скопировано!",
        "studio.quality_label": "Качество видео",
        "studio.nav_details": "Сведения",
        "studio.your_video_label": "Ваше видео",
        "studio.not_found": "Это видео не найдено в вашем канале",
        "studio.status_discarded": "Изменения отменены",

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
        "rules.item_nsfw": "Видео с откровенным NSFW контентом (порно и 'gore') должны быть отмечены как NSFW при публикации",
        "rules.item_harm": "Запрещён контент, пропагандирующий насилие, или явно "
                            "незаконную деятельность.",
        "rules.item_moderation": "Нарушения могут привести к удалению видео или блокировке канала "
                                  "держателем сайта.",

        "publish.title": "Опубликовать видео",
        "publish.description": "Сначала выберите видеофайл (и, по желанию, картинку для превью) через "
                                 "локальный ITubeP Bridge (он попросит подтвердить запрос и выбрать файлы), "
                                 "а затем заполните название и описание прямо здесь, на сайте.",
        "publish.choose_file_btn": "Выбрать видеофайл (через Bridge)",
        "publish.status_pairing": "Сопряжение с мостом...",
        "publish.status_choosing": "Переключитесь на окно ITubeP Bridge, чтобы подтвердить запрос и выбрать файл...",
        "publish.file_chosen": "Выбран файл: ",
        "publish.thumbnail_chosen": "Выбрано превью: ",
        "publish.change_file_btn": "Выбрать другой файл",
        "publish.title_label": "Название",
        "publish.title_placeholder": "Название видео",
        "publish.description_label": "Описание",
        "publish.description_placeholder": "Описание видео (необязательно)",
        "publish.nsfw_label": "NSFW-контент",
        "publish.nsfw_hint": "Обязательно — отметьте, содержит ли это видео NSFW-контент. "
                              "Позже это можно изменить в Студии.",
        "publish.nsfw_no": "Нет",
        "publish.nsfw_yes": "Да, это видео NSFW",
        "publish.qualities_label": "Доступные качества",
        "publish.qualities_hint": "360p всегда включено как безопасный минимум для медленных соединений I2P. "
                                   "Все выбранные качества скачиваются и раздаются вместе, одним торрентом — "
                                   "зрители переключаются между ними мгновенно, без повторной докачки.",
        "publish.quality_360p": "360p (включено всегда)",
        "publish.quality_480p": "480p",
        "publish.quality_720p": "720p",
        "publish.quality_1080p": "1080p",
        "publish.quality_heavy_warning": "Более высокие качества заметно дольше кодируются и качаются по I2P, "
                                          "а также занимают значительно больше места на диске — и у вас, и у зрителей.",
        "publish.button": "Опубликовать видео",
        "publish.status_publishing": "Публикация — это может занять время в зависимости от длины видео...",
        "publish.status_published": "Опубликовано! ",
        "publish.open_video": "Открыть видео",
        "publish.status_error": "Ошибка: ",
        "publish.error_no_file": "Сначала выберите видеофайл",
        "publish.error_no_title": "Введите название",
        "publish.error_no_nsfw_choice": "Отметьте, содержит ли это видео NSFW-контент",
        "publish.enter_code_prompt": "Введите код подтверждения из окна ITubeP Bridge:",
        "publish.error_no_code": "Код не введён",
        "publish.error_bad_code": "Неверный код или отклонено на мосте",
        "publish.error_unknown": "Неизвестная ошибка публикации",

        "video.nojs_needs_js": "Для просмотра прямо на сайте нужен JavaScript и установленный",
        "video.nojs_bridge_link": "ITubeP Bridge",
        "video.nojs_or_download": "Либо скачайте торрент-файл и смотрите локально любым "
                                    "BitTorrent-клиентом с поддержкой I2P:",
        "video.download_torrent": "Скачать торрент видео (все качества, .torrent)",
        "video.quality_label": "Качество:",

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
        "player.status_switching_quality": "Переключаю качество, ожидаю докачку новых сегментов...",
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
