"""Hardware-specific acceptance receipts gate automatic placement."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import DeviceInfo, Operation


class Qualification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    device_identity: str
    runtime_identity: str
    operation: Operation
    recipe: str
    minimum_units: int = Field(ge=1)
    maximum_units: int = Field(ge=1)
    maximum_batch_items: int = Field(default=1, ge=1, le=8)
    quality_passed: bool
    source_to_publication_speedup: float = Field(ge=1.5, allow_inf_nan=False)
    interactive_p95_ratio: float = Field(gt=0, le=1.1, allow_inf_nan=False)
    peak_device_bytes: int = Field(gt=0)
    peak_host_bytes: int = Field(gt=0)

    def accepts(
        self,
        device: DeviceInfo,
        runtime: str,
        operation: Operation,
        recipe: str,
        units: int,
        batch_items: int = 1,
    ) -> bool:
        return (
            self.quality_passed
            and self.device_identity == device.identity
            and self.runtime_identity == runtime
            and self.operation == operation
            and self.recipe == recipe
            and self.minimum_units <= units <= self.maximum_units
            and 1 <= batch_items <= self.maximum_batch_items
        )


def read_receipts(path: Path) -> tuple[Qualification, ...]:
    try:
        with path.open("rb") as source:
            payload = source.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            return ()
        data = json.loads(payload)
        if not isinstance(data, list) or len(data) > 256:
            return ()
        return tuple(Qualification.model_validate(item) for item in data)
    except OSError, ValueError, ValidationError:
        # A receipt is optional deployment evidence, never durable work intent.
        # Invalid or missing evidence keeps automatic routing on the CPU.
        return ()
