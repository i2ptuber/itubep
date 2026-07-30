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

// Обёртка над fetch для всех запросов с Bearer-токеном: 401 — токен
// невалиден/отозван (см. AuthenticationFailed в bridge/policy/authz.py),
// выбрасывает BridgeTokenRevokedError, чтобы вызывающий код среагировал
// переустановкой сопряжения. 403 — это НЕ проблема токена: конкретное
// действие отклонено (например, пользователь нажал "Отклонить" на
// диалоге подтверждения в confirm-режиме моста) — токен по-прежнему
// валиден, повторное сопряжение тут не нужно и не должно запускаться
// (раньше 403 обрабатывался так же, как 401, — из-за этого простой отказ
// на ОДНОМ действии выглядел как "сопряжение сломалось", и сайт тихо
// перезапускал весь pairing-флоу с prompt() за кодом, который легко не
// заметить).
async function bridgeFetchAuthed(url, options, token) {
    const resp = await fetch(url, {
        ...options,
        headers: { ...(options.headers || {}), "Authorization": `Bearer ${token}` },
    });
    if (resp.status === 401) {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        throw new BridgeTokenRevokedError(window.t("player.error_token_revoked"));
    }
    return resp;
}

async function addVideoToBridge(token, videoId, torrentName) {
    // Единый торрент на ВСЕ качества (см. bridge/snark/publisher.py) —
    // добавляется в мост ровно один раз за просмотр, независимо от того,
    // сколько раз зритель переключит качество внутри этого же сеанса
    // просмотра (переключение качества — это смена приоритета файлов
    // внутри уже добавленного торрента, см. changeQuality ниже, а не
    // повторное добавление).
    const torrentResp = await fetch(
        `${window.ITUBEP_VIDEO.site_origin}/api/video/${videoId}/torrent`
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

// Сообщает мосту, что зритель выбрал другое качество — сегменты этого
// качества (диапазон [file_start_index, file_start_index+file_count) в
// едином торренте, см. манифест) получат приоритет high, все остальные —
// normal (см. bridge/snark/integration.py:set_quality_priority). НЕ
// критично для воспроизведения, если запрос не удался (то же самое
// поведение best-effort, что и notifyBridgeSeek ниже) — сегменты всё
// равно продолжат докачиваться, просто без форсированного приоритета.
async function notifyBridgeQualityChange(token, torrentId, quality) {
    try {
        await bridgeFetchAuthed(`${BRIDGE_URL}/bridge/set_quality`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                torrent_id: torrentId,
                high_start: quality.file_start_index,
                high_count: quality.file_count,
            }),
        }, token);
    } catch (e) {
        console.warn("[itubep] failed to notify bridge about quality change:", e);
    }
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

function buildPlaylistUrl(streamToken, torrentId, durations, indices) {
    const durationsB64 = base64UrlSafe(JSON.stringify(durations));
    const indicesB64 = base64UrlSafe(JSON.stringify(indices));
    return `${BRIDGE_URL}/bridge/playlist?stream_token=${encodeURIComponent(streamToken)}` +
        `&torrent_id=${torrentId}&durations_b64=${encodeURIComponent(durationsB64)}` +
        `&indices_b64=${encodeURIComponent(indicesB64)}`;
}

async function initPlayer() {
    const { video_id, qualities, torrent_name } = window.ITUBEP_VIDEO;
    const statusEl = document.getElementById("player-status");
    const containerEl = document.getElementById("player-container");
    const fallbackEl = document.getElementById("nojs-fallback");
    const videoEl = document.getElementById("video-player");
    const qualitySelectEl = document.getElementById("quality-select");

    if (!qualities || qualities.length === 0) {
        return; // остаёмся на no-JS fallback
    }

    try {
        let token = await getOrCreateToken();
        containerEl.style.display = "block";
        statusEl.textContent = window.t("player.status_pairing_done");

        // Единый торрент на ВСЕ качества (см. bridge/snark/publisher.py) —
        // добавляется в мост один раз за весь сеанс просмотра, независимо
        // от того, сколько раз зритель переключит качество ниже.
        let handle;
        try {
            console.log("[itubep] добавляю торрент с текущим токеном...");
            handle = await addVideoToBridge(token, video_id, torrent_name);
        } catch (e) {
            console.warn("[itubep] addVideoToBridge упал:", e, "instanceof BridgeTokenRevokedError =", e instanceof BridgeTokenRevokedError);
            if (!(e instanceof BridgeTokenRevokedError)) throw e;
            statusEl.textContent = window.t("player.status_reauth");
            console.log("[itubep] токен отозван, запрашиваю новое сопряжение...");
            token = await getOrCreateToken(/* forceNewPairing */ true);
            console.log("[itubep] новое сопряжение получено, повторяю добавление торрента...");
            statusEl.textContent = window.t("player.status_pairing_done");
            handle = await addVideoToBridge(token, video_id, torrent_name);
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

        // По умолчанию — самое лёгкое качество (qualities[0], см.
        // QUALITY_ORDER в bridge/snark/publisher.py) — самый быстрый старт
        // воспроизведения в сети I2P; зритель может переключиться выше
        // через селектор ниже в любой момент.
        let currentQuality = qualities[0];
        let hls = null;

        qualitySelectEl.innerHTML = "";
        for (const q of qualities) {
            const opt = document.createElement("option");
            opt.value = q.label;
            opt.textContent = q.label;
            qualitySelectEl.appendChild(opt);
        }
        qualitySelectEl.value = currentQuality.label;
        qualitySelectEl.style.display = "";

        function absoluteIndices(quality) {
            return quality.segment_durations.map((_, i) => quality.file_start_index + i);
        }

        const playlistUrl = buildPlaylistUrl(
            stream_token, handle.torrent_id, currentQuality.segment_durations, absoluteIndices(currentQuality),
        );

        async function changeQuality(newLabel) {
            const newQuality = qualities.find(q => q.label === newLabel);
            if (!newQuality || newQuality.label === currentQuality.label) return;

            // Зритель НЕ должен видеть паузу/перезагрузку ради переключения
            // качества: текущий фрагмент, который он уже смотрит, доигрывает
            // в СТАРОМ качестве как есть, а следующие за ним — уже в новом.
            // Поэтому вместо мгновенной подмены всего плейлиста собираем
            // "склеенный" плейлист: сегменты [0..n] — те же индексы старого
            // качества (это тот же самый уже отданный/докачанный контент,
            // см. bridge/transport/http_server.py:playlist — indices_b64),
            // а [n+1..конец] — индексы нового качества. n — номер сегмента,
            // который сейчас воспроизводится (по currentTime).
            const n = segmentIndexForTime(currentQuality.segment_durations, videoEl.currentTime);

            const oldAbs = absoluteIndices(currentQuality);
            const newAbs = absoluteIndices(newQuality);
            // Общая длина плейлиста — по новому качеству (сегменты за
            // пределами старого, если оно короче/длиннее, просто берутся
            // из нового целиком).
            const stitchedDurations = [];
            const stitchedIndices = [];
            for (let i = 0; i < newQuality.segment_durations.length; i++) {
                if (i <= n && i < currentQuality.segment_durations.length) {
                    stitchedDurations.push(currentQuality.segment_durations[i]);
                    stitchedIndices.push(oldAbs[i]);
                } else {
                    stitchedDurations.push(newQuality.segment_durations[i]);
                    stitchedIndices.push(newAbs[i]);
                }
            }

            // Приоритет — только на ещё не показанный зрителю "хвост" нового
            // качества (от n+1 до конца), а не на всё качество целиком: то,
            // что зритель уже посмотрел (в старом качестве), торопить незачем.
            const tailStart = newQuality.file_start_index + (n + 1);
            const tailCount = Math.max(0, newQuality.file_count - (n + 1));
            if (tailCount > 0) {
                notifyBridgeQualityChange(token, handle.torrent_id, { file_start_index: tailStart, file_count: tailCount });
            }

            currentQuality = newQuality;
            const newPlaylistUrl = buildPlaylistUrl(stream_token, handle.torrent_id, stitchedDurations, stitchedIndices);

            // Сегменты до n+1 в новом плейлисте — те же самые URL
            // (тот же index), что и были в старом, то есть уже
            // отданы/закэшированы браузером — hls.js не должен их
            // перекачивать заново, только продолжить с n+1 в новом
            // качестве. Позицию/паузу всё равно восстанавливаем на всякий
            // случай — hls.js по-прежнему делает полный reload источника.
            const resumeAt = videoEl.currentTime;
            const wasPaused = videoEl.paused;

            statusEl.textContent = window.t("player.status_switching_quality");

            if (hls) {
                hls.once(Hls.Events.MANIFEST_PARSED, () => {
                    videoEl.currentTime = resumeAt;
                    if (!wasPaused) videoEl.play().catch(() => {});
                    statusEl.textContent = "";
                });
                hls.loadSource(newPlaylistUrl);
            } else {
                // Safari (нативный HLS) — просто переставляем src и
                // восстанавливаем позицию/состояние воспроизведения вручную.
                videoEl.src = newPlaylistUrl;
                videoEl.addEventListener("loadedmetadata", function onLoaded() {
                    videoEl.removeEventListener("loadedmetadata", onLoaded);
                    videoEl.currentTime = resumeAt;
                    if (!wasPaused) videoEl.play().catch(() => {});
                    statusEl.textContent = "";
                });
            }
        }

        qualitySelectEl.addEventListener("change", () => {
            changeQuality(qualitySelectEl.value).catch(e => {
                console.warn("[itubep] failed to switch quality:", e);
                statusEl.textContent = window.t("player.status_playback_error") + e.message;
            });
        });

        if (window.Hls && Hls.isSupported()) {
            hls = new Hls({
                manifestLoadingTimeOut: 20000,
                fragLoadingTimeOut: 60000, // сегменты могут ещё докачиваться через торрент
                // Раньше было 20 попыток — этого категорически не хватает,
                // когда сегмент реально докачивается через BT ещё несколько
                // минут: hls.js исчерпывал бюджет ретраев, помечал фрагмент
                // фатальной ошибкой и переставал его когда-либо перезапрашивать,
                // даже если сегмент потом успевал докачаться на бридже.
                // 1000 попыток с потолком backoff в 8с — это фактически
                // "проверяй каждые ~8 секунд, пока не появится", без
                // отдельного кастомного поллинга.
                fragLoadingMaxRetry: 1000,
                fragLoadingRetryDelay: 2000,
                fragLoadingMaxRetryTimeout: 8000,
            });
            hls.loadSource(playlistUrl);
            hls.attachMedia(videoEl);
            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                statusEl.textContent = window.t("player.status_ready");
                fallbackEl.style.display = "none";
            });
            // Счётчик подряд идущих "фатальных" ошибок загрузки одного и
            // того же зависшего фрагмента — сбрасывается при любой успешной
            // докачке фрагмента. Верхняя граница нужна только на случай
            // действительно безнадёжно зависшего сегмента (рой пропал и
            // т.п.), чтобы не долбить hls.js бесконечно — на практике за
            // ~200 попыток по 5с (≈16 минут) сегмент либо докачается, либо
            // дело не в докачке.
            let fragRecoveryAttempts = 0;
            const MAX_FRAG_RECOVERY_ATTEMPTS = 200;

            hls.on(Hls.Events.FRAG_LOADED, () => {
                fragRecoveryAttempts = 0;
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

                    if (data.fatal) {
                        // hls.js исчерпал свой внутренний бюджет ретраев и
                        // считает фрагмент безнадёжным — но через торрент он
                        // вполне может докачаться чуть позже. startLoad()
                        // сбрасывает внутреннее состояние загрузки и
                        // продолжает с текущей позиции воспроизведения, то
                        // есть hls.js снова начинает запрашивать тот же (и
                        // следующие) сегмент(ы), как будто ретраи ещё не
                        // кончались.
                        if (fragRecoveryAttempts < MAX_FRAG_RECOVERY_ATTEMPTS) {
                            fragRecoveryAttempts++;
                            const attempt = fragRecoveryAttempts;
                            setTimeout(() => {
                                console.log(`[itubep] возобновляю загрузку после фатальной ошибки фрагмента (попытка ${attempt})`);
                                hls.startLoad();
                            }, 5000);
                        } else {
                            statusEl.textContent = window.t("player.status_playback_error") + data.details;
                        }
                    }
                    return;
                }

                if (data.fatal) {
                    statusEl.textContent = window.t("player.status_playback_error") + data.details;
                }
            });
            // Форсирование приоритета сегментов через мост при перемотке
            // временно отключено — stop/start у i2psnark на каждую
            // перемотку рвёт все текущие BT-соединения, что оказалось
            // хуже, чем просто ждать естественную докачку по порядку
            // (enableInOrder). См. bridge/snark/integration.py:set_seek_priority.
            // HLS.js сам ждёт нужный сегмент естественным путём — статус
            // "loading_fragments" выше уже покрывает это ожидание, отдельное
            // сообщение про перемотку было лишним и здесь больше не выводится.
        } else if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
            // Safari — нативная поддержка HLS
            videoEl.src = playlistUrl;
            statusEl.textContent = window.t("player.status_ready");
            fallbackEl.style.display = "none";
        } else {
            throw new Error(window.t("player.error_no_hls"));
        }

        // "Ready to play" полезен только до старта воспроизведения — как
        // только видео реально пошло, статус больше не нужен и просто
        // зависал бы под плеером, визуально сливаясь с описанием видео
        // ниже. Проверяем текущий текст перед очисткой, чтобы не затереть
        // более свежее сообщение (например, "loading_fragments"), которое
        // могло появиться между MANIFEST_PARSED и первым фактическим playing.
        videoEl.addEventListener("playing", () => {
            if (statusEl.textContent === window.t("player.status_ready")) {
                statusEl.textContent = "";
            }
        });
    } catch (e) {
        console.error("[itubep] failed to initialize bridge player:", e);
        statusEl.textContent = "";
        containerEl.style.display = "none";
        // fallback остаётся видимым — пользователь может скачать .torrent вручную
    }
}

initPlayer();
