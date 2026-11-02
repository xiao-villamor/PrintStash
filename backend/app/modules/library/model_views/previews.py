"""One selected mesh Artifact, with batched lifecycle and publication reads."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    Model,
)
from app.db.scopes import live
from app.modules.derivatives.kinds import MESH_TYPES, PREVIEWS_RECIPE
from app.modules.media.preview_contracts import PreviewManifest
from app.schemas.previews import (
    REASONED_STATES,
    ArtifactPreviews,
    ModelPreviewRead,
    NoPreviewSubject,
    PreviewImageRead,
    PreviewManifestRead,
    PreviewState,
    PublishedPreviewRead,
)


def generation(row: ArtifactDerivative) -> str:
    return hashlib.sha256(row.output_json.encode()).hexdigest()


def published(row: ArtifactDerivative, sha256: str) -> PreviewManifest | None:
    if row.storage_key is None:
        return None
    # Only the producer writes this after validating it; a malformed row is a bug.
    manifest = PreviewManifest.model_validate_json(row.output_json)
    # A linked Library rescan may replace the Artifact's bytes in place; views
    # rendered from the previous content must not be advertised for the new.
    return manifest if manifest.source_sha256 == sha256 else None


def selected_files(session: Session, model_ids: list[int]) -> dict[int, File]:
    ranked = (
        select(
            col(File.id).label("id"),
            func.row_number()
            .over(
                partition_by=col(File.model_id),
                order_by=(col(File.version).desc(), col(File.id).desc()),
            )
            .label("position"),
        )
        .where(
            col(File.model_id).in_(model_ids),
            col(File.file_type).in_(MESH_TYPES),
            live(File),
        )
        .subquery()
    )
    return {
        file.model_id: file
        for file in session.exec(
            select(File)
            .join(ranked, ranked.c.id == col(File.id))
            .where(ranked.c.position == 1)
        ).all()
    }


def summaries(
    session: Session, model_ids: list[int], *, root: str | None = None
) -> dict[int, ModelPreviewRead]:
    """Batch preview state; *root* addresses one shared Model's delivery routes."""
    if root is not None and len(model_ids) != 1:
        raise ValueError("shared_preview_root_needs_one_model")
    if not model_ids:
        return {}
    files = selected_files(session, model_ids)
    by_file: dict[int, list[ArtifactDerivative]] = defaultdict(list)
    if files:
        for row in session.exec(
            select(ArtifactDerivative)
            .where(
                col(ArtifactDerivative.file_id).in_(
                    [file.id for file in files.values()]
                ),
                col(ArtifactDerivative.kind) == DerivativeKind.PREVIEWS,
            )
            .order_by(col(ArtifactDerivative.recipe_version).desc())
        ).all():
            by_file[row.file_id].append(row)
    result: dict[int, ModelPreviewRead] = {}
    for model_id in model_ids:
        file = files.get(model_id)
        if file is None:
            result[model_id] = NoPreviewSubject()
            continue
        assert file.id is not None
        rows = by_file[file.id]
        current = next(
            (row for row in rows if row.recipe_version == PREVIEWS_RECIPE), None
        )
        release = None
        for row in rows:
            manifest = published(row, file.sha256)
            if manifest is None:
                continue
            base = (
                root if root is not None else f"/api/v1/models/{model_id}"
            ) + f"/previews/{generation(row)}"
            release = PublishedPreviewRead(
                profile=manifest.profile,
                manifest_url=base,
                views=[
                    PreviewImageRead(
                        id=view.id, role=view.role, url=f"{base}/{view.id}"
                    )
                    for view in manifest.views
                ],
            )
            break
        if current is None:
            state = PreviewState.PENDING
        elif current.state is DerivativeState.SKIPPED:
            state = PreviewState.UNSUPPORTED
        elif (
            current.state is DerivativeState.READY
            and published(current, file.sha256) is None
        ):
            # Ready for bytes this Artifact no longer holds: re-derivation is owed.
            state = PreviewState.PENDING
        else:
            state = PreviewState(current.state.value)
        result[model_id] = ArtifactPreviews(
            file_id=file.id,
            state=state,
            reason=current.failure_reason
            if current is not None and state in REASONED_STATES
            else None,
            published=release,
        )
    return result


def resolve(
    session: Session, model_id: int, version: str
) -> tuple[ArtifactDerivative, PreviewManifestRead]:
    model = session.exec(
        select(Model).where(col(Model.id) == model_id, live(Model))
    ).first()
    file = (
        selected_files(session, [model_id]).get(model_id) if model is not None else None
    )
    if file is not None:
        assert file.id is not None
        for row in session.exec(
            select(ArtifactDerivative).where(
                col(ArtifactDerivative.file_id) == file.id,
                col(ArtifactDerivative.kind) == DerivativeKind.PREVIEWS,
            )
        ).all():
            manifest = published(row, file.sha256)
            if manifest is not None and generation(row) == version:
                return row, PreviewManifestRead(
                    **manifest.model_dump(), file_id=file.id, generation=version
                )
    raise OperationError("preview_not_found", kind=ErrorKind.NOT_FOUND)
