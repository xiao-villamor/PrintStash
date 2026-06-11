"""File download + thumbnail + on-the-fly STL conversion."""

from __future__ import annotations

import io
from pathlib import Path

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
from app.core.security import require_auth
from app.db.models import File, FileType, Model
from app.db.scopes import live
from app.db.session import SessionFactory, get_session, get_session_factory
from app.schemas.ingest import IngestResponse
from app.services import thumbnail
from app.services.jobs import registry
from app.services.storage_backend import get_backend

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

_MESH_TYPES = {FileType.STL, FileType.THREE_MF, FileType.OBJ}


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


@router.get(
    "/{file_id}/download",
    summary="Download the raw file blob",
    description="Streams the underlying artifact (G-code/STL/3MF/OBJ) from storage.",
)
def download_file(file_id: int, session: Session = Depends(get_session)):
    f = get_or_404(session, File, file_id, "file_not_found")
    if not get_backend().exists(f.path):
        raise HTTPException(status_code=410, detail="file_blob_missing")
    return _serve_file(f.path, f.original_filename)


@router.get(
    "/{file_id}/download-url",
    summary="Get a pre-signed direct download URL (S3 only)",
    description=(
        "Returns a short-lived pre-signed URL when storage backend is S3. "
        "Falls back to API streaming URL for local storage."
    ),
)
def download_url(file_id: int, session: Session = Depends(get_session)) -> dict:
    f = get_or_404(session, File, file_id, "file_not_found")
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
def download_direct(file_id: int, session: Session = Depends(get_session)):
    f = get_or_404(session, File, file_id, "file_not_found")
    backend = get_backend()
    url = backend.presigned_download_url(f.path, f.original_filename)
    if url:
        return RedirectResponse(url=url, status_code=307)
    return download_file(file_id=file_id, session=session)


@router.get(
    "/{file_id}/thumbnail",
    summary="Get the thumbnail extracted from the file (if any)",
)
def file_thumbnail(file_id: int, session: Session = Depends(get_session)):
    f = get_or_404(session, File, file_id, "file_not_found")  # noqa: F841
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
    file_id: int, request: Request, session: Session = Depends(get_session)
):
    f = get_or_404(session, File, file_id, "file_not_found")
    backend = get_backend()
    if not backend.exists(f.path):
        raise HTTPException(status_code=410, detail="file_blob_missing")

    stem = Path(f.original_filename).stem
    # File blobs are immutable (content-addressed by sha256), so the rendered
    # STL never changes (content-addressed), but keep the browser TTL modest;
    # the ETag still lets it revalidate cheaply after expiry.
    etag = f'"{f.sha256}"'
    cache_headers = {
        "Content-Disposition": f'attachment; filename="{stem}.stl"',
        "Cache-Control": "public, max-age=3600",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=cache_headers)

    # Already STL: stream the blob straight through, no conversion.
    if Path(f.original_filename).suffix.lower() == ".stl":
        data = backend.read_bytes(f.path)
        return StreamingResponse(
            io.BytesIO(data), media_type="application/sla", headers=cache_headers
        )

    # 3MF/OBJ: trimesh conversion is expensive, so cache the result keyed by the
    # source sha256 and serve the cached STL on every subsequent request.
    cache_key = backend.stl_cache_key(f.sha256)
    if backend.exists(cache_key):
        data = backend.read_bytes(cache_key)
        return StreamingResponse(
            io.BytesIO(data), media_type="application/sla", headers=cache_headers
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
        logger.warning("stl cache write failed for file %s", file_id, exc_info=True)

    return StreamingResponse(
        io.BytesIO(data), media_type="application/sla", headers=cache_headers
    )


def _run_thumbnail_rebuild(
    job_id: str, force: bool, session_factory: SessionFactory
) -> None:
    """Walk models and re-render thumbnails. Runs as a background task."""
    from app.services import mesh_processing

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

            backend = get_backend()
            for index, m in enumerate(models):
                assert m.id is not None
                registry.update(
                    job_id,
                    step=index + 1,
                    total_steps=len(models),
                    label=f"rendering model {m.id}",
                    progress=index / len(models) * 100,
                )
                # Newest mesh file wins.
                mesh_file = session.exec(
                    select(File)
                    .where(File.model_id == m.id, File.file_type.in_(_MESH_TYPES))  # type: ignore[attr-defined]
                    .order_by(File.version.desc())  # type: ignore[attr-defined]
                ).first()
                if mesh_file is None:
                    skipped.append(m.id)
                    continue

                if not backend.exists(mesh_file.path):
                    skipped.append(m.id)
                    continue

                try:
                    with backend.local_path(mesh_file.path) as path:
                        thumb_bytes = mesh_processing.render_thumbnail(path)
                except Exception:  # noqa: BLE001 — defensive, log and continue
                    logger.exception(
                        "rebuild: render_thumbnail crashed for model %s", m.id
                    )
                    thumb_bytes = None

                if not thumb_bytes:
                    failed.append(m.id)
                    continue

                assert mesh_file.id is not None
                thumb_key = backend.thumbnail_key(mesh_file.id)
                backend.write_bytes(thumbnail.to_webp(thumb_bytes), thumb_key)
                # Rebuilt thumbnails are WebP; drop the stale PNG variant so
                # the serving fallback never picks it up again.
                backend.delete(backend.legacy_thumbnail_key(mesh_file.id))
                m.thumbnail_path = thumb_key
                m.thumbnail_file_id = mesh_file.id
                session.add(m)
                session.commit()
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
    dependencies=[Depends(require_auth)],
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
    session_factory: SessionFactory = Depends(get_session_factory),
) -> IngestResponse:
    job_id = registry.create()
    background_tasks.add_task(_run_thumbnail_rebuild, job_id, force, session_factory)
    return IngestResponse(
        job_id=job_id, state="pending", message="thumbnail rebuild queued"
    )
