from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import PrintJobState, PrinterProvider, PrinterStatus


def validate_remote_filename_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    cleaned = value.strip()
    if not cleaned:
        return cleaned
    parts = cleaned.replace("\\", "/").split("/")
    if cleaned.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("remote_filename_invalid")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError("remote_filename_invalid")
    return cleaned


class PrinterCapabilities(BaseModel):
    can_start: bool
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_live_status: bool
    can_upload: bool
    can_list_files: bool = False
    support_level: str = "stable"
    support_notes: list[str] = Field(default_factory=list)
    unsupported_actions: list[str] = Field(default_factory=list)


class PrinterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    provider: PrinterProvider = PrinterProvider.MOONRAKER
    moonraker_url: Optional[str] = Field(default=None, max_length=512)
    api_key: Optional[str] = Field(default=None, max_length=512)
    bambu_host: Optional[str] = Field(default=None, max_length=255)
    bambu_serial: Optional[str] = Field(default=None, max_length=255)
    bambu_access_code: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=4096)
    group: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "PrinterCreate":
        if self.provider == PrinterProvider.MOONRAKER and not self.moonraker_url:
            raise ValueError("moonraker_url_required")
        if self.provider == PrinterProvider.BAMBU_LAN:
            if not self.bambu_host:
                raise ValueError("bambu_host_required")
            if not self.bambu_serial:
                raise ValueError("bambu_serial_required")
            if not self.bambu_access_code:
                raise ValueError("bambu_access_code_required")
        return self


class PrinterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Optional[PrinterProvider] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    moonraker_url: Optional[str] = Field(default=None, max_length=512)
    api_key: Optional[str] = Field(default=None, max_length=512)
    bambu_host: Optional[str] = Field(default=None, max_length=255)
    bambu_serial: Optional[str] = Field(default=None, max_length=255)
    bambu_access_code: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=4096)
    group: Optional[str] = Field(default=None, max_length=128)


class PrinterRead(BaseModel):
    id: int
    name: str
    provider: PrinterProvider
    moonraker_url: str
    has_api_key: bool
    bambu_host: Optional[str] = None
    bambu_serial: Optional[str] = None
    has_bambu_access_code: bool = False
    capabilities: PrinterCapabilities
    notes: Optional[str] = None
    group: Optional[str] = None
    status: PrinterStatus
    last_seen_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MoonrakerConfigRead(BaseModel):
    printer_id: int
    server_info: dict[str, Any] = Field(default_factory=dict)
    printer_info: dict[str, Any] = Field(default_factory=dict)
    moonraker_config: dict[str, Any] = Field(default_factory=dict)
    klipper_config: dict[str, Any] = Field(default_factory=dict)


class SendToPrinter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int = Field(gt=0)
    start_print: bool = False
    remote_filename: Optional[str] = Field(default=None, max_length=512)

    @field_validator("remote_filename")
    @classmethod
    def validate_remote_filename(cls, value: Optional[str]) -> Optional[str]:
        return validate_remote_filename_value(value)


class StartPrinterFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_filename: str = Field(min_length=1, max_length=512)
    file_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("remote_filename")
    @classmethod
    def validate_remote_filename(cls, value: str) -> str:
        cleaned = validate_remote_filename_value(value)
        if not cleaned:
            raise ValueError("remote_filename_invalid")
        return cleaned


class PrintJobRead(BaseModel):
    id: int
    printer_id: int
    file_id: int
    model_id: int
    remote_filename: str
    state: PrintJobState
    progress: float
    source: str = "vault"
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class PrinterFileRead(BaseModel):
    id: int
    printer_id: int
    printer_name: Optional[str] = None
    file_id: Optional[int] = None
    model_id: Optional[int] = None
    model_name: Optional[str] = None
    original_filename: Optional[str] = None
    remote_filename: str
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    matched_by: str
    modified_at: Optional[datetime] = None
    last_seen_at: datetime
    missing_since: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
