from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import FileRevisionStatus, FileType


class MetadataRead(BaseModel):
    slicer_name: Optional[str] = None
    slicer_version: Optional[str] = None
    printer_model: Optional[str] = None
    nozzle_diameter_mm: Optional[float] = None
    layer_height_mm: Optional[float] = None
    first_layer_height_mm: Optional[float] = None
    infill_percent: Optional[float] = None
    wall_loops: Optional[int] = None
    top_shell_layers: Optional[int] = None
    bottom_shell_layers: Optional[int] = None
    support_material: Optional[bool] = None
    nozzle_temperature_c: Optional[float] = None
    bed_temperature_c: Optional[float] = None

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
    gcode_revision_number: Optional[int] = None
    size_bytes: int
    sha256: str
    revision_label: Optional[str] = None
    revision_status: Optional[FileRevisionStatus] = None
    revision_notes: Optional[str] = None
    is_recommended: bool = False
    uploaded_at: datetime
    metadata: Optional[MetadataRead] = None


class FileRevisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_label: Optional[str] = Field(default=None, max_length=128)
    revision_status: Optional[FileRevisionStatus] = None
    revision_notes: Optional[str] = Field(default=None, max_length=4096)
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


class ModelPrinterPresenceRead(BaseModel):
    printer_id: int
    printer_name: str
    file_count: int


class ModelListItem(BaseModel):
    id: int
    name: str
    slug: str
    category: Optional[str] = None
    category_id: Optional[int] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    file_count: int
    printer_presence: List[ModelPrinterPresenceRead] = []
    updated_at: datetime


class StorageUsageRead(BaseModel):
    backend: str
    prefix: Optional[str] = None
    bucket: Optional[str] = None
    object_count: int = 0
    total_size_bytes: int = 0
    ok: bool = True
    error: Optional[str] = None


class VaultStatsRead(BaseModel):
    model_count: int = 0
    file_count: int = 0
    source_file_count: int = 0
    gcode_file_count: int = 0
    category_count: int = 0
    tag_count: int = 0
    printer_count: int = 0
    indexed_size_bytes: int = 0
    storage: StorageUsageRead


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4096)
    category: Optional[str] = Field(default=None, max_length=1024)
    tags: Optional[List[str]] = Field(default=None, max_length=100)


class CategoryRead(BaseModel):
    id: int
    name: str
    slug: str
    path: str
    parent_id: Optional[int] = None
    model_count: int = 0


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    parent_id: Optional[int] = None


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class TagRead(BaseModel):
    id: int
    name: str
    slug: str
    model_count: int = 0


class FilamentProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    material_type: Optional[str] = Field(default=None, max_length=64)
    material_brand: Optional[str] = Field(default=None, max_length=128)
    cost_per_kg: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=4096)


class FilamentProfileCreate(FilamentProfileBase):
    model_config = ConfigDict(extra="forbid")


class FilamentProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    material_type: Optional[str] = Field(default=None, max_length=64)
    material_brand: Optional[str] = Field(default=None, max_length=128)
    cost_per_kg: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=4096)


class FilamentProfileRead(FilamentProfileBase):
    id: int
    created_at: datetime
    updated_at: datetime
