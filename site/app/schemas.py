"""
schemas.py — Pydantic-модели для API-контракта.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    signature: str = Field(..., min_length=1)


class StudioStateResponse(BaseModel):
    channel_id: str
    display_name: str
    site_display_name: str = ""
    site_description: str = ""
    pinned_video_id: str | None = None
    videos: list[VideoListItem]
