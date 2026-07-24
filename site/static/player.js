const BRIDGE_URL = "http://127.0.0.1:9080";
const TOKEN_STORAGE_KEY = "itubep_bridge_token";

class BridgeTokenRevokedError extends Error {}

async function getOrCreateToken(forceNewPairing = false) {
    if (!forceNewPairing) {
        const token = localStorage.getItem(TOKEN_STORAGE_KEY);
        if (token) return token;
    } else {
        // Старый токен мост больше не примет (отозван на стороне моста) —
        // выкидываем его сразу, чтобы случайно не использовать повторно
        // где-нибудь ещё до завершения нового сопряжения.
        localStorage.removeItem(TOKEN_STORAGE_KEY);
    }

    const pairResp = await fetch(`${BRIDGE_URL}/bridge/pair/request`, {
        method: "POST", mode: "cors",
    });
    // no-cors раньше скрывал от нас статус ответа — из-за этого сайт не мог
    // отличить "код показан пользователю" от "мост отказал" (заблокирован
    // origin, cooldown после недавнего запроса и т.п.) и всё равно показывал
    // prompt, на который правильный код никогда бы не пришёл.
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
            // не JSON/неожиданный формат — не блокируем сопряжение из-за этого
        }
    }

    const code = prompt(window.t("player.prompt_code"));
    if (!code) throw new Error(window.t("player.error_no_code"));

    const resp = await fetch(`${BRIDGE_URL}/bridge/pair/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(window.t("player.error_bad_code"));

    const data = await resp.json();
    localStorage.setItem(TOKEN_STORAGE_KEY, data.token);
    return data.token;
}

// Обёртка над fetch для всех запросов с Bearer-токеном: если мост отвечает
// 401/403 — токен отозван (или изначально невалиден), выбрасывает
// BridgeTokenRevokedError вместо обычной ошибки, чтобы вызывающий код мог
// среагировать переустановкой сопряжения, а не просто показать "не удалось".
async function bridgeFetchAuthed(url, options, token) {
    const resp = await fetch(url, {
        ...options,
        headers: { ...(options.headers || {}), "Authorization": `Bearer ${token}` },
    });
    if (resp.status === 401 || resp.status === 403) {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        throw new BridgeTokenRevokedError(window.t("player.error_token_revoked"));
    }
    return resp;
}

async function addVideoToBridge(token, videoId, quality, torrentName) {
    const torrentResp = await fetch(
        `${window.ITUBEP_VIDEO.site_origin}/api/video/${videoId}/chunk/${quality}.torrent`
    );
    if (!torrentResp.ok) throw new Error(window.t("player.error_no_torrent_fetch"));

    const torrentBytes = await torrentResp.arrayBuffer();
    const torrentB64 = btoa(String.fromCharCode(...new Uint8Array(torrentBytes)));

    const resp = await bridgeFetchAuthed(`${BRIDGE_URL}/bridge/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            torrent_b64: torrentB64,
            torrent_name: torrentName,
            video_id: videoId,
        }),
    }, token);

    if (!resp.ok) throw new Error(window.t("player.error_bridge_rejected") + await resp.text());
    return await resp.json();
}

function base64UrlSafe(str) {
    return btoa(str);
}

function segmentIndexForTime(durations, timeSeconds) {
    let acc = 0;
    for (let i = 0; i < durations.length; i++) {
        acc += durations[i];
        if (timeSeconds < acc) return i;
    }
    return durations.length - 1;
}

async function notifyBridgeSeek(token, torrentId, targetIndex) {
    try {
        await bridgeFetchAuthed(`${BRIDGE_URL}/bridge/seek`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                torrent_id: torrentId,
                target_segment_index: targetIndex,
                window_ahead: 5,
                window_behind: 1,
            }),
        }, token);
    } catch (e) {
        console.warn("[itubep] failed to notify bridge about seek:", e);
        // BridgeTokenRevokedError тоже попадает сюда — не критично прямо
        // сейчас (сегмент всё равно продолжит докачиваться естественным
        // путём), но localStorage уже очищен внутри bridgeFetchAuthed, так
        // что при следующей загрузке страницы сопряжение переустановится
        // само, без ручной чистки БД моста.
    }
}

async function initPlayer() {
    const { video_id, qualities } = window.ITUBEP_VIDEO;
    const statusEl = document.getElementById("player-status");
    const containerEl = document.getElementById("player-container");
    const fallbackEl = document.getElementById("nojs-fallback");
    const videoEl = document.getElementById("video-player");

    if (!qualities || qualities.length === 0) {
        return; // остаёмся на no-JS fallback
    }

    try {
        let token = await getOrCreateToken();
        containerEl.style.display = "block";
        statusEl.textContent = window.t("player.status_pairing_done");

        const quality = qualities[0];

        let handle;
        try {
            console.log("[itubep] добавляю торрент с текущим токеном...");
            handle = await addVideoToBridge(token, video_id, quality.label, quality.torrent_name);
        } catch (e) {
            console.warn("[itubep] addVideoToBridge упал:", e, "instanceof BridgeTokenRevokedError =", e instanceof BridgeTokenRevokedError);
            if (!(e instanceof BridgeTokenRevokedError)) throw e;
            statusEl.textContent = window.t("player.status_reauth");
            console.log("[itubep] токен отозван, запрашиваю новое сопряжение...");
            token = await getOrCreateToken(/* forceNewPairing */ true);
            console.log("[itubep] новое сопряжение получено, повторяю добавление торрента...");
            statusEl.textContent = window.t("player.status_pairing_done");
            handle = await addVideoToBridge(token, video_id, quality.label, quality.torrent_name);
        }

        statusEl.textContent = window.t("player.status_torrent_added");

        // Короткоживущий scoped-токен вместо основного bearer-токена в URL —
        // сам запрос авторизован заголовком (не светится в query), а в URL
        // плейлиста/сегментов уходит уже урезанный токен: только чтение
        // ЭТОГО torrent_id, ограниченное время жизни (см. authz.py:create_stream_token).
        // Так утечка URL (история браузера, скриншот, лог прокси) не даёт
        // постоянный полный доступ к мосту, только временный read-only одного видео.
        const streamTokenResp = await bridgeFetchAuthed(`${BRIDGE_URL}/bridge/stream_token`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ torrent_id: handle.torrent_id }),
        }, token);
        if (!streamTokenResp.ok) throw new Error(window.t("player.error_no_stream_token"));
        const { stream_token } = await streamTokenResp.json();

        const durationsB64 = base64UrlSafe(JSON.stringify(quality.segment_durations));
        const playlistUrl =
            `${BRIDGE_URL}/bridge/playlist?stream_token=${encodeURIComponent(stream_token)}` +
            `&torrent_id=${handle.torrent_id}&durations_b64=${encodeURIComponent(durationsB64)}`;

        if (window.Hls && Hls.isSupported()) {
            const hls = new Hls({
                manifestLoadingTimeOut: 20000,
                fragLoadingTimeOut: 60000, // сегменты могут ещё докачиваться через торрент
                fragLoadingMaxRetry: 20,
                fragLoadingRetryDelay: 2000,
            });
            hls.loadSource(playlistUrl);
            hls.attachMedia(videoEl);
            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                statusEl.textContent = window.t("player.status_ready");
                fallbackEl.style.display = "none";
            });
            hls.on(Hls.Events.ERROR, (event, data) => {
                console.warn("HLS.js событие ошибки:", data);

                // Ошибки загрузки фрагмента (404 от моста = сегмент ещё не
                // докачан) — это ожидаемая ситуация при просмотре по мере
                // докачки, не показываем как настоящую ошибку
                const isFragmentNotReady =
                    data.details === "fragLoadError" ||
                    data.details === "fragLoadTimeOut" ||
                    data.details === "fragParsingError";

                if (isFragmentNotReady) {
                    statusEl.textContent = window.t("player.status_loading_fragments");
                    return;
                }

                if (data.fatal) {
                    statusEl.textContent = window.t("player.status_playback_error") + data.details;
                }
            });
            let seekDebounceTimer = null;
            let lastSeekTargetIndex = null;
            videoEl.addEventListener("seeking", () => {
                // TODO(seek-priority): форсирование приоритета сегментов через
                // мост временно отключено — stop/start у i2psnark на каждую
                // перемотку рвёт все текущие BT-соединения, что оказалось
                // хуже, чем просто ждать естественную докачку по порядку
                // (enableInOrder). См. bridge/snark/integration.py:set_seek_priority.
                // Пока просто даём HLS.js ждать сегмент естественным путём.
                if (seekDebounceTimer) clearTimeout(seekDebounceTimer);
                seekDebounceTimer = setTimeout(() => {
                    const targetIndex = segmentIndexForTime(quality.segment_durations, videoEl.currentTime);
                    if (targetIndex === lastSeekTargetIndex) return;
                    lastSeekTargetIndex = targetIndex;
                    statusEl.textContent = window.t("player.status_seeking", {index: targetIndex});
                    // notifyBridgeSeek(token, handle.torrent_id, targetIndex); — отключено, см. TODO выше
                }, 300);
            });
        } else if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
            // Safari — нативная поддержка HLS
            videoEl.src = playlistUrl;
            statusEl.textContent = window.t("player.status_ready");
            fallbackEl.style.display = "none";
        } else {
            throw new Error(window.t("player.error_no_hls"));
        }
    } catch (e) {
        console.error("[itubep] failed to initialize bridge player:", e);
        statusEl.textContent = "";
        containerEl.style.display = "none";
        // fallback остаётся видимым — пользователь может скачать .torrent вручную
    }
}

initPlayer();
