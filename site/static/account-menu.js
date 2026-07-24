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

    // TODO: "Настройки" — по клику дёрнуть мост (POST на локальный
    // эндпоинт настроек), мост обязан проверять, что запрос пришёл от
    // уже сопряжённого (авторизованного) origin, и молча игнорировать
    // иначе — чтобы посторонний сайт не мог спамить пользователя окнами
    // настроек. Реализуется отдельным шагом.
    var settingsBtn = document.getElementById("open-bridge-settings");
    if (settingsBtn) {
        settingsBtn.addEventListener("click", function () {
            closeMenu();
        });
    }
})();
