"""Single-Model detail and version history queries."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal
from typing import cast as type_cast

from sqlmodel import Session, select

from app.db.models import (
    CollectionRole,
    File,
    Metadata,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelStar,
    PrintJob,
    PrintJobState,
    ProvenanceCapture,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library import provenance
from app.modules.similarity.projections import summaries as similarity_summaries
from app.schemas.models import (
    ModelRead,
)
from app.schemas.provenance import (
    ModelProvenanceRead,
    ProvenanceCaptureSummaryRead,
    ProvenanceFieldRead,
    ProvenanceSourceRead,
)

from .access import _effective_model_role
from .projections import _file_reads_with_revisions, collection_name_for, thumb_url

# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def provenance_detail(session: Session, model_id: int) -> ModelProvenanceRead:
    """Compose the safe provenance read model with fixed query count.

    Raw snapshots and actor identifiers deliberately stay inside persistence;
    this model-view exposes only values, origins, and capture summaries.
    """
    sources = list(
        session.exec(
            select(ModelProvenanceSource)
            .where(ModelProvenanceSource.model_id == model_id)
            .order_by(ModelProvenanceSource.id.asc())
        ).all()
    )
    source_ids = [source.id for source in sources if source.id is not None]
    if not source_ids:
        return ModelProvenanceRead()
    fields_by_source: dict[int, list[ProvenanceFieldRead]] = defaultdict(list)
    for field in session.exec(
        select(ModelProvenanceField)
        .where(ModelProvenanceField.provenance_source_id.in_(source_ids))  # type: ignore[union-attr]
        .order_by(ModelProvenanceField.field_name.asc())
    ).all():
        fields_by_source[field.provenance_source_id].append(
            ProvenanceFieldRead(
                field_name=field.field_name,
                captured_value=json.loads(field.captured_value_json),
                captured_origin=field.captured_origin,
                user_value=json.loads(field.user_value_json)
                if field.user_value_json is not None
                else None,
                user_override_set=field.user_override_set,
                effective_value=provenance.effective_value(field),
                effective_origin=(
                    "user"
                    if field.user_override_set
                    else type_cast(
                        Literal["confirmed", "inferred"], field.captured_origin
                    )
                ),
                captured_at=field.captured_at,
                user_updated_at=field.user_updated_at,
            )
        )
    captures_by_source: dict[int, list[ProvenanceCaptureSummaryRead]] = defaultdict(
        list
    )
    for capture in session.exec(
        select(ProvenanceCapture)
        .where(ProvenanceCapture.provenance_source_id.in_(source_ids))  # type: ignore[union-attr]
        .order_by(ProvenanceCapture.captured_at.desc())
    ).all():
        captures_by_source[capture.provenance_source_id].append(
            ProvenanceCaptureSummaryRead(
                id=capture.id,  # type: ignore[arg-type]
                snapshot_sha256=capture.snapshot_sha256,
                adapter_version=capture.adapter_version,
                source_revision=capture.source_revision,
                captured_at=capture.captured_at,
                checked_at=capture.checked_at,
            )
        )
    return ModelProvenanceRead(
        sources=[
            ProvenanceSourceRead(
                id=source.id,  # type: ignore[arg-type]
                provider=source.provider,
                source_item_id=source.source_item_id,
                canonical_url=source.canonical_url,
                source_revision=source.source_revision,
                tags=json.loads(source.tags_json),
                first_captured_at=source.first_captured_at,
                last_checked_at=source.last_checked_at,
                fields=fields_by_source[source.id],  # type: ignore[index]
                captures=captures_by_source[source.id],  # type: ignore[index]
            )
            for source in sources
        ]
    )


def detail(session: Session, model_id: int, user: User) -> ModelRead | None:
    """Full model detail with files + metadata. None when missing or trashed."""
    m = session.get(Model, model_id)
    if m is None or m.deleted_at is not None:
        return None
    role = _effective_model_role(session, user, m)
    if not rbac.role_allows(role, CollectionRole.VIEW):
        return None

    files_with_meta = session.exec(
        select(File, Metadata)
        .where(File.model_id == model_id)
        .where(live(File))
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    starred = (
        session.exec(
            select(ModelStar.id).where(
                ModelStar.user_id == user.id, ModelStar.model_id == model_id
            )
        ).first()
        is not None
    )

    from .families import family_summaries

    return ModelRead(
        family=family_summaries(session, user, [model_id]).get(model_id),
        similarity=similarity_summaries(session, user, [model_id]).get(model_id, {}),
        id=m.id,  # type: ignore[arg-type]
        name=m.name,
        slug=m.slug,
        hash=m.hash,
        collection=collection_name_for(m),
        collection_id=m.collection_id,
        description=m.description,
        source_url=m.source_url,
        effective_role=role,
        # m.tags loads via selectin (see the Model relationship) — no extra query.
        tags=sorted(t.name for t in m.tags),
        thumbnail_url=thumb_url(m),
        created_at=m.created_at,
        updated_at=m.updated_at,
        files=_file_reads_with_revisions(session, files_with_meta),
        starred=starred,
    )


def artifact_outcomes(
    session: Session, model_id: int, file_ids: list[int]
) -> list[dict[str, object]]:
    """Aggregate measured print outcomes for selected live Artifacts."""
    ids = list(dict.fromkeys(file_ids))
    files = session.exec(
        select(File.id).where(File.model_id == model_id, File.id.in_(ids), live(File))
    ).all()
    if len(files) != len(ids):
        return []
    jobs = session.exec(
        select(PrintJob).where(
            PrintJob.model_id == model_id,
            PrintJob.file_id.in_(ids),
            live(PrintJob),
        )
    ).all()
    result: list[dict[str, object]] = []
    for file_id in ids:
        rows = [job for job in jobs if job.file_id == file_id]
        completed = [job for job in rows if job.state == PrintJobState.COMPLETED]
        failed = [job for job in rows if job.state == PrintJobState.FAILED]
        cancelled = [job for job in rows if job.state == PrintJobState.CANCELLED]
        decided = len(completed) + len(failed)
        durations = [
            job.actual_duration_s for job in rows if job.actual_duration_s is not None
        ]
        filament = [
            job.filament_g_effective
            for job in rows
            if job.filament_g_effective is not None
        ]
        costs = [job.cost for job in rows if job.cost is not None]
        result.append(
            {
                "file_id": file_id,
                "print_count": len(rows),
                "completed_count": len(completed),
                "failed_count": len(failed),
                "cancelled_count": len(cancelled),
                "success_rate": len(completed) / decided if decided else None,
                "average_duration_s": sum(durations) / len(durations)
                if durations
                else None,
                "total_filament_g": sum(filament) if filament else None,
                "total_cost": sum(costs) if costs else None,
            }
        )
    return result
