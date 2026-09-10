"""Validated opt-in configuration; threshold changes never rewrite decisions."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from printstash_core.mesh.similarity.verification import EvidenceClass
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.db.models import AuditLog, SystemConfig, User

Confidence = Annotated[float, Field(ge=0.5, le=1.0, allow_inf_nan=False)]


class CandidateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_confidence: Confidence = 0.9
    class_overrides: dict[EvidenceClass, Confidence] = Field(default_factory=dict)


class SimilaritySettings(CandidateSelection):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    fingerprint_on_ingest: bool = True
    triangle_cap: int = Field(default=200_000, ge=100, le=200_000, strict=True)
    sample_points: int = Field(default=5000, ge=256, le=5000, strict=True)
    voxel_resolution: Literal[64] = 64
    max_candidates: int = Field(default=20, ge=1, le=100, strict=True)
    page_size: int = Field(default=100, ge=1, le=1000, strict=True)
    schedule_hours: int = Field(default=0, ge=0, le=168, strict=True)
    embeddings_enabled: bool = False

    def threshold_for(self, evidence_class: str) -> float:
        return self.class_overrides.get(evidence_class, self.minimum_confidence)


def read_settings(session: Session) -> SimilaritySettings:
    row = session.get(SystemConfig, 1)
    values = {
        name: getattr(settings, f"similarity_{name}")
        for name in SimilaritySettings.model_fields
        if name not in ("class_overrides", "voxel_resolution")
    }
    try:
        if row is not None and row.similarity_settings_json:
            stored = json.loads(row.similarity_settings_json)
            if not isinstance(stored, dict):
                raise ValueError("invalid settings")
            values.update(stored)
        return SimilaritySettings.model_validate(values)
    except (ValueError, ValidationError) as exc:
        raise OperationError(
            "similarity_configuration_invalid", kind=ErrorKind.CONFLICT
        ) from exc


def update_settings(
    session: Session, actor: User, patch: dict[str, object]
) -> SimilaritySettings:
    if not actor.is_superuser:
        raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    before = read_settings(session)
    try:
        after = SimilaritySettings.model_validate(before.model_dump() | patch)
    except ValidationError as exc:
        raise OperationError(
            "similarity_settings_invalid", kind=ErrorKind.UNPROCESSABLE
        ) from exc
    row = session.get(SystemConfig, 1)
    if row is None:
        row = SystemConfig(id=1)
    row.similarity_settings_json = after.model_dump_json()
    session.add(row)
    session.add(
        AuditLog(
            actor_id=actor.id,
            action="similarity_settings",
            resource_type="system_config",
            resource_id=1,
            diff_json=json.dumps(
                {"before": before.model_dump(), "after": after.model_dump()}
            ),
        )
    )
    session.commit()
    return after
