"""Typed observations shared by the native and ingestion benchmark runners."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.media.mesh_contracts import ThumbnailFailureReason
    from app.schemas.jobs import DerivativeRead, DerivativeStatus


class BenchmarkMode(StrEnum):
    WORKER = "worker"
    INGESTION = "ingestion"


class SampleOutcome(StrEnum):
    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"
    TIMEOUT = "timeout"


def native_failure_outcome(reason: ThumbnailFailureReason) -> SampleOutcome:
    # Keep application imports lazy: CLI configuration must precede Settings.
    from app.modules.media.mesh_contracts import ThumbnailFailureReason

    if reason == ThumbnailFailureReason.TIMEOUT:
        return SampleOutcome.TIMEOUT
    if reason in {
        ThumbnailFailureReason.WORKER_FAILED,
        ThumbnailFailureReason.STORAGE,
        ThumbnailFailureReason.RENDERER_NO_OUTPUT,
    }:
        return SampleOutcome.FAILED
    return SampleOutcome.REFUSED


def unavailable_derivative_outcome(rows: Sequence[DerivativeRead]) -> SampleOutcome:
    """Preserve operational errors separately from known input/policy refusals."""
    from app.modules.media.mesh_contracts import ThumbnailFailureReason
    from app.schemas.jobs import DerivativeStatus

    if not rows:
        raise ValueError("unavailable derivative observations cannot be empty")
    outcomes: set[SampleOutcome] = set()
    for row in rows:
        if row.state == DerivativeStatus.FAILED:
            if row.failure_reason is None:
                outcomes.add(SampleOutcome.FAILED)
                continue
            try:
                reason = ThumbnailFailureReason(row.failure_reason)
            except ValueError:
                # Other producers can have their own failure codes. An unknown
                # operational error is not evidence of an input-policy refusal.
                outcomes.add(SampleOutcome.FAILED)
            else:
                outcomes.add(native_failure_outcome(reason))
        elif row.state in {
            DerivativeStatus.SKIPPED,
            DerivativeStatus.CANCELLED,
            DerivativeStatus.DISABLED,
        }:
            outcomes.add(SampleOutcome.REFUSED)
        else:
            raise ValueError("derivative is not terminal and unavailable")
    for outcome in (SampleOutcome.FAILED, SampleOutcome.TIMEOUT, SampleOutcome.REFUSED):
        if outcome in outcomes:
            return outcome
    raise RuntimeError("missing derivative outcome")


@dataclass(frozen=True)
class InputIdentity:
    name: str
    input_sha256: str
    input_bytes: int
    sample_index: int


@dataclass(frozen=True)
class IngestionObservation:
    name: str
    input_sha256: str
    input_bytes: int
    sample_index: int
    elapsed_ms: float
    outcome: SampleOutcome
    reason: str | None
    accepted_ms: float | None
    artifact_observed_ms: float | None
    metadata_observed_ms: float | None
    thumbnail_visible_ms: float | None
    metadata_state: DerivativeStatus | None
    thumbnail_state: DerivativeStatus | None
    file_id: int | None
    source_preexisting: bool | None
    source_probe_ms: float | None
    artifact_reused: bool | None
    output_bytes: int
    output_sha256: str | None
    output_format: str | None
    output_dimensions: tuple[int, int] | None
    original_verified: bool | None
    original_verification_ms: float | None
