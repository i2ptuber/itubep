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

    class Config:
        from_attributes = True
        
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
    view_count: int

    class Config:
        from_attributes = True


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
    
class VideoListItem(BaseModel):
    video_id: str
    title: str
    duration_seconds: int
    view_count: int
    published_at: str

    class Config:
        from_attributes = True


class ChannelVideosResponse(BaseModel):
    channel_id: str
    total: int
    videos: list[VideoListItem]
