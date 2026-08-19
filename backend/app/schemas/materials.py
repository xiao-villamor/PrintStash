from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import MaterialSlotState, MaterialSource


class MaterialToolWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    nozzle_diameter_mm: float | None = Field(default=None, gt=0, le=5)


class MaterialSlotWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    tool_key: str | None = Field(default=None, max_length=64)
    state: MaterialSlotState = MaterialSlotState.UNKNOWN
    material_type: str | None = Field(default=None, max_length=64)
    material_brand: str | None = Field(default=None, max_length=128)
    color_hex: str | None = Field(default=None, max_length=16)
    spool_id: int | None = Field(default=None, gt=0)
    spool_name: str | None = Field(default=None, max_length=256)
    spool_filament_id: int | None = Field(default=None, gt=0)

    @field_validator("color_hex")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip().upper()
        if not cleaned.startswith("#"):
            cleaned = f"#{cleaned}"
        if len(cleaned) != 7 or any(
            char not in "#0123456789ABCDEF" for char in cleaned
        ):
            raise ValueError("material_color_invalid")
        return cleaned


class ManualMaterialStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime | None = None
    tools: list[MaterialToolWrite] = Field(default_factory=list, max_length=16)
    slots: list[MaterialSlotWrite] = Field(default_factory=list, max_length=64)


class MaterialToolRead(BaseModel):
    tool_key: str
    label: str
    nozzle_diameter_mm: float | None = None
    source: MaterialSource
    observed_at: datetime | None = None
    stale: bool = False


class MaterialSlotRead(BaseModel):
    slot_key: str
    label: str
    tool_key: str | None = None
    state: MaterialSlotState
    source: MaterialSource
    confidence: Literal["operator_set", "provider_reported", "externally_tracked"]
    material_type: str | None = None
    material_brand: str | None = None
    color_hex: str | None = None
    spool_id: int | None = None
    spool_name: str | None = None
    spool_filament_id: int | None = None
    observed_at: datetime | None = None
    stale: bool = False


class PrinterMaterialStateRead(BaseModel):
    printer_id: int
    updated_at: datetime
    provider_sync_enabled: bool
    tools: list[MaterialToolRead]
    slots: list[MaterialSlotRead]


class CompatibilityPrinterRead(BaseModel):
    printer_id: int
    verdict: Literal["compatible", "mismatch", "unknown"]
    reasons: list[str] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    color_advisories: list[str] = Field(default_factory=list)


class CompatibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int = Field(gt=0)
    printer_ids: list[int] = Field(min_length=1, max_length=100)


class ArtifactRequirementRead(BaseModel):
    tool_index: int
    material_type: str | None = None
    color_hex: str | None = None


class CompatibilityRead(BaseModel):
    file_id: int
    requirements: list[ArtifactRequirementRead]
    nozzle_diameter_mm: float | None = None
    printers: list[CompatibilityPrinterRead]
