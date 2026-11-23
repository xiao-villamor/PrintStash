"""Moonraker source configuration editor contract."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_CONFIG_BYTES = 256 * 1024
# JSON can encode a single ASCII byte as six bytes (for example a tab).
MAX_CONFIG_REQUEST_BYTES = 6 * MAX_CONFIG_BYTES + 1024

ConfigRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PrinterConfigFileEntry(BaseModel):
    path: str
    size_bytes: int
    modified: float
    writable: bool


class PrinterConfigFiles(BaseModel):
    files: list[PrinterConfigFileEntry]


class PrinterConfigFile(BaseModel):
    path: str
    content: str
    revision: ConfigRevision
    writable: bool


class PrinterConfigFileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=MAX_CONFIG_BYTES, repr=False)
    base_revision: ConfigRevision
