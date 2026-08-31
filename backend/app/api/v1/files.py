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
    RedirectResponse,
    StreamingResponse,
)
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import settings
from app.core.http import get_or_404
from app.core.logging import get_logger
from app.core.security import get_current_user, require_superuser, require_user
from app.db.models import CollectionRole, File, FileType, Model, User
from app.db.scopes import live
from app.db.session import SessionFactory, get_session, get_session_factory
from app.schemas.ingest import IngestResponse
from app.services import auth, rbac
from app.services.artifact_content import (
    ArtifactContentError,
    ArtifactContentMissingError,
    presigned_download_url,
    resolve,
)
from app.services.jobs import registry
from app.services.storage_backend import StorageCollisionError, get_backend
from app.services.storage_ownership import publish_bytes
from app.services.three_mf_preview import EmbeddedGcodeError, read_embedded_gcode_path

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


def _serve_artifact(
    artifact: File,
    filename: str,
    media_type: str = "application/octet-stream",
    *,
    headers: dict[str, str] | None = None,
):
    handle = resolve(artifact)
    if handle.backend is not None:
        direct = handle.backend.direct_path(artifact.path)
        if direct is not None:
            if not direct.exists():
                raise HTTPException(status_code=410, detail="file_blob_missing")
            return FileResponse(
                path=str(direct),
                filename=filename,
                media_type=media_type,
                headers=headers,
            )
    try:
        chunks = handle.stream()
    except ArtifactContentMissingError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    return StreamingResponse(
        chunks,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **(headers or {}),
        },
    )


def _serve_download(
    session: Session,
    file_id: int,
    slicer_token: str | None,
    current_user: User | None,
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
    return _serve_artifact(f, f.original_filename)


@router.get(
    "/{file_id}/download",
    summary="Download the raw file blob",
    description="Streams the underlying artifact (G-code/STL/3MF/OBJ) from storage.",
)
def download_file(
    file_id: int,
    slicer_token: str | None = None,
    current_user: User | None = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _serve_download(session, file_id, slicer_token, current_user)


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
    return PlainTextResponse(
        content=embedded.content,
        headers={
            "Content-Disposition": f'inline; filename="{embedded.filename}"',
        },
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
    slicer_token: str,
    filename: str,
    session: Session = Depends(get_session),
):
    return _serve_download(session, file_id, slicer_token, current_user=None)


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
    "/{file_id}/download-url",
    summary="Get a pre-signed direct download URL (S3 only)",
    description=(
        "Returns a short-lived pre-signed URL when storage backend is S3. "
        "Falls back to API streaming URL for local storage."
    ),
)
def download_url(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    f = _accessible_file(session, file_id, current_user)
    url = presigned_download_url(f, f.original_filename)
    if url:
        return {
            "url": url,
            "backend": "s3",
            "expires_in": settings.s3_presigned_url_expire_seconds,
        }
    return {"url": f"/api/v1/files/{file_id}/download", "backend": "local"}


@router.get(
    "/{file_id}/download-direct",
    summary="Redirect to pre-signed URL when available",
)
def download_direct(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    f = _accessible_file(session, file_id, current_user)
    url = presigned_download_url(f, f.original_filename)
    if url:
        return RedirectResponse(url=url, status_code=307)
    return download_file(file_id=file_id, current_user=current_user, session=session)


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


def _etag_matches(request: Request | None, etag: str) -> bool:
    if request is None:
        return False
    candidates = request.headers.get("if-none-match", "").split(",")
    return any(
        candidate.strip().removeprefix("W/") in ("*", etag) for candidate in candidates
    )


def thumbnail_response(
    file_id: int,
    request: Request | None = None,
    *,
    thumbnail_path: str | None = None,
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
    # Thumbnails only change on explicit rebuilds; let the browser cache them
    # so the library grid doesn't re-request every image on each visit.
    # Revalidate cheaply so a newly-published immutable generation is visible
    # immediately instead of leaving cards stale for the previous one-hour TTL.
    headers = {"Cache-Control": "public, max-age=0, must-revalidate"}
    if info.etag:
        headers["ETag"] = info.etag
        if _etag_matches(request, info.etag):
            return Response(status_code=304, headers=headers)
    return _serve_file(
        thumb_key,
        filename,
        media_type,
        headers=headers,
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


def stl_response(f: File, request: Request):
    """Serve a mesh File as binary STL (cached). No access checks — callers
    are responsible for authorising access to *f* first."""
    stem = Path(f.original_filename).stem
    # File blobs are immutable (content-addressed by sha256), so the rendered
    # STL never changes (content-addressed), but keep the browser TTL modest;
    # the ETag still lets it revalidate cheaply after expiry.
    etag = f'"{f.sha256}"'
    # Content-Disposition is added per-response below: _serve_file derives it
    # from the filename, the in-memory Response sets it explicitly.
    cache_headers = {
        "Cache-Control": "public, max-age=3600",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)

    # Already STL: stream the blob straight through, no conversion — never read
    # a (potentially multi-GB) STL fully into memory just to serve it.
    if Path(f.original_filename).suffix.lower() == ".stl":
        return _serve_artifact(
            f,
            f"{stem}.stl",
            media_type="application/sla",
            headers=cache_headers,
        )

    # 3MF/OBJ: trimesh conversion is expensive, so cache the result keyed by the
    # source sha256 and serve the cached STL on every subsequent request.
    backend = get_backend()
    cache_key = backend.stl_cache_key(f.sha256)
    if backend.exists(cache_key):
        return _serve_file(
            cache_key,
            f"{stem}.stl",
            media_type="application/sla",
            headers=cache_headers,
        )

    # Lazy import: trimesh is heavy; pull it in only when we must convert.
    from app.services import mesh_processing

    try:
        with resolve(f).materialize() as path:
            data = mesh_processing.to_stl_bytes(path)
    except ArtifactContentMissingError as exc:
        raise HTTPException(status_code=410, detail="file_blob_missing") from exc
    if data is None:
        raise HTTPException(status_code=500, detail="stl_conversion_failed")

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
    except StorageCollisionError:
        # Another request won the create-only race. Serve our in-memory result;
        # subsequent requests will use the already-published cache object.
        pass
    except Exception:
        logger.warning("stl cache write failed for file %s", f.id, exc_info=True)

    # Freshly converted bytes are already in memory (and bounded by the render
    # cap), so serve them directly; subsequent requests hit the streamed cache.
    return Response(
        content=data,
        media_type="application/sla",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}.stl"',
            **cache_headers,
        },
    )


def _run_thumbnail_rebuild(
    job_id: str, force: bool, session_factory: SessionFactory
) -> None:
    """Walk models and re-render thumbnails. Runs as a background task."""
    from app.services.thumbnail_generations import ThumbnailEnsureOutcome
    from app.services.thumbnail_repair import regenerate_model_thumbnail_result

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
