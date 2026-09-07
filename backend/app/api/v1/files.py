"""File download + thumbnail + on-the-fly STL conversion."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import (
    FileResponse,
    PlainTextResponse,
    StreamingResponse,
)
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.artifact_responses import delivery_request, render_delivery
from app.core.config import settings
from app.core.http import get_or_404
from app.core.logging import get_logger
from app.core.security import get_current_user, require_superuser, require_user
from app.db.models import CollectionRole, File, FileType, Model, User
from app.db.scopes import live
from app.db.session import SessionFactory, get_session, get_session_factory
from app.modules.identity import auth, rbac
from app.modules.media.three_mf_preview import (
    EmbeddedGcodeError,
    read_embedded_gcode_path,
)
from app.modules.storage.artifact_content import (
    ArtifactContentError,
    ArtifactContentMissingError,
    resolve,
)
from app.modules.storage.artifact_delivery import (
    DeliveryPurpose,
    bytes_response_headers,
    plan_artifact,
    plan_stored_representation,
)
from app.modules.storage.delivery_contracts import content_disposition
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import publish_bytes
from app.runtime.jobs import registry
from app.schemas.ingest import IngestResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

_MESH_TYPES = {FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP}


def _live_file(session: Session, file_id: int) -> File:
    """Load a live file (its model and the file itself not deleted).

    No access control — callers must authorise via a user (``_accessible_file``)
    or a bearer capability such as a slicer download token.
    """
    f = get_or_404(session, File, file_id, "file_not_found")
    model = session.get(Model, f.model_id)
    if model is None or model.deleted_at is not None or f.deleted_at is not None:
        raise HTTPException(status_code=404, detail="file_not_found")
    return f


def _accessible_file(session: Session, file_id: int, user: User) -> File:
    f = _live_file(session, file_id)
    model = session.get(Model, f.model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="file_not_found")
    rbac.require_model_collection_role(
        session,
        user,
        model.collection_id,
        CollectionRole.VIEW,
    )
    return f


def _serve_file(
    key: str,
    filename: str,
    media_type: str = "application/octet-stream",
    *,
    headers: dict[str, str] | None = None,
):
    backend = get_backend()
    direct = backend.direct_path(key)
    if direct is not None:
        if not direct.exists():
            raise HTTPException(status_code=410, detail="file_blob_missing")
        return FileResponse(
            path=str(direct), filename=filename, media_type=media_type, headers=headers
        )
    chunks = backend.stream_chunks(key)
    return StreamingResponse(
        chunks,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **(headers or {}),
        },
    )


def serve_artifact(
    artifact: File,
    request: Request | None,
    filename: str,
    media_type: str = "application/octet-stream",
    purpose: DeliveryPurpose = DeliveryPurpose.DOWNLOAD,
):
    """Render an original after the caller has authorized its exact Artifact."""
    try:
        return render_delivery(
            plan_artifact(
                artifact, delivery_request(request, filename, media_type, purpose)
            )
        )
    except (ArtifactContentMissingError, FileNotFoundError) as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc


def _serve_download(
    session: Session,
    file_id: int,
    slicer_token: str | None,
    current_user: User | None,
    request: Request,
):
    # A logged-in user goes through the normal RBAC check. Otherwise the request
    # must carry a valid slicer download token — a short-lived bearer capability
    # for this one file, used by "Open in slicer" so an external slicer process
    # (which has no login session) can fetch the file. See `slicer_download_url`.
    if current_user is not None:
        f = _accessible_file(session, file_id, current_user)
    elif slicer_token and auth.verify_file_download_token(slicer_token, file_id):
        f = _live_file(session, file_id)
    else:
        raise HTTPException(status_code=401, detail="not_authenticated")
    purpose = (
        DeliveryPurpose.DOWNLOAD if current_user is not None else DeliveryPurpose.SLICER
    )
    return serve_artifact(f, request, f.original_filename, purpose=purpose)


@router.get(
    "/{file_id}/download",
    summary="Download the raw file blob",
    description="Streams the underlying artifact (G-code/STL/3MF/OBJ) from storage.",
)
def download_file(
    file_id: int,
    request: Request,
    slicer_token: str | None = None,
    current_user: User | None = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _serve_download(session, file_id, slicer_token, current_user, request)


@router.get(
    "/{file_id}/embedded-gcode",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Embedded G-code text",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        404: {"description": "Artifact is inaccessible or preview is unavailable"},
        410: {"description": "Artifact blob is missing from storage"},
        413: {"description": "Archive or embedded toolpath exceeds configured limits"},
        429: {"description": "Preview concurrency capacity is exhausted"},
    },
    summary="Serve a Bambu 3MF's embedded G-code preview",
    description=(
        "Reads Metadata/plate_<N>.gcode on demand from an authorized 3MF. "
        "The selected member is bounded and is never extracted or persisted."
    ),
)
def embedded_gcode(
    file_id: int,
    request: Request,
    plate_index: int | None = Query(default=None, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    artifact = _accessible_file(session, file_id, current_user)
    if artifact.file_type != FileType.THREE_MF:
        raise HTTPException(status_code=404, detail="embedded_gcode_not_supported")
    archive_cap = settings.three_mf_preview_max_archive_mb * 1024 * 1024
    if artifact.size_bytes > archive_cap:
        raise HTTPException(status_code=413, detail="embedded_gcode_archive_too_large")
    try:
        with resolve(artifact).materialize() as path:
            embedded = read_embedded_gcode_path(path, plate_index=plate_index)
    except EmbeddedGcodeError as exc:
        status_code = (
            413
            if exc.code
            in {
                "embedded_gcode_too_large",
                "embedded_gcode_bomb",
                "embedded_gcode_archive_too_large",
                "embedded_gcode_too_many_entries",
                "embedded_gcode_central_directory_too_large",
            }
            else 429
            if exc.code == "embedded_gcode_busy"
            else 404
        )
        raise HTTPException(status_code=status_code, detail=exc.code) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    except ArtifactContentError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    payload = embedded.content
    status_code, headers = bytes_response_headers(
        payload,
        delivery_request(
            request, embedded.filename, "text/plain", DeliveryPurpose.TRANSFORMED
        ),
    )
    headers["Content-Disposition"] = content_disposition(embedded.filename, inline=True)
    return PlainTextResponse(
        content=embedded.content if status_code == 200 else "",
        status_code=status_code,
        headers=headers,
    )


@router.get(
    "/{file_id}/slicer/{slicer_token}/{filename}",
    summary="Login-free download for opening in a slicer (token in path)",
    description=(
        "Streams the file blob for 'Open in slicer'. The token lives in the path "
        "and the original filename is the LAST path segment, so the URL ends in "
        "the file's extension. Slicers (OrcaSlicer, Bambu Studio, …) take the URL "
        "tail as the download name — if the extension isn't last (e.g. a "
        "?token=… query trailing it) they save the blob but won't open it. The "
        "filename is cosmetic; access is governed solely by the token."
    ),
)
def slicer_download(
    file_id: int,
    request: Request,
    slicer_token: str,
    filename: str,
    session: Session = Depends(get_session),
):
    return _serve_download(
        session, file_id, slicer_token, current_user=None, request=request
    )


@router.get(
    "/{file_id}/slicer-url",
    summary="Signed, login-free download URL for opening in a slicer",
    description=(
        "Returns a short-lived download URL the slicer (OrcaSlicer, Bambu "
        "Studio, …) can fetch without the user's login session. The token is in "
        "the path and the original filename is last, so the URL ends in the "
        "file's extension and the slicer detects the format."
    ),
)
def slicer_download_url(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    f = _accessible_file(session, file_id, current_user)
    token = auth.create_file_download_token(file_id)
    name = quote(f.original_filename, safe="")
    return {"url": f"/api/v1/files/{file_id}/slicer/{token}/{name}"}


@router.get(
    "/{file_id}/thumbnail",
    summary="Get the thumbnail extracted from the file (if any)",
)
def file_thumbnail(
    file_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    file_row = _accessible_file(session, file_id, current_user)
    return thumbnail_response(file_id, request, thumbnail_path=file_row.thumbnail_path)


def thumbnail_response(
    file_id: int,
    request: Request | None = None,
    *,
    thumbnail_path: str | None = None,
    purpose: DeliveryPurpose = DeliveryPurpose.THUMBNAIL,
):
    """Serve a file's thumbnail. No access checks — authorise the caller first."""
    backend = get_backend()
    thumb_key = thumbnail_path or backend.thumbnail_key(file_id)
    filename, media_type = f"{file_id}.webp", "image/webp"
    info = backend.object_info(thumb_key)
    if info is None and thumbnail_path is not None:
        thumb_key = backend.thumbnail_key(file_id)
        info = backend.object_info(thumb_key)
    if info is None:
        # Thumbnails written before the WebP switch are still PNG on disk.
        thumb_key = backend.legacy_thumbnail_key(file_id)
        filename, media_type = f"{file_id}.png", "image/png"
        info = backend.object_info(thumb_key)
        if info is None:
            raise HTTPException(status_code=404, detail="thumbnail_not_found")
    return render_delivery(
        plan_stored_representation(
            backend,
            thumb_key,
            delivery_request(request, filename, media_type, purpose),
            info=info,
        )
    )


@router.get(
    "/{file_id}/stl",
    summary="Serve any mesh file as STL for 3D preview",
    description=(
        "If the file is already STL it is served directly. 3MF and OBJ files are "
        "converted to binary STL on the fly via trimesh."
    ),
)
def file_as_stl(
    file_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    f = _accessible_file(session, file_id, current_user)
    return stl_response(f, request)


def stl_response(
    f: File, request: Request, purpose: DeliveryPurpose = DeliveryPurpose.TRANSFORMED
):
    """Serve a mesh File as binary STL (cached). No access checks — callers
    are responsible for authorising access to *f* first."""
    stem = Path(f.original_filename).stem
    delivery = delivery_request(request, f"{stem}.stl", "application/sla", purpose)
    if Path(f.original_filename).suffix.lower() == ".stl":
        return serve_artifact(
            f,
            request,
            f"{stem}.stl",
            "application/sla",
            DeliveryPurpose.BROWSER_FETCH
            if purpose == DeliveryPurpose.TRANSFORMED
            else purpose,
        )

    backend = get_backend()
    cache_key = backend.stl_cache_key(f.sha256)
    if backend.exists(cache_key):
        return render_delivery(plan_stored_representation(backend, cache_key, delivery))

    # Lazy import: trimesh is heavy; pull it in only when we must convert.
    from app.modules.media import mesh_processing

    try:
        with resolve(f).materialize() as path:
            data = mesh_processing.to_stl_bytes(path)
    except ArtifactContentMissingError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    if data is None:
        raise HTTPException(status_code=500, detail="stl_conversion_failed")

    cached = False
    try:
        with get_session_factory().scoped_session() as ownership_session:
            publish_bytes(
                ownership_session,
                backend,
                cache_key,
                data,
                object_kind="derived_stl_cache",
            )
            ownership_session.commit()
            cached = True
    except StorageCollisionError:
        # Another request won the create-only race. Serve our in-memory result;
        # subsequent requests will use the already-published cache object.
        pass
    except Exception:
        logger.warning("stl cache write failed for file %s", f.id, exc_info=True)

    if cached:
        return render_delivery(plan_stored_representation(backend, cache_key, delivery))
    status_code, headers = bytes_response_headers(data, delivery)
    return Response(
        content=data if status_code == 200 else b"",
        status_code=status_code,
        media_type="application/sla",
        headers=headers,
    )


def _run_thumbnail_rebuild(
    job_id: str, force: bool, session_factory: SessionFactory
) -> None:
    """Walk models and re-render thumbnails. Runs as a background task."""
    from app.modules.media.thumbnail_generations import ThumbnailEnsureOutcome
    from app.modules.media.thumbnail_repair import regenerate_model_thumbnail_result

    registry.update(job_id, state="running", label="scanning_models")
    try:
        with session_factory.scoped_session() as session:
            stmt = select(Model).where(live(Model))
            if not force:
                stmt = stmt.where(Model.thumbnail_file_id.is_(None))  # type: ignore[union-attr]
            total = session.exec(
                select(func.count()).select_from(stmt.subquery())
            ).one()
            registry.update(
                job_id,
                stage="thumbnailing",
                processed=0,
                total=total,
                total_steps=total,
            )

            rebuilt: list[int] = []
            cached: list[int] = []
            coalesced: list[int] = []
            negative_cached: list[int] = []
            skipped: list[int] = []
            failed: list[int] = []
            processed = 0
            after_id = 0
            while True:
                page_stmt = (
                    stmt.where(Model.id > after_id).order_by(Model.id).limit(100)
                )  # type: ignore[operator,union-attr]
                models = session.exec(page_stmt).all()
                if not models:
                    break
                for m in models:
                    assert m.id is not None
                    processed += 1
                    registry.update(
                        job_id,
                        step=processed,
                        total_steps=total,
                        label=f"rendering model {m.id}",
                        progress=(processed - 1) / max(total, 1) * 100,
                        stage="thumbnailing",
                        processed=processed - 1,
                        total=total,
                    )
                    mesh_file = session.exec(
                        select(File)
                        .where(
                            File.model_id == m.id,
                            File.file_type.in_(_MESH_TYPES),  # type: ignore[attr-defined]
                            live(File),
                        )
                        .order_by(File.version.desc())  # type: ignore[attr-defined]
                    ).first()
                    if mesh_file is None:
                        skipped.append(m.id)
                        continue

                    try:
                        result = regenerate_model_thumbnail_result(
                            session, m.id, force=force
                        )
                    except Exception:  # noqa: BLE001 — task boundary
                        logger.exception(
                            "rebuild: thumbnail regeneration crashed for model %s",
                            m.id,
                        )
                        failed.append(m.id)
                        continue

                    if result.outcome is ThumbnailEnsureOutcome.GENERATED:
                        rebuilt.append(m.id)
                    elif result.outcome is ThumbnailEnsureOutcome.CACHED:
                        cached.append(m.id)
                    elif result.outcome is ThumbnailEnsureOutcome.COALESCED:
                        coalesced.append(m.id)
                    elif result.outcome is ThumbnailEnsureOutcome.NEGATIVE_CACHED:
                        negative_cached.append(m.id)
                    else:
                        failed.append(m.id)
                after_id = models[-1].id or after_id

            registry.update(
                job_id,
                state="completed",
                stage="completed",
                processed=processed,
                total=total,
                succeeded=len(rebuilt) + len(cached),
                skipped=len(skipped),
                failed=len(failed),
                completion="partial" if failed else "complete",
                thumbnail_status=(
                    "failed"
                    if failed
                    else "generated"
                    if rebuilt or cached
                    else "skipped"
                ),
                thumbnail_reason=(
                    "renderer_no_output"
                    if failed
                    else "no_mesh"
                    if skipped and not rebuilt
                    else None
                ),
                result={
                    "scanned": processed,
                    "rebuilt": rebuilt,
                    "cache_hits": cached,
                    "coalesced": coalesced,
                    "negative_cached": negative_cached,
                    "skipped_no_mesh": skipped,
                    "failed_render": failed,
                },
            )
    except Exception as exc:  # noqa: BLE001 — top-level task boundary
        logger.exception("rebuild[%s] failed: %s", job_id, exc)
        registry.update(job_id, state="failed", error=str(exc))


@router.post(
    "/thumbnails/rebuild",
    response_model=IngestResponse,
    status_code=202,
    dependencies=[Depends(require_superuser)],
    summary="Regenerate mesh thumbnails for existing models",
    description=(
        "Walks non-soft-deleted models and tries to render a thumbnail from "
        "the newest mesh file (STL/3MF/OBJ). By default only missing "
        "thumbnails are rebuilt; pass force=true to refresh existing "
        "thumbnails after renderer improvements without re-uploading. "
        "Runs in the background: poll GET /ingest/jobs/{job_id}; the final "
        "per-model summary lands in the job's `result` field."
    ),
)
def rebuild_missing_thumbnails(
    background_tasks: BackgroundTasks,
    force: bool = False,
    current_user: User = Depends(require_superuser),
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    job_id = registry.create(owner_user_id=current_user.id, kind="thumbnail_rebuild")
    background_tasks.add_task(_run_thumbnail_rebuild, job_id, force, session_factory)
    return IngestResponse(
        job_id=job_id, state="pending", message="thumbnail rebuild queued"
    )


@router.get(
    "/{file_id}/toolpath", summary="Bounded ASCII toolpath from the original Artifact"
)
async def get_toolpath(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    from app.modules.media import toolpath

    file = _accessible_file(session, file_id, current_user)
    content = await toolpath.render(file)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Cache-Control": "private, no-store"},
    )
