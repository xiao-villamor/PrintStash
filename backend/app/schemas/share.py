"""Schemas for public share links.

``PublicModelRead`` / ``PublicFileRead`` are the *only* projection exposed on
the unauthenticated share endpoints. They deliberately omit owner, audit,
collection, print-history, and any cross-model data — a share grants view of
one model and nothing else.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ShareLinkCreate(BaseModel):
    expires_in_days: int = 7
    allow_download: bool = False


class ShareLinkRead(BaseModel):
    """Admin view of a share link. Never includes the raw token."""

    id: int
    model_id: int
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    allow_download: bool
    access_count: int
    created_at: datetime
    is_active: bool


class ShareLinkCreated(ShareLinkRead):
    """Returned once, at creation — carries the raw token + relative URL."""

    token: str
    url: str


class PublicFileRead(BaseModel):
    id: int
    original_filename: str
    file_type: str
    size_bytes: int
    version: int
    # Geometry hints for display (mesh files only).
    bbox_x_mm: Optional[float] = None
    bbox_y_mm: Optional[float] = None
    bbox_z_mm: Optional[float] = None
    triangle_count: Optional[int] = None


class PublicModelRead(BaseModel):
    """Strictly read-only, single-model projection for public share viewers."""

    name: str
    description: Optional[str] = None
    has_thumbnail: bool
    allow_download: bool
    files: list[PublicFileRead]
