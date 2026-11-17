"""Read related profiles through accessible live Models and exact Artifact links."""

from __future__ import annotations

import json

from printstash_core.imports import CaptureProfile
from sqlmodel import Session, col, select

from app.db.models import (
    ArtifactProvenanceLink,
    File,
    Model,
    ModelProvenanceSource,
    ProvenanceCapture,
    User,
)
from app.db.scopes import live
from app.schemas.inbox import CaptureProfileDraft
from app.schemas.provenance import RelatedProfileRead, RelatedProfilesRead

from .access import accessible_live_model_ids_stmt


def related_profiles(
    session: Session, user: User, model_id: int
) -> RelatedProfilesRead:
    visible = accessible_live_model_ids_stmt(session, user)
    identities = select(ModelProvenanceSource.identity_key).where(
        ModelProvenanceSource.model_id == model_id,
        col(ModelProvenanceSource.model_id).in_(visible),
    )
    rows = session.exec(
        select(
            ArtifactProvenanceLink,
            File,
            Model,
            ModelProvenanceSource,
            ProvenanceCapture,
        )
        .join(File, col(File.id) == ArtifactProvenanceLink.file_id)
        .join(Model, col(Model.id) == File.model_id)
        .join(
            ModelProvenanceSource,
            col(ModelProvenanceSource.id)
            == ArtifactProvenanceLink.provenance_source_id,
        )
        .join(
            ProvenanceCapture,
            col(ProvenanceCapture.id) == ArtifactProvenanceLink.capture_id,
        )
        .where(
            col(Model.id).in_(visible),
            live(File),
            col(ModelProvenanceSource.identity_key).in_(identities),
            ModelProvenanceSource.model_id == Model.id,
            ProvenanceCapture.provenance_source_id == ModelProvenanceSource.id,
        )
        .order_by(Model.id, File.id, ArtifactProvenanceLink.id)
        .limit(501)
    ).all()
    items = []
    seen = set()
    for link, file, model, source, capture in rows[:500]:
        raw = (
            json.loads(capture.snapshot_json)
            .get("profiles", {})
            .get(link.source_file_id)
        )
        if raw is None:
            continue
        profile = CaptureProfile.from_dict(raw)
        identity = (file.id, source.identity_key, profile.id)
        if identity in seen:
            continue
        seen.add(identity)
        assert file.id is not None and model.id is not None
        items.append(
            RelatedProfileRead(
                model_id=model.id,
                model_name=model.name,
                file_id=file.id,
                filename=file.original_filename,
                provider=source.provider,
                captured_at=capture.captured_at,
                profile=CaptureProfileDraft.model_validate(profile.to_dict()),
            )
        )
    return RelatedProfilesRead(items=items, has_more=len(rows) > 500)
