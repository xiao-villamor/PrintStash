from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from app.db.models import FileRevisionStatus, FileType


class MetadataRead(BaseModel):
    slicer_name: Optional[str] = None
    slicer_version: Optional[str] = None
    printer_model: Optional[str] = None
    nozzle_diameter_mm: Optional[float] = None
    layer_height_mm: Optional[float] = None
    infill_percent: Optional[float] = None

    estimated_time_s: Optional[int] = None
    filament_weight_g: Optional[float] = None
    filament_length_mm: Optional[float] = None
    filament_cost: Optional[float] = None
    material_type: Optional[str] = None
    material_brand: Optional[str] = None

    bbox_x_mm: Optional[float] = None
    bbox_y_mm: Optional[float] = None
    bbox_z_mm: Optional[float] = None
    volume_mm3: Optional[float] = None
    triangle_count: Optional[int] = None


class FileRead(BaseModel):
    id: int
    model_id: int
    original_filename: str
    file_type: FileType
    version: int
    size_bytes: int
    sha256: str
    revision_status: Optional[FileRevisionStatus] = None
    revision_notes: Optional[str] = None
    is_recommended: bool = False
    uploaded_at: datetime
    metadata: Optional[MetadataRead] = None


class FileRevisionUpdate(BaseModel):
    revision_status: Optional[FileRevisionStatus] = None
    revision_notes: Optional[str] = None
    is_recommended: Optional[bool] = None


class ModelRead(BaseModel):
    id: int
    name: str
    slug: str
    hash: str
    category: Optional[str] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    files: List[FileRead] = []


class ModelPrinterFileRead(BaseModel):
    file_id: int
    printer_id: int
    printer_name: str
    remote_filename: str
    matched_by: str
    last_seen_at: datetime
    missing_since: Optional[datetime] = None


class ModelListItem(BaseModel):
    id: int
    name: str
    slug: str
    category: Optional[str] = None
    category_id: Optional[int] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    file_count: int
    updated_at: datetime


class ModelUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None  # path string; will be resolved/created
    tags: Optional[List[str]] = None  # replaces existing set


class CategoryRead(BaseModel):
    id: int
    name: str
    slug: str
    path: str
    parent_id: Optional[int] = None
    model_count: int = 0


class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[int] = None


class TagCreate(BaseModel):
    name: str


class TagRead(BaseModel):
    id: int
    name: str
    slug: str
    model_count: int = 0
