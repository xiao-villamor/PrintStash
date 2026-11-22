"""One selected mesh Artifact, with batched lifecycle and publication reads."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import Integer, String, and_, column, func, values
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
    ModelProvenanceSource,
    ModelSourceCover,
)
from app.db.scopes import live
from app.modules.derivatives import policy
from app.modules.derivatives.kinds import MESH_TYPES, PREVIEWS_RECIPE
from app.modules.derivatives.sliced_reads import meshless_archive_rows
from app.modules.media.mesh_contracts import ThumbnailStrategy
from app.modules.media.preview_contracts import (
    PreviewImageKind,
    PreviewManifest,
    ViewId,
    ViewProfile,
    ViewRole,
)
from app.modules.media.sliced_archive import ArchiveImage
from app.modules.media.sliced_gcode import is_sliced_filename
from app.schemas.previews import (
    REASONED_STATES,
    ArchiveImageRead,
    ArtifactPreviews,
    ModelPreviewRead,
    NoPreviewSubject,
    PreviewImageRead,
    PreviewManifestRead,
    PreviewState,
    PublishedPreviewRead,
    SourceImageRead,
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


def _ranked_files(model_ids: list[int], meshless_ids):
    return (
        select(
            col(File.id).label("id"),
            col(File.model_id).label("model_id"),
            col(File.sha256).label("sha256"),
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


@dataclass(frozen=True)
class PreviewFile:
    id: int
    sha256: str


@dataclass(frozen=True)
class CoverInfo:
    digest: str | None
    automatic_file: int | None
    custom_filename: str | None
    image_kind: PreviewImageKind | None


def _preview_sources(session: Session, models: list[Model]):
    """Read selected mesh, meshless archive and cover data in one bounded query."""
    requested = (
        values(
            column("model_id", Integer),
            column("cover_filename", String),
            column("thumbnail_file_id", Integer),
            column("thumbnail_path", String),
        )
        .data(
            [
                (
                    model.id,
                    model.cover_filename,
                    model.thumbnail_file_id,
                    model.thumbnail_path,
                )
                for model in models
            ]
        )
        .cte("requested_previews")
    )
    model_ids = [model.id for model in models if model.id is not None]
    archives = meshless_archive_rows(session, model_ids).cte("preview_archives")
    selected = _ranked_files(model_ids, select(archives.c.file_id))
    thumbnail = (
        select(col(ArtifactDerivative.id))
        .where(
            col(ArtifactDerivative.file_id) == requested.c.thumbnail_file_id,
            col(ArtifactDerivative.storage_key) == requested.c.thumbnail_path,
            col(ArtifactDerivative.kind) == DerivativeKind.THUMBNAIL,
            col(ArtifactDerivative.state) == DerivativeState.READY,
        )
        .order_by(col(ArtifactDerivative.recipe_version).desc())
        .limit(1)
        .correlate(requested)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            requested,
            col(ModelCover.sha256).label("cover_digest"),
            col(ArtifactDerivative.output_json).label("thumbnail_output"),
            File.file_type,
            File.original_filename,
            selected.c.id.label("mesh_id"),
            selected.c.sha256.label("mesh_sha256"),
            archives.c.file_id.label("archive_id"),
            archives.c.sha256.label("archive_sha256"),
            archives.c.output.label("archive_output"),
        )
        .select_from(requested)
        .outerjoin(
            ModelCover,
            and_(
                col(ModelCover.model_id) == requested.c.model_id,
                col(ModelCover.filename) == requested.c.cover_filename,
            ),
        )
        .outerjoin(ArtifactDerivative, col(ArtifactDerivative.id) == thumbnail)
        .outerjoin(File, col(File.id) == col(ArtifactDerivative.file_id))
        .outerjoin(
            selected,
            and_(selected.c.model_id == requested.c.model_id, selected.c.position == 1),
        )
        .outerjoin(archives, archives.c.model_id == requested.c.model_id)
        .order_by(archives.c.file_version.desc(), archives.c.recipe_version.desc())
    )
    files: dict[int, PreviewFile] = {}
    archive_files: dict[int, tuple[int, str, tuple[ArchiveImage, ...]]] = {}
    covers = {}
    for row in rows:
        if row.mesh_id is not None:
            files[row.model_id] = PreviewFile(row.mesh_id, row.mesh_sha256)
        if row.archive_id is not None and row.archive_id not in archive_files:
            data = json.loads(row.archive_output)
            images = tuple(ArchiveImage(**image) for image in data["archive_images"])
            archive_files[row.archive_id] = (row.model_id, row.archive_sha256, images)
        if row.model_id not in covers:
            covers[row.model_id] = (
                row.cover_filename,
                row.thumbnail_file_id,
                row.thumbnail_path,
                row.cover_digest,
                row.thumbnail_output,
                row.file_type,
                row.original_filename,
            )
    return files, archive_files, _cover_info(covers, archive_files)


def _cover_info(covers, archives) -> dict[int, CoverInfo]:
    info = {}
    for model_id, (
        cover_filename,
        thumbnail_file_id,
        thumbnail_path,
        cover_digest,
        thumbnail,
        file_type,
        filename,
    ) in covers.items():
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
        if cover_filename is not None:
            kind = PreviewImageKind.PHOTO
        elif embedded_3mf:
            image_kind = metadata.get("image_kind")
            if image_kind is not None:
                kind = PreviewImageKind(image_kind)
            else:
                # Older publications chose the first meshless archive image
                # when one existed; ordinary embedded thumbnails are renders.
                archive = (
                    archives.get(thumbnail_file_id)
                    if thumbnail_file_id is not None
                    else None
                )
                images = archive[2] if archive is not None else ()
                kind = (
                    images[0].image_kind if images else PreviewImageKind.SLICER_RENDER
                )
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


def _archive_image_url(
    file_id: int, index: int, sha256: str, image: ArchiveImage
) -> str:
    version = hashlib.sha256(f"{sha256}:{image.member}".encode()).hexdigest()
    url = f"/api/v1/files/{file_id}/archive-images/{index}?v={version}"
    if image.image_kind is PreviewImageKind.PHOTO:
        url += "&framing=photo"
    return url


def summaries(
    session: Session, models: list[Model], *, root: str | None = None
) -> dict[int, ModelPreviewRead]:
    """Batch preview state; *root* addresses one shared Model's delivery routes."""
    model_ids = [model.id for model in models if model.id is not None]
    if root is not None and len(model_ids) != 1:
        raise ValueError("shared_preview_root_needs_one_model")
    if not model_ids:
        return {}
    source_images: dict[int, list[SourceImageRead]] = defaultdict(list)
    if root is None:
        for source, image in session.exec(
            select(ModelProvenanceSource, ModelSourceCover)
            .join(
                ModelSourceCover,
                ModelSourceCover.provenance_source_id == ModelProvenanceSource.id,
            )
            .where(col(ModelProvenanceSource.model_id).in_(model_ids))
            .order_by(ModelProvenanceSource.id, ModelSourceCover.position)
        ).all():
            version = hashlib.sha256(
                f"{image.storage_key}:{image.updated_at.isoformat()}".encode()
            ).hexdigest()[:16]
            source_images[source.model_id].append(
                SourceImageRead(
                    id=f"source-{image.id}",
                    source_id=source.id,
                    position=image.position,
                    provider=source.provider,
                    url=f"/api/v1/models/{source.model_id}/provenance/{source.id}/cover/content?position={image.position}&v={version}",
                )
            )
    files, archive_files, cover_info = _preview_sources(session, models)
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
    archive_by_model: dict[int, list[ArchiveImageRead]] = {}
    if root is None:
        for file_id, (model_id, sha256, images) in archive_files.items():
            archive_by_model.setdefault(
                model_id,
                [
                    ArchiveImageRead(
                        file_id=file_id,
                        url=_archive_image_url(file_id, index, sha256, image),
                        plate=image.plate,
                        image_kind=image.image_kind,
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
            archive_cover = (
                archive_images[0]
                if archive_images and custom_filename is None
                else None
            )
            result[model_id] = NoPreviewSubject(
                cover_image_kind=cover_kind,
                large_cover_image_kind=(
                    PreviewImageKind.PHOTO
                    if custom_url
                    else archive_cover.image_kind
                    if archive_cover is not None
                    else None
                ),
                archive_images=archive_images,
                source_images=source_images[model_id],
                large_cover_url=custom_url
                or (archive_cover.url if archive_cover is not None else None),
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
            source_images=source_images[model_id],
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
        _preview_sources(session, [model])[0].get(model_id)
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
