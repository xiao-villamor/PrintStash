"""Closed compute policy and diagnostic contracts; no driver imports."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt


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
    render_submissions: int = Field(default=0, ge=0)
    # Host copies into upload staging; separate from queue command submissions.
    render_upload_calls: int = Field(default=0, ge=0)
    render_bounds_vertices: int = Field(default=0, ge=0)
    # Zero smooth normals repaired locally by flat shading, including unused
    # vertices. This is neither a count of models nor a whole-model CPU retry.
    geometry_local_normal_fallback_vertices: int = Field(default=0, ge=0)
    # Exact bins for 1..8 inputs/frames per completed readback fence.
    render_batch_model_counts: tuple[NonNegativeInt, ...] = Field(
        default=(0,) * 8, min_length=8, max_length=8
    )
    render_batch_frame_counts: tuple[NonNegativeInt, ...] = Field(
        default=(0,) * 8, min_length=8, max_length=8
    )
    render_interactive_requests: int = Field(default=0, ge=0)
    render_background_requests: int = Field(default=0, ge=0)
    render_cache_hits: int = Field(default=0, ge=0)
    geometry_upload_bytes: int = Field(default=0, ge=0)
    geometry_gpu_uploads: int = Field(default=0, ge=0)
    geometry_cpu_uploads: int = Field(default=0, ge=0)
    geometry_gpu_normal_uploads: int = Field(default=0, ge=0)
    geometry_cpu_normal_fallbacks: int = Field(default=0, ge=0)
    geometry_normal_seconds: float = Field(default=0.0, ge=0)
    geometry_packing_min_faces: int | None = Field(default=None, ge=1)
    # Host preparation and upload submission; execution includes the GPU fence.
    geometry_packing_seconds: float = Field(default=0.0, ge=0)
    render_postprocess_gpu: bool = False
    geometry_input_bytes: int = Field(default=0, ge=0)
    geometry_input_cache_hits: int = Field(default=0, ge=0)
    geometry_input_cache_bytes: int = Field(default=0, ge=0)


class ComputeUnavailable(Exception):
    def __init__(self, reason: Reason):
        super().__init__(reason.value)
        self.reason = reason
