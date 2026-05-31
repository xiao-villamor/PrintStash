from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from pydantic import model_validator

from app.db.models import PrintJobState, PrinterProvider, PrinterStatus


class PrinterCapabilities(BaseModel):
    can_start: bool
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    can_live_status: bool
    can_upload: bool
    can_list_files: bool = False


class PrinterCreate(BaseModel):
    name: str
    provider: PrinterProvider = PrinterProvider.MOONRAKER
    moonraker_url: Optional[str] = None
    api_key: Optional[str] = None
    bambu_host: Optional[str] = None
    bambu_serial: Optional[str] = None
    bambu_access_code: Optional[str] = None
    notes: Optional[str] = None
    group: Optional[str] = None

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
    provider: Optional[PrinterProvider] = None
    name: Optional[str] = None
    moonraker_url: Optional[str] = None
    api_key: Optional[str] = None
    bambu_host: Optional[str] = None
    bambu_serial: Optional[str] = None
    bambu_access_code: Optional[str] = None
    notes: Optional[str] = None
    group: Optional[str] = None


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


class SendToPrinter(BaseModel):
    file_id: int
    start_print: bool = False
    remote_filename: Optional[str] = None


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
