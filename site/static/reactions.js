"use strict";

// Использует menuGetOrCreateToken/menuBridgeFetchAuthed/MENU_BRIDGE_URL —
// они определены в account-menu.js, который подключается в base.html и
// есть на КАЖДОЙ странице (в т.ч. этой). Сами эти функции вызываются
// только из обработчиков кликов/сабмита ниже, то есть уже после того, как
// весь документ (включая account-menu.js в подвале <body>) точно
// выполнился — поэтому порядок подключения тут не важен, в отличие от
// немедленного (не отложенного) кода.

(function () {
    var videoId = window.ITUBEP_VIDEO && window.ITUBEP_VIDEO.video_id;
    if (!videoId) return;

    var COMMENT_MAX_NON_WHITESPACE = 2000;
    var COMMENTS_PAGE_SIZE = 50;

    // --- Лайк/дизлайк ---

    var likeBtn = document.getElementById("like-btn");
    var dislikeBtn = document.getElementById("dislike-btn");
    var likeCountEl = document.getElementById("like-count");
    var dislikeCountEl = document.getElementById("dislike-count");

    function setActiveReactionButton(myValue) {
        likeBtn.classList.toggle("active", myValue === 1);
        dislikeBtn.classList.toggle("active", myValue === -1);
    }

    function sendReaction(clickedValue) {
        return (async function () {
            try {
                var token = await menuGetOrCreateToken();
                // Повторный клик по уже активной кнопке — отмена голоса (value=0),
                // как и просили: лайк/дизлайк, а не просто "лайк навсегда".
                var newValue = likeBtn.classList.contains("active") && clickedValue === 1 ? 0
                    : dislikeBtn.classList.contains("active") && clickedValue === -1 ? 0
                    : clickedValue;
                var resp = await menuBridgeFetchAuthed(
                    MENU_BRIDGE_URL + "/bridge/react",
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ video_id: videoId, value: newValue }),
                    },
                    token,
                );
                if (!resp.ok) {
                    var errData = await resp.json().catch(function () { return {}; });
                    throw new Error(errData.error || ("bridge error (" + resp.status + ")"));
                }
                var data = await resp.json();
                likeCountEl.textContent = data.like_count;
                dislikeCountEl.textContent = data.dislike_count;
                setActiveReactionButton(data.my_value);
            } catch (err) {
                console.error("reaction:", err);
            }
        })();
    }

    if (likeBtn && dislikeBtn) {
        likeBtn.addEventListener("click", function () { sendReaction(1); });
        dislikeBtn.addEventListener("click", function () { sendReaction(-1); });
    }

    // --- Комментарии ---

    var listEl = document.getElementById("comments-list");
    var totalEl = document.getElementById("comments-total");
    var loadMoreBtn = document.getElementById("comments-load-more");
    var formEl = document.getElementById("comment-form");
    var inputEl = document.getElementById("comment-input");
    var charsLeftEl = document.getElementById("comment-chars-left");
    var statusEl = document.getElementById("comment-status");

    var commentsOffset = 0;

    function escapeHtml(s) {
        var div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    function renderComment(c) {
        var el = document.createElement("div");
        el.className = "comment-row";
        el.innerHTML =
            '<a class="comment-author" href="/channel/' + encodeURIComponent(c.channel_id) + '">' +
            escapeHtml(c.channel_display_name) + "</a>" +
            '<p class="comment-body">' + escapeHtml(c.body) + "</p>";
        return el;
    }

    async function loadComments(offset) {
        var resp = await fetch(
            "/api/video/" + encodeURIComponent(videoId) + "/comments" +
            "?limit=" + COMMENTS_PAGE_SIZE + "&offset=" + offset,
        );
        if (!resp.ok) return;
        var data = await resp.json();
        totalEl.textContent = data.total;
        data.comments.forEach(function (c) { listEl.appendChild(renderComment(c)); });
        commentsOffset = offset + data.comments.length;
        loadMoreBtn.style.display = commentsOffset < data.total ? "" : "none";
    }

    loadComments(0).catch(function (err) { console.error("comments:", err); });

    if (loadMoreBtn) {
        loadMoreBtn.addEventListener("click", function () {
            loadComments(commentsOffset).catch(function (err) { console.error("comments:", err); });
        });
    }

    function nonWhitespaceLength(s) {
        return s.replace(/\s/g, "").length;
    }

    function updateCharsLeft() {
        var left = COMMENT_MAX_NON_WHITESPACE - nonWhitespaceLength(inputEl.value);
        charsLeftEl.textContent = left;
        charsLeftEl.classList.toggle("form-hint-error", left < 0);
    }

    if (inputEl) {
        inputEl.addEventListener("input", updateCharsLeft);
        updateCharsLeft();
    }

    if (formEl) {
        formEl.addEventListener("submit", function (e) {
            e.preventDefault();
            var body = inputEl.value.trim();
            if (!body) return;
            if (nonWhitespaceLength(body) > COMMENT_MAX_NON_WHITESPACE) {
                statusEl.textContent = window.t("comments.too_long");
                return;
            }

            statusEl.textContent = window.t("comments.status_sending");

            (async function () {
                try {
                    var token = await menuGetOrCreateToken();
                    var resp = await menuBridgeFetchAuthed(
                        MENU_BRIDGE_URL + "/bridge/comment",
                        {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ video_id: videoId, body: body }),
                        },
                        token,
                    );
                    if (!resp.ok) {
                        var errData = await resp.json().catch(function () { return {}; });
                        throw new Error(errData.error || ("bridge error (" + resp.status + ")"));
                    }
                    var comment = await resp.json();
                    listEl.insertBefore(renderComment(comment), listEl.firstChild);
                    commentsOffset += 1;
                    totalEl.textContent = String(parseInt(totalEl.textContent, 10) + 1);
                    inputEl.value = "";
                    updateCharsLeft();
                    statusEl.textContent = "";
                } catch (err) {
                    console.error("comment submit:", err);
                    statusEl.textContent = window.t("comments.status_error") + err.message;
                }
            })();
        });
    }
})();
