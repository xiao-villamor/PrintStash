"""File download + thumbnail + on-the-fly STL conversion."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlmodel import Session, select

from app.core.config import settings
from app.core.http import get_or_404
from app.core.logging import get_logger
from app.core.security import require_auth
from app.db.models import File, FileType, Model
from app.db.session import get_session
from app.services import storage
from app.services.storage_backend import LocalStorageBackend, get_backend

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

_MESH_TYPES = {FileType.STL, FileType.THREE_MF, FileType.OBJ}


def _serve_file(key: str, filename: str, media_type: str = "application/octet-stream"):
    backend = get_backend()
    if isinstance(backend, LocalStorageBackend):
        path = Path(key)
        if not path.exists():
            raise HTTPException(status_code=410, detail="file_blob_missing")
        return FileResponse(path=str(path), filename=filename, media_type=media_type)
    chunks = backend.stream_chunks(key)
    return StreamingResponse(
        chunks,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _resolve_mesh_path(key: str) -> Path:
    """Return a local Path for mesh operations, downloading from S3 if needed.

    For S3, the returned path points to a temporary file that must be removed
    by the caller when no longer needed.
    """
    backend = get_backend()
    if isinstance(backend, LocalStorageBackend):
        path = Path(key)
        if not path.exists():
            raise HTTPException(status_code=410, detail="file_blob_missing")
        return path
    ext = Path(key).suffix
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.close()
    backend.download_to_path(key, Path(tmp.name))
    return Path(tmp.name)


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
        return {"url": url, "backend": "s3", "expires_in": settings.s3_presigned_url_expire_seconds}
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
    summary="Get the PNG thumbnail extracted from the file (if any)",
)
def file_thumbnail(file_id: int, session: Session = Depends(get_session)):
    f = get_or_404(session, File, file_id, "file_not_found")  # noqa: F841
    thumb_key = storage.thumbnail_path_for(file_id)
    if not get_backend().exists(thumb_key):
        raise HTTPException(status_code=404, detail="thumbnail_not_found")
    return _serve_file(thumb_key, f"{file_id}.png", "image/png")


@router.get(
    "/{file_id}/stl",
    summary="Serve any mesh file as STL for 3D preview",
    description=(
        "If the file is already STL it is served directly. 3MF and OBJ files are "
        "converted to binary STL on the fly via trimesh."
    ),
)
def file_as_stl(file_id: int, session: Session = Depends(get_session)):
    # Lazy import: trimesh is heavy; pulling it in only for endpoints that need it.
    from app.services import mesh_processing

    f = get_or_404(session, File, file_id, "file_not_found")
    path = _resolve_mesh_path(f.path)
    is_temp = isinstance(get_backend(), LocalStorageBackend) is False

    try:
        if path.suffix.lower() == ".stl":
            data = path.read_bytes()
            stem = Path(f.original_filename).stem
            return StreamingResponse(
                io.BytesIO(data),
                media_type="application/sla",
                headers={"Content-Disposition": f'attachment; filename="{stem}.stl"'},
            )

        stl_bytes = mesh_processing.to_stl_bytes(path)
        if stl_bytes is None:
            raise HTTPException(status_code=500, detail="stl_conversion_failed")

        stem = Path(f.original_filename).stem
        return StreamingResponse(
            io.BytesIO(stl_bytes),
            media_type="application/sla",
            headers={"Content-Disposition": f'attachment; filename="{stem}.stl"'},
        )
    finally:
        if is_temp:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


@router.post(
    "/thumbnails/rebuild",
    dependencies=[Depends(require_auth)],
    summary="Regenerate mesh thumbnails for existing models",
    description=(
        "Walks non-soft-deleted models and tries to render a thumbnail from "
        "the newest mesh file (STL/3MF/OBJ). By default only missing "
        "thumbnails are rebuilt; pass force=true to refresh existing "
        "thumbnails after renderer improvements without re-uploading."
    ),
)
def rebuild_missing_thumbnails(
    force: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    from app.services import mesh_processing

    stmt = select(Model).where(Model.deleted_at.is_(None))  # type: ignore[union-attr]
    if not force:
        stmt = stmt.where(Model.thumbnail_file_id.is_(None))  # type: ignore[union-attr]
    models = session.exec(stmt).all()

    rebuilt: list[int] = []
    skipped: list[int] = []
    failed: list[int] = []

    for m in models:
        assert m.id is not None
        # Newest mesh file wins.
        mesh_file = session.exec(
            select(File)
            .where(File.model_id == m.id, File.file_type.in_(_MESH_TYPES))  # type: ignore[attr-defined]
            .order_by(File.version.desc())  # type: ignore[attr-defined]
        ).first()
        if mesh_file is None:
            skipped.append(m.id)
            continue

        if not get_backend().exists(mesh_file.path):
            skipped.append(m.id)
            continue

        is_temp = False
        path = None
        try:
            path = _resolve_mesh_path(mesh_file.path)
            is_temp = not isinstance(get_backend(), LocalStorageBackend)
            thumb_bytes = mesh_processing.render_thumbnail(path)
        except Exception:  # noqa: BLE001 — defensive, log and continue
            logger.exception("rebuild: render_thumbnail crashed for model %s", m.id)
            thumb_bytes = None
        finally:
            if is_temp and path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        if not thumb_bytes:
            failed.append(m.id)
            continue

        assert mesh_file.id is not None
        thumb_key = storage.thumbnail_path_for(mesh_file.id)
        get_backend().write_bytes(thumb_bytes, thumb_key)
        m.thumbnail_path = thumb_key
        m.thumbnail_file_id = mesh_file.id
        session.add(m)
        session.commit()
        rebuilt.append(m.id)
        logger.info(
            "rebuild: thumbnail regenerated for model %s file %s", m.id, mesh_file.id
        )

    return {
        "scanned": len(models),
        "rebuilt": rebuilt,
        "skipped_no_mesh": skipped,
        "failed_render": failed,
    }
