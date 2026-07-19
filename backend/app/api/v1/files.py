"""File download + thumbnail + on-the-fly STL conversion."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
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
from app.services.jobs import registry
from app.services.storage_backend import get_backend

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
    rbac.require_model_collection_role(
        session,
        user,
        session.get(Model, f.model_id).collection_id,
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
    if not get_backend().exists(f.path):
        raise HTTPException(status_code=410, detail="file_blob_missing")
    return _serve_file(f.path, f.original_filename)


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
    backend = get_backend()
    url = backend.presigned_download_url(f.path, f.original_filename)
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
    backend = get_backend()
    url = backend.presigned_download_url(f.path, f.original_filename)
    if url:
        return RedirectResponse(url=url, status_code=307)
    return download_file(file_id=file_id, current_user=current_user, session=session)


@router.get(
    "/{file_id}/thumbnail",
    summary="Get the thumbnail extracted from the file (if any)",
)
def file_thumbnail(
    file_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    _accessible_file(session, file_id, current_user)
    return thumbnail_response(file_id)


def thumbnail_response(file_id: int):
    """Serve a file's thumbnail. No access checks — authorise the caller first."""
    backend = get_backend()
    thumb_key = backend.thumbnail_key(file_id)
    filename, media_type = f"{file_id}.webp", "image/webp"
    if not backend.exists(thumb_key):
        # Thumbnails written before the WebP switch are still PNG on disk.
        thumb_key = backend.legacy_thumbnail_key(file_id)
        filename, media_type = f"{file_id}.png", "image/png"
        if not backend.exists(thumb_key):
            raise HTTPException(status_code=404, detail="thumbnail_not_found")
    # Thumbnails only change on explicit rebuilds; let the browser cache them
    # so the library grid doesn't re-request every image on each visit.
    return _serve_file(
        thumb_key,
        filename,
        media_type,
        headers={"Cache-Control": "public, max-age=3600"},
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
    backend = get_backend()
    if not backend.exists(f.path):
        raise HTTPException(status_code=410, detail="file_blob_missing")

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
        return _serve_file(
            f.path,
            f"{stem}.stl",
            media_type="application/sla",
            headers=cache_headers,
        )

    # 3MF/OBJ: trimesh conversion is expensive, so cache the result keyed by the
    # source sha256 and serve the cached STL on every subsequent request.
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

    with backend.local_path(f.path) as path:
        data = mesh_processing.to_stl_bytes(path)
    if data is None:
        raise HTTPException(status_code=500, detail="stl_conversion_failed")

    try:
        backend.write_bytes(data, cache_key)
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
    from app.services.thumbnail_repair import regenerate_model_thumbnail

    registry.update(job_id, state="running", label="scanning_models")
    try:
        with session_factory.scoped_session() as session:
            stmt = select(Model).where(live(Model))
            if not force:
                stmt = stmt.where(Model.thumbnail_file_id.is_(None))  # type: ignore[union-attr]
            models = session.exec(stmt).all()

            rebuilt: list[int] = []
            skipped: list[int] = []
            failed: list[int] = []

            for index, m in enumerate(models):
                assert m.id is not None
                registry.update(
                    job_id,
                    step=index + 1,
                    total_steps=len(models),
                    label=f"rendering model {m.id}",
                    progress=index / len(models) * 100,
                )
                mesh_file = session.exec(
                    select(File)
                    .where(File.model_id == m.id, File.file_type.in_(_MESH_TYPES))  # type: ignore[attr-defined]
                    .order_by(File.version.desc())  # type: ignore[attr-defined]
                ).first()
                if mesh_file is None:
                    skipped.append(m.id)
                    continue

                try:
                    regenerated = regenerate_model_thumbnail(session, m.id)
                except Exception:  # noqa: BLE001 — defensive, log and continue
                    logger.exception(
                        "rebuild: thumbnail regeneration crashed for model %s", m.id
                    )
                    regenerated = False

                if not regenerated:
                    failed.append(m.id)
                    continue
                rebuilt.append(m.id)
                logger.info(
                    "rebuild: thumbnail regenerated for model %s file %s",
                    m.id,
                    mesh_file.id,
                )

            registry.update(
                job_id,
                state="completed",
                result={
                    "scanned": len(models),
                    "rebuilt": rebuilt,
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
    job_id = registry.create(owner_user_id=current_user.id)
    background_tasks.add_task(_run_thumbnail_rebuild, job_id, force, session_factory)
    return IngestResponse(
        job_id=job_id, state="pending", message="thumbnail rebuild queued"
    )
