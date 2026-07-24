/*
 * i18n.js — переводчик для строк, которые генерируются на клиенте
 * (player.js, инлайн-скрипт publish.html). window.ITUBEP_STRINGS
 * заполняется сервером в base.html из текущего языка САЙТА (cookie
 * site_lang, см. app/i18n.py) — независимо от языка моста.
 *
 * Использование: window.t("player.status_ready")
 * Подстановка: window.t("video.download_quality", {quality: "720p"})
 */
(function () {
    "use strict";

    var STRINGS = window.ITUBEP_STRINGS || {};

    window.t = function (key, params) {
        var str = STRINGS[key];
        if (str === undefined) return key;
        if (params) {
            Object.keys(params).forEach(function (k) {
                str = str.split("{" + k + "}").join(params[k]);
            });
        }
        return str;
    };
})();
