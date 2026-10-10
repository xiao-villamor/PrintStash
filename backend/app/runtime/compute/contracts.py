"""Closed compute policy and diagnostic contracts; no driver imports."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ComputeMode(StrEnum):
    AUTO = "auto"
    CPU = "cpu"


class Operation(StrEnum):
    RENDER = "render"
    DENSE = "dense"
    SPARSE = "sparse"


class Reason(StrEnum):
    READY = "ready"
    PREVIEW = "preview"
    DISABLED = "disabled"
    RUNTIME_MISSING = "runtime_missing"
    ADAPTER_MISSING = "adapter_missing"
    SOFTWARE_ADAPTER = "software_adapter"
    UNQUALIFIED = "unqualified"
    DEVICE_FAILED = "device_failed"
    CAPACITY = "capacity"
    DEADLINE = "deadline"
    INVALID_INPUT = "invalid_input"
    CANCELLED = "cancelled"
    BROKER_UNAVAILABLE = "broker_unavailable"


class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    identity: str
    name: str
    vendor_id: int
    device_id: int
    driver: str
    backend: str


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    available: bool
    reason: Reason


class ComputeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ComputeMode
    device: DeviceInfo | None
    runtime_identity: str
    capabilities: list[Capability]
    budget_bytes: int = Field(ge=0)
    reserved_bytes: int = Field(ge=0)
    resident_entries: int = Field(ge=0)
    queue_depth: int = Field(ge=0)
    completed: int = Field(ge=0)
    fallbacks: int = Field(ge=0)
    cold_start_seconds: float = Field(ge=0)
    resident_model_ids: list[str]
    queue_seconds: float = Field(ge=0)
    execution_seconds: float = Field(ge=0)
    input_bytes: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    batch_inputs: int = Field(ge=0)
    residency_hits: int = Field(ge=0)
    residency_misses: int = Field(ge=0)
    host_reserved_bytes: int = Field(ge=0)
    queued_bytes: int = Field(ge=0)
    rendering_transfer_seconds: float = Field(ge=0)
    inference_batches: int = Field(ge=0)
    render_batches: int = Field(default=0, ge=0)
    render_frames: int = Field(default=0, ge=0)
    render_cache_hits: int = Field(default=0, ge=0)
    geometry_upload_bytes: int = Field(default=0, ge=0)
    render_postprocess_gpu: bool = False
    geometry_input_bytes: int = Field(default=0, ge=0)
    geometry_input_cache_hits: int = Field(default=0, ge=0)
    geometry_input_cache_bytes: int = Field(default=0, ge=0)


class ComputeUnavailable(Exception):
    def __init__(self, reason: Reason):
        super().__init__(reason.value)
        self.reason = reason
