const MENU_BRIDGE_URL = "http://127.0.0.1:9080";
const MENU_TOKEN_STORAGE_KEY = "itubep_bridge_token";

// Сопряжение с мостом (тот же паттерн, что в player.js/publish.html —
// продублировано по конвенции проекта: страницы не делят общий JS-модуль).
// Имена констант/функций здесь намеренно с префиксом menu*, а не такие же,
// как в player.js/publish.html (BRIDGE_URL, TOKEN_STORAGE_KEY,
// getOrCreateToken) — account-menu.js подключается ИЗ base.html на КАЖДОЙ
// странице, в том числе на video.html, где уже есть свой player.js с
// top-level const BRIDGE_URL/TOKEN_STORAGE_KEY. В classic-скриптах все
// <script>-теги одного документа делят один и тот же global lexical scope,
// поэтому одинаковые имена const/let в двух файлах на одной странице дают
// SyntaxError "already been declared" и ломают страницу целиком.
async function menuGetOrCreateToken(forceNewPairing = false) {
    if (!forceNewPairing) {
        const token = localStorage.getItem(MENU_TOKEN_STORAGE_KEY);
        if (token) return token;
    } else {
        localStorage.removeItem(MENU_TOKEN_STORAGE_KEY);
    }

    const pairResp = await fetch(`${MENU_BRIDGE_URL}/bridge/pair/request`, {
        method: "POST", mode: "cors",
    });
    if (pairResp.ok) {
        try {
            const pairData = await pairResp.json();
            if (pairData.status === "blocked") {
                throw new Error(window.t("player.error_blocked"));
            }
            if (pairData.status === "cooldown") {
                throw new Error(window.t("player.error_cooldown"));
            }
        } catch (e) {
            if (e instanceof Error && e.message) throw e;
        }
    }

    const code = prompt(window.t("player.prompt_code"));
    if (!code) throw new Error(window.t("player.error_no_code"));

    const resp = await fetch(`${MENU_BRIDGE_URL}/bridge/pair/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(window.t("player.error_bad_code"));

    const data = await resp.json();
    localStorage.setItem(MENU_TOKEN_STORAGE_KEY, data.token);
    return data.token;
}

async function menuBridgeFetchAuthed(url, options, token) {
    const resp = await fetch(url, {
        ...options,
        headers: { ...(options.headers || {}), "Authorization": `Bearer ${token}` },
    });
    if (resp.status === 401 || resp.status === 403) {
        localStorage.removeItem(MENU_TOKEN_STORAGE_KEY);
    }
    return resp;
}

(function () {
    "use strict";

    var btn = document.getElementById("account-btn");
    var dropdown = document.getElementById("account-dropdown");
    if (!btn || !dropdown) return;

    function openMenu() {
        dropdown.classList.add("open");
        btn.setAttribute("aria-expanded", "true");
    }

    function closeMenu() {
        dropdown.classList.remove("open");
        btn.setAttribute("aria-expanded", "false");
    }

    function isOpen() {
        return dropdown.classList.contains("open");
    }

    btn.addEventListener("click", function (e) {
        e.stopPropagation();
        isOpen() ? closeMenu() : openMenu();
    });

    document.addEventListener("click", function (e) {
        if (isOpen() && !dropdown.contains(e.target) && e.target !== btn) {
            closeMenu();
        }
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && isOpen()) closeMenu();
    });

    // "Мой канал" — узнаём channel_id у моста (запросив сопряжение, если
    // его ещё нет — как при просмотре/публикации видео) и перекидываем на
    // /channel/<id>. Если канала ещё нет вообще — ведём в /studio, где он
    // создаётся при первом сохранении.
    var myChannelLink = document.getElementById("nav-my-channel");
    if (myChannelLink) {
        myChannelLink.addEventListener("click", function (e) {
            e.preventDefault();
            closeMenu();
            (async function () {
                try {
                    const token = await menuGetOrCreateToken();
                    const resp = await menuBridgeFetchAuthed(
                        `${MENU_BRIDGE_URL}/bridge/my_channel`, { method: "GET" }, token,
                    );
                    if (!resp.ok) {
                        const data = await resp.json().catch(function () { return {}; });
                        throw new Error(data.error || `bridge error (${resp.status})`);
                    }
                    const data = await resp.json();
                    window.location.href = data.channel_id ? `/channel/${data.channel_id}` : "/studio";
                } catch (err) {
                    console.error("nav-my-channel:", err);
                    window.location.href = "/studio";
                }
            })();
        });
    }

    // "Настройки" — открывает окно настроек моста. Сопрягаемся с мостом
    // точно так же, как при просмотре/публикации видео (запрос пейринга,
    // если токена ещё нет), затем просим мост открыть окно. Мост сам
    // проверяет, что запрос пришёл от уже сопряжённого (авторизованного)
    // origin — см. bridge/policy/authz.py:open_bridge_settings — так что
    // здесь достаточно просто вызвать эндпоинт и не показывать
    // пользователю отдельную ошибку, если что-то пошло не так на этом шаге.
    var settingsBtn = document.getElementById("open-bridge-settings");
    if (settingsBtn) {
        settingsBtn.addEventListener("click", function () {
            closeMenu();
            (async function () {
                try {
                    const token = await menuGetOrCreateToken();
                    const resp = await menuBridgeFetchAuthed(
                        `${MENU_BRIDGE_URL}/bridge/open_settings`, { method: "POST" }, token,
                    );
                    if (!resp.ok && resp.status !== 403) {
                        const data = await resp.json().catch(function () { return {}; });
                        console.error("open-bridge-settings:", data.error || `bridge error (${resp.status})`);
                    }
                    // 403 = мост посчитал этот origin несопряжённым/отозванным —
                    // ожидаемый молчаливый случай, см. authz.py:open_bridge_settings.
                } catch (err) {
                    // Пейринг отменён пользователем, мост недоступен и т.п. —
                    // без всплывающих ошибок, но видно в консоли для отладки.
                    console.error("open-bridge-settings:", err);
                }
            })();
        });
    }
})();
