// nsfw-sync.js — синхронизирует переключатель "показывать NSFW" из
// настроек МОСТА (bridge/ui/settings_window.py) в cookie этого браузера
// (site_show_nsfw, см. app/main.py:NSFW_COOKIE_NAME).
//
// Зачем это вообще нужно: сайт полностью server-rendered (FastAPI отдаёт
// уже готовый HTML с отфильтрованными списками видео) и не хранит понятия
// "текущий пользователь" — он в принципе не может сам решить, показывать
// ли NSFW-контент ЭТОМУ конкретному посетителю. Источник истины для этого
// решения живёт локально в мосте. Поэтому на каждой странице этот скрипт:
//   1. спрашивает мост, что сейчас выбрано (GET /bridge/nsfw_preference,
//      единственный bridge-эндпоинт без токена/пейринга — см. docstring
//      BridgePolicy.get_show_nsfw_preference);
//   2. сравнивает с тем, что сейчас лежит в cookie сайта;
//   3. если они разошлись — уходит на /set-nsfw, который проставит cookie
//      и вернёт на ту же страницу уже с верным фильтром в HTML.
//
// Тот же паттерн независимости, что и у языка сайта (см. i18n.js —
// "язык сайта переключается кнопкой на сайте, не мостом"): здесь наоборот,
// источник истины — мост, а сайт только синхронизируется с ним, потому
// что нет смысла заставлять человека настраивать это отдельно на каждом
// itubep-сайте, который он посещает.
(function () {
    const BRIDGE_URL = "http://127.0.0.1:9080";
    const COOKIE_NAME = "site_show_nsfw";

    function readCookie(name) {
        const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : null;
    }

    async function sync() {
        let showNsfw;
        try {
            const resp = await fetch(`${BRIDGE_URL}/bridge/nsfw_preference`, { method: "GET" });
            if (!resp.ok) return;
            const data = await resp.json();
            showNsfw = !!data.show_nsfw;
        } catch (e) {
            // Мост недоступен/не запущен/CORS отклонён — оставляем как есть.
            // Cookie по умолчанию не установлена, а _show_nsfw() на сайте
            // трактует отсутствие cookie как "скрыто" — безопасный дефолт,
            // никакого дополнительного действия тут не требуется.
            return;
        }

        const current = readCookie(COOKIE_NAME);
        const currentBool = current === "1";
        if (current !== null && currentBool === showNsfw) {
            return; // уже синхронизировано, повторный редирект не нужен
        }

        const next = window.location.pathname + window.location.search;
        window.location.href = `/set-nsfw?show=${showNsfw ? "1" : "0"}&next=${encodeURIComponent(next)}`;
    }

    sync();
})();
