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

from app.db.models import FileRevisionStatus


class ShareLinkCreate(BaseModel):
    expires_in_days: int = 7
    allow_download: bool = False
    revision_file_ids: Optional[list[int]] = None


class ShareLinkRead(BaseModel):
    """Admin view of a share link. Never includes the raw token."""

    id: int
    model_id: int
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    allow_download: bool
    revision_file_ids: Optional[list[int]] = None
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
    gcode_revision_number: Optional[int] = None
    revision_label: Optional[str] = None
    revision_status: Optional[FileRevisionStatus] = None
    revision_notes: Optional[str] = None
    is_recommended: bool = False
    # Geometry hints for display (mesh files only).
    bbox_x_mm: Optional[float] = None
    bbox_y_mm: Optional[float] = None
    bbox_z_mm: Optional[float] = None
    triangle_count: Optional[int] = None
    slicer_name: Optional[str] = None
    slicer_version: Optional[str] = None
    printer_model: Optional[str] = None
    nozzle_diameter_mm: Optional[float] = None
    layer_height_mm: Optional[float] = None
    first_layer_height_mm: Optional[float] = None
    infill_percent: Optional[float] = None
    wall_loops: Optional[int] = None
    support_material: Optional[bool] = None
    nozzle_temperature_c: Optional[float] = None
    bed_temperature_c: Optional[float] = None
    estimated_time_s: Optional[int] = None
    filament_weight_g: Optional[float] = None
    filament_length_mm: Optional[float] = None
    filament_cost: Optional[float] = None
    material_type: Optional[str] = None
    material_brand: Optional[str] = None


class PublicModelRead(BaseModel):
    """Strictly read-only, single-model projection for public share viewers."""

    name: str
    description: Optional[str] = None
    has_thumbnail: bool
    allow_download: bool
    files: list[PublicFileRead]
