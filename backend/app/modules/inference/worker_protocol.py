"""Bounded, validated messages exchanged with the isolated native worker."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_INPUT_BYTES = 34 * 1024**2
MAX_OUTPUT_BYTES = 1024**2


class WorkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["text", "image", "point_cloud"]
    text: str | None = Field(default=None, max_length=16384)
    rgb_base64: str | None = Field(default=None, max_length=4 * 1024**2)
    width: int = Field(default=0, ge=0, le=1024, strict=True)
    height: int = Field(default=0, ge=0, le=1024, strict=True)
    points_base64: str | None = Field(default=None, max_length=320000)


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    space_json: str | None = Field(default=None, max_length=32768)
    inputs: list[WorkerInput] = Field(default_factory=list, max_length=8)
    sparse_text: str | None = Field(default=None, min_length=1, max_length=16384)


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_hash: str
    vectors: list[list[float]] = Field(max_length=8)
    truncated: list[bool] = Field(default_factory=list, max_length=8)


class WorkerError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^embedding_[a-z_]{1,64}$")
