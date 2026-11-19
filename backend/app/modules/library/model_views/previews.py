"""One selected mesh Artifact, with batched lifecycle and publication reads."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import and_, func
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    FileType,
    JobKind,
    Model,
    ModelCover,
)
from app.db.scopes import live
from app.modules.derivatives import policy
from app.modules.derivatives.kinds import MESH_TYPES, PREVIEWS_RECIPE
from app.modules.derivatives.sliced_reads import meshless_archives
from app.modules.media.mesh_contracts import ThumbnailStrategy
from app.modules.media.preview_contracts import (
    PreviewManifest,
    ViewId,
    ViewProfile,
    ViewRole,
)
from app.modules.media.sliced_gcode import is_sliced_filename
from app.schemas.previews import (
    REASONED_STATES,
    ArchiveImageRead,
    ArtifactPreviews,
    ModelPreviewRead,
    NoPreviewSubject,
    PreviewImageKind,
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


def selected_files(
    session: Session, model_ids: list[int], *, meshless_ids: tuple[int, ...] = ()
) -> dict[int, File]:
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
            col(File.id).not_in(meshless_ids),
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


@dataclass(frozen=True)
class CoverInfo:
    digest: str | None
    automatic_file: int | None
    custom_filename: str | None
    image_kind: PreviewImageKind | None


def _cover_info(session: Session, model_ids: list[int]) -> dict[int, CoverInfo]:
    """Resolve the chosen cover or its published thumbnail and media kind."""
    rows = session.exec(
        select(
            Model.id,
            Model.cover_filename,
            Model.thumbnail_file_id,
            Model.thumbnail_path,
            ModelCover.sha256,
            ArtifactDerivative.output_json,
            File.file_type,
            File.original_filename,
        )
        .select_from(Model)
        .outerjoin(
            ModelCover,
            and_(
                col(ModelCover.model_id) == col(Model.id),
                col(ModelCover.filename) == col(Model.cover_filename),
            ),
        )
        .outerjoin(
            ArtifactDerivative,
            and_(
                col(ArtifactDerivative.file_id) == col(Model.thumbnail_file_id),
                col(ArtifactDerivative.storage_key) == col(Model.thumbnail_path),
                col(ArtifactDerivative.kind) == DerivativeKind.THUMBNAIL,
                col(ArtifactDerivative.state) == DerivativeState.READY,
            ),
        )
        .outerjoin(File, col(File.id) == col(ArtifactDerivative.file_id))
        .where(col(Model.id).in_(model_ids))
    ).all()
    info = {}
    for (
        model_id,
        cover_filename,
        thumbnail_file_id,
        thumbnail_path,
        cover_digest,
        thumbnail,
        file_type,
        filename,
    ) in rows:
        if model_id is None:
            continue
        metadata = json.loads(thumbnail) if thumbnail else {}
        strategy = metadata.get("strategy")
        embedded_3mf = strategy == ThumbnailStrategy.EMBEDDED and (
            file_type is FileType.THREE_MF
            or (
                file_type is FileType.GCODE
                and filename is not None
                and is_sliced_filename(filename)
            )
        )
        if cover_filename is not None or embedded_3mf:
            kind = PreviewImageKind.PHOTO
        elif thumbnail_file_id or thumbnail_path:
            kind = PreviewImageKind.RENDER
        else:
            kind = None
        info[model_id] = CoverInfo(
            digest=cover_digest if cover_filename else metadata.get("sha256"),
            automatic_file=thumbnail_file_id
            if not cover_filename and strategy == ThumbnailStrategy.FULL
            else None,
            custom_filename=cover_filename,
            image_kind=kind,
        )
    return info


def summaries(
    session: Session, model_ids: list[int], *, root: str | None = None
) -> dict[int, ModelPreviewRead]:
    """Batch preview state; *root* addresses one shared Model's delivery routes."""
    if root is not None and len(model_ids) != 1:
        raise ValueError("shared_preview_root_needs_one_model")
    if not model_ids:
        return {}
    archive_files = meshless_archives(session, model_ids)
    files = selected_files(session, model_ids, meshless_ids=tuple(archive_files))
    enabled = not files or policy.resolve(session)[JobKind.DERIVATIVES_PREVIEWS].enabled
    include_poles = policy.preview_poles(session) if files else False
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
    cover_info = _cover_info(session, model_ids)
    archive_by_model: dict[int, list[ArchiveImageRead]] = {}
    if root is None:
        for file_id, (model_id, sha256, images) in archive_files.items():
            archive_by_model.setdefault(
                model_id,
                [
                    ArchiveImageRead(
                        file_id=file_id,
                        url=f"/api/v1/files/{file_id}/archive-images/{index}?v={sha256}&framing=photo",
                        plate=image.plate,
                    )
                    for index, image in enumerate(images)
                ],
            )
    result: dict[int, ModelPreviewRead] = {}
    for model_id in model_ids:
        file = files.get(model_id)
        cover = cover_info.get(model_id)
        digest = cover.digest if cover is not None else None
        automatic_file = cover.automatic_file if cover is not None else None
        custom_filename = cover.custom_filename if cover is not None else None
        cover_kind = cover.image_kind if cover is not None else None
        archive_images = archive_by_model.get(model_id, [])
        custom_url = (
            f"/api/v1/models/{model_id}/cover/content?v={custom_filename}"
            if custom_filename is not None and root is None
            else None
        )
        if file is None:
            photo_url = custom_url or (
                archive_images[0].url
                if archive_images and custom_filename is None
                else None
            )
            result[model_id] = NoPreviewSubject(
                cover_image_kind=cover_kind,
                large_cover_image_kind=PreviewImageKind.PHOTO if photo_url else None,
                archive_images=archive_images,
                large_cover_url=photo_url,
            )
            continue
        assert file.id is not None
        rows = by_file[file.id]
        current = next(
            (row for row in rows if row.recipe_version == PREVIEWS_RECIPE), None
        )
        release = None
        large_cover_url = custom_url
        large_cover_view = None
        large_cover_thumbnail_url = None
        if custom_filename is None:
            candidates = (
                (row, view)
                for row in rows
                if (manifest := published(row, file.sha256)) is not None
                for view in manifest.views
                if view.sha256 == digest
                or (file.id == automatic_file and view.id == manifest.cover_view)
                or (
                    file.id == automatic_file
                    and manifest.profile is ViewProfile.PLANAR
                    and view.role is ViewRole.FACE
                )
            )
            cover = max(
                candidates,
                default=None,
                key=lambda item: (item[1].width, item[1].role is ViewRole.FACE),
            )
            if cover is not None:
                row, view = cover
                base = root if root is not None else f"/api/v1/models/{model_id}"
                large_cover_url = f"{base}/previews/{generation(row)}/{view.id}"
                large_cover_view = (
                    ViewId.TOP
                    if view.role is ViewRole.FACE and view.rotation[2][2] < -0.999
                    else view.id
                )
                if view.thumbnail is not None:
                    large_cover_thumbnail_url = f"{large_cover_url}?size=thumbnail"
        for row in rows:
            manifest = published(row, file.sha256)
            if not enabled or manifest is None or manifest.profile != ViewProfile.ORBIT:
                continue
            base = (
                root if root is not None else f"/api/v1/models/{model_id}"
            ) + f"/previews/{generation(row)}"
            visible_views = [
                view
                for view in manifest.views
                if (include_poles or view.role not in (ViewRole.TOP, ViewRole.BOTTOM))
                and not (
                    view.id == manifest.cover_view
                    and view.role is ViewRole.FACE
                    and f"{base}/{view.id}" != large_cover_url
                )
            ]
            release = PublishedPreviewRead(
                profile=manifest.profile,
                manifest_url=base,
                cover_view=next(
                    (view.id for view in visible_views if view.sha256 == digest),
                    None,
                ),
                views=[
                    PreviewImageRead(
                        id=view.id,
                        role=view.role,
                        url=f"{base}/{view.id}",
                        thumbnail_url=f"{base}/{view.id}?size=thumbnail"
                        if view.thumbnail is not None
                        else None,
                    )
                    for view in visible_views
                ],
            )
            break
        if not enabled:
            state = PreviewState.DISABLED
        elif current is None:
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
            cover_image_kind=cover_kind,
            large_cover_image_kind=(
                PreviewImageKind.PHOTO
                if custom_url
                else PreviewImageKind.RENDER
                if large_cover_url
                else None
            ),
            large_cover_url=large_cover_url,
            large_cover_view=large_cover_view,
            large_cover_thumbnail_url=large_cover_thumbnail_url,
            archive_images=archive_images,
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
        selected_files(
            session,
            [model_id],
            meshless_ids=tuple(meshless_archives(session, [model_id])),
        ).get(model_id)
        if model is not None
        else None
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
