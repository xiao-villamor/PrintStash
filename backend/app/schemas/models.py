from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import CollectionRole, FileRevisionStatus, FileType, PrintJobState


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
    collection: Optional[str] = None
    collection_id: Optional[int] = None
    description: Optional[str] = None
    source_url: Optional[str] = None
    effective_role: Optional[CollectionRole] = None
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


class ModelPrintJobRead(BaseModel):
    """A print job belonging to a model, enriched for the history view."""

    id: int
    printer_id: int
    printer_name: str
    file_id: int
    gcode_revision_number: Optional[int] = None
    revision_label: Optional[str] = None
    state: PrintJobState
    material_type: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime


class PrintSummaryRead(BaseModel):
    """Aggregated print metadata for a model's most recent G-code file."""

    layer_height_mm: Optional[float] = None
    estimated_time_s: Optional[int] = None
    filament_weight_g: Optional[float] = None
    material_type: Optional[str] = None
    slicer_name: Optional[str] = None


class ModelListItem(BaseModel):
    id: int
    name: str
    slug: str
    collection: Optional[str] = None
    collection_id: Optional[int] = None
    source_url: Optional[str] = None
    effective_role: Optional[CollectionRole] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    file_count: int
    # Newest mesh file (STL/3MF/OBJ) — lets the UI preload the 3D preview.
    mesh_file_id: Optional[int] = None
    printer_presence: List[ModelPrinterPresenceRead] = []
    updated_at: datetime
    print_summary: Optional[PrintSummaryRead] = None
    recommended_revision_status: Optional[FileRevisionStatus] = None
    recommended_revision_label: Optional[str] = None


class TrashedModelRead(BaseModel):
    id: int
    name: str
    slug: str
    collection: Optional[str] = None
    tags: List[str] = []
    thumbnail_url: Optional[str] = None
    file_count: int
    size_bytes: int
    deleted_at: datetime
    expires_at: Optional[datetime] = None


class TrashPurgeRead(BaseModel):
    purged_model_ids: List[int] = []
    purged_count: int = 0


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
    collection_count: int = 0
    tag_count: int = 0
    printer_count: int = 0
    indexed_size_bytes: int = 0
    storage: StorageUsageRead


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4096)
    source_url: Optional[str] = Field(default=None, max_length=2048)
    collection: Optional[str] = Field(default=None, max_length=1024)
    tags: Optional[List[str]] = Field(default=None, max_length=100)

    @field_validator("source_url")
    @classmethod
    def normalise_source_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        parsed = urlparse(stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url_must_be_http_url")
        return stripped


class ManualPrintJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    printer_id: int
    file_id: int
    state: str = Field(default="completed", max_length=32)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = Field(default=None, max_length=1024)


class ImportedPrintJobRead(BaseModel):
    filename: str
    status: str
    print_duration: Optional[float] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    matched_file_id: Optional[int] = None
    imported: bool = False


class CollectionRead(BaseModel):
    id: int
    name: str
    slug: str
    path: str
    parent_id: Optional[int] = None
    model_count: int = 0
    effective_role: Optional[CollectionRole] = None


class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    parent_id: Optional[int] = None


class CollectionMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: Optional[int] = None


class CollectionPermissionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: CollectionRole


class CollectionPermissionRead(BaseModel):
    user_id: int
    username: str
    collection_id: int
    role: CollectionRole
    inherited: bool = False


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
    usage_count: int = 0
    created_at: datetime
    updated_at: datetime


class PrinterProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    printer_model: Optional[str] = Field(default=None, max_length=128)
    slicer_name: Optional[str] = Field(default=None, max_length=64)
    nozzle_diameter_mm: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=4096)


class PrinterProfileCreate(PrinterProfileBase):
    model_config = ConfigDict(extra="forbid")


class PrinterProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    printer_model: Optional[str] = Field(default=None, max_length=128)
    slicer_name: Optional[str] = Field(default=None, max_length=64)
    nozzle_diameter_mm: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=4096)


class PrinterProfileRead(PrinterProfileBase):
    id: int
    usage_count: int = 0
    created_at: datetime
    updated_at: datetime
