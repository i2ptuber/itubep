"""
schemas.py — Pydantic-модели для API-контракта.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChannelRegisterRequest(BaseModel):
    channel_id: str = Field(..., min_length=1, max_length=64)
    public_key: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1, max_length=200)
    updated_at: str
    latest_videos: list[str] = Field(default_factory=list)
    signature: str = Field(..., min_length=1)


class ChannelResponse(BaseModel):
    channel_id: str
    display_name: str
    updated_at: str
    site_display_name: str = ""
    site_description: str = ""
    pinned_video_id: str | None = None

    class Config:
        from_attributes = True


class StudioUpdateRequest(BaseModel):
    """
    Подписанная владельцем канала (мостом) запись обновления "студии" —
    та же схема подписи, что у ChannelRegisterRequest/VideoManifest:
    ed25519 по каноническому JSON без поля signature, verify_signature()
    против public_key, УЖЕ ИЗВЕСТНОГО сайту из БД (не из тела запроса —
    иначе кто угодно мог бы подставить свой ключ и подписать им).
    """
    channel_id: str = Field(..., min_length=1, max_length=64)
    site_display_name: str = Field("", max_length=200)
    site_description: str = Field("", max_length=5000)
    pinned_video_id: str | None = None
    video_access: dict[str, str] = Field(default_factory=dict)
    updated_at: str
    # audience_origin: адрес сайта, для которого мост подписал эту запись
    # (см. bridge/policy/authz.py). Часть ПОДПИСАННЫХ данных — сайт
    # обязан сверить это со СВОИМ собственным адресом (см. main.py:
    # _require_audience_matches_this_site) и отклонить запрос, если
    # запись была подписана "для" другого сайта. Без этого поля подпись
    # была бы переносима между любыми сайтами, знающими тот же
    # channel_id — что позволяло бы одному (в т.ч. недобросовестному)
    # сайту получить от моста подписанную запись и реплеить её на ДРУГОЙ,
    # настоящий сайт жертвы.
    audience_origin: str = Field(..., min_length=1)
    signature: str = Field(..., min_length=1)
        
class QualityManifest(BaseModel):
    label: str
    torrent_infohash: str


class VideoManifest(BaseModel):
    video_id: str = Field(..., min_length=64, max_length=64)  # sha256 hex
    channel_id: str
    title: str = Field(..., min_length=1, max_length=300)
    description: str = ""
    duration: int = 0
    qualities: list[QualityManifest]
    published_at: str
    signature: str
    
class SearchResultItem(BaseModel):
    video_id: str
    title: str
    channel_id: str
    channel_display_name: str
    duration_seconds: int
    download_count: int

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
    
class VideoListItem(BaseModel):
    video_id: str
    title: str
    duration_seconds: int
    download_count: int
    published_at: str
    access_level: str = "public"

    class Config:
        from_attributes = True


class ChannelVideosResponse(BaseModel):
    channel_id: str
    total: int
    videos: list[VideoListItem]


class StudioAuthRequest(BaseModel):
    """
    Подписанный владельцем канала запрос на чтение полного состояния
    "студии" (включая unlisted/private видео, которые НЕ отдаются
    публичными read-путями). timestamp вместо монотонного updated_at —
    это read-запрос, не запись, достаточно окна свежести (см. main.py:
    STUDIO_STATE_TIMESTAMP_TOLERANCE_SECONDS) вместо хранения состояния
    на сервере.
    """
    channel_id: str = Field(..., min_length=1, max_length=64)
    timestamp: str
    audience_origin: str = Field(..., min_length=1)  # см. StudioUpdateRequest
    signature: str = Field(..., min_length=1)


class StudioStateResponse(BaseModel):
    channel_id: str
    display_name: str
    site_display_name: str = ""
    site_description: str = ""
    pinned_video_id: str | None = None
    videos: list[VideoListItem]


class ReactionRequest(BaseModel):
    """
    Подписанный владельцем channel_id голос (лайк/дизлайк) — тот же
    паттерн подписи, что StudioUpdateRequest. value=0 значит "отменить
    голос" (удалить строку в video_reactions), не отдельный "нейтральный" тип.
    """
    video_id: str = Field(..., min_length=1, max_length=64)
    channel_id: str = Field(..., min_length=1, max_length=64)
    value: int = Field(..., ge=-1, le=1)
    updated_at: str
    audience_origin: str = Field(..., min_length=1)  # см. StudioUpdateRequest
    signature: str = Field(..., min_length=1)

    @field_validator("value")
    @classmethod
    def value_not_zero_unless_explicit(cls, v: int) -> int:
        # ge=-1/le=1 уже ограничивают диапазон -1/0/1 — 0 явно разрешён
        # (означает "убрать голос"), отдельная проверка тут не нужна,
        # оставлено для читаемости namespace значений в одном месте.
        return v


class ReactionResponse(BaseModel):
    video_id: str
    like_count: int
    dislike_count: int
    my_value: int  # -1/0/1 — текущий голос ЭТОГО channel_id после применения запроса


class CommentCreateRequest(BaseModel):
    """
    Подписанный владельцем channel_id комментарий. Лимит длины — 2000
    символов БЕЗ УЧЁТА пробельных символов (по просьбе — комментарии могут
    быть многострочными/развёрнутыми); raw max_length=10000 — потолок на
    сырую длину (с пробелами/переводами строк), чтобы нельзя было обойти
    смысловой лимит абсурдным паддингом из пробелов.
    client_nonce — обязателен, уникален (см. models.py:Comment) — защита
    от replay: без него повтор того же подписанного запроса создавал бы
    дубликат комментария на каждую отправку.
    """
    video_id: str = Field(..., min_length=1, max_length=64)
    channel_id: str = Field(..., min_length=1, max_length=64)
    body: str = Field(..., min_length=1, max_length=10000)
    client_nonce: str = Field(..., min_length=8, max_length=64)
    created_at: str
    audience_origin: str = Field(..., min_length=1)  # см. StudioUpdateRequest
    signature: str = Field(..., min_length=1)

    @field_validator("body")
    @classmethod
    def body_length_without_whitespace(cls, v: str) -> str:
        stripped_len = len("".join(v.split()))
        if stripped_len == 0:
            raise ValueError("Comment body must contain non-whitespace characters")
        if stripped_len > 2000:
            raise ValueError("Comment body exceeds 2000 non-whitespace characters")
        return v


class CommentItem(BaseModel):
    id: int
    channel_id: str
    channel_display_name: str
    body: str
    created_at: str

    class Config:
        from_attributes = True


class CommentsListResponse(BaseModel):
    video_id: str
    total: int
    comments: list[CommentItem]
