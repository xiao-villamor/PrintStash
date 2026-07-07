"""Standalone Document items (markdown / PDF / other) shown in the library
alongside models. Markdown docs are edited in-app (``body``); binary docs store
a blob served back through the storage backend.

RBAC is inherited from the document's collection (root docs → superuser only),
mirroring how models are scoped.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File as FileParam,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session, select

from app.api.v1.files import _serve_file
from app.core.config import settings
from app.core.http import get_or_404
from app.core.security import require_auth, require_user
from app.core.time import utcnow
from app.db.models import Collection, CollectionRole, Document, DocumentKind, User
from app.db.scopes import live
from app.db.session import get_session
from app.schemas.documents import (
    DocumentCreate,
    DocumentImageUpload,
    DocumentListItem,
    DocumentRead,
    DocumentUpdate,
)
from app.services import rbac
from app.services.storage_backend import get_backend

router = APIRouter(prefix="/documents", tags=["documents"])

# Raster image formats only (no SVG) for embedding in markdown — served inline.
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_IMAGE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpe?g|gif|webp)$")

_MARKDOWN_EXTS = {".md", ".markdown", ".txt"}
_BINARY_TYPES = {".pdf": "application/pdf"}


def _kind_for(ext: str) -> DocumentKind:
    if ext in _MARKDOWN_EXTS:
        return DocumentKind.MARKDOWN
    if ext == ".pdf":
        return DocumentKind.PDF
    return DocumentKind.OTHER


def _safe_filename(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stem or base)[:120] or "document"
    return f"{safe}{dot}{ext.lower()}"


def _collection_path(session: Session, collection_id: Optional[int]) -> Optional[str]:
    if collection_id is None:
        return None
    col = session.get(Collection, collection_id)
    return col.path if col else None


def _item(session: Session, user: User, doc: Document) -> DocumentListItem:
    return DocumentListItem(
        id=doc.id,
        name=doc.name,
        kind=doc.kind,
        collection=_collection_path(session, doc.collection_id),
        collection_id=doc.collection_id,
        filename=doc.filename,
        effective_role=rbac.effective_collection_role(session, user, doc.collection_id),
        updated_at=doc.updated_at,
    )


def _read(session: Session, user: User, doc: Document) -> DocumentRead:
    item = _item(session, user, doc)
    return DocumentRead(**item.model_dump(), body=doc.body)


def _require_doc(
    session: Session, user: User, document_id: int, minimum: CollectionRole
) -> Document:
    doc = session.exec(
        select(Document).where(Document.id == document_id, live(Document))
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    rbac.require_collection_role(session, user, doc.collection_id, minimum)
    return doc


def _require_collection_edit(
    session: Session, user: User, collection_id: Optional[int]
) -> None:
    if collection_id is not None:
        get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, user, collection_id, CollectionRole.EDIT)


@router.get("", response_model=List[DocumentListItem], summary="List documents")
def list_documents(
    collection: Optional[str] = Query(None),
    direct: bool = Query(False),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[DocumentListItem]:
    stmt = select(Document).where(live(Document))

    if collection:
        path = collection.strip().strip("/").lower()
        if direct:
            cat_ids = select(Collection.id).where(Collection.path == path)
        else:
            cat_ids = select(Collection.id).where(
                (Collection.path == path) | (Collection.path.startswith(path + "/"))
            )
        stmt = stmt.where(Document.collection_id.in_(cat_ids))  # type: ignore[union-attr]
    elif direct:
        stmt = stmt.where(Document.collection_id.is_(None))  # type: ignore[union-attr]

    if not current_user.is_superuser:
        accessible = rbac.accessible_collection_ids(session, current_user)
        if not accessible:
            return []
        stmt = stmt.where(Document.collection_id.in_(accessible))  # type: ignore[union-attr]
    if q:
        stmt = stmt.where(Document.name.ilike(f"%{q}%"))  # type: ignore[union-attr]

    docs = session.exec(
        stmt.order_by(Document.updated_at.desc()).offset(offset).limit(limit)  # type: ignore[union-attr]
    ).all()
    return [_item(session, current_user, d) for d in docs]


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Create a markdown document",
)
def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DocumentRead:
    _require_collection_edit(session, current_user, payload.collection_id)
    doc = Document(
        name=payload.name.strip(),
        kind=DocumentKind.MARKDOWN,
        collection_id=payload.collection_id,
        body=payload.body or "",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return _read(session, current_user, doc)


@router.post(
    "/upload",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Upload a document file (PDF, MD, or other)",
)
async def upload_document(
    file: UploadFile = FileParam(...),
    name: Optional[str] = Form(None),
    collection_id: Optional[int] = Form(None),
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DocumentRead:
    _require_collection_edit(session, current_user, collection_id)
    raw = file.filename or "document"
    ext = ("." + raw.rsplit(".", 1)[-1].lower()) if "." in raw else ""
    kind = _kind_for(ext)
    display = (name or Path(raw).stem or "Document").strip()

    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="upload_too_large")

    doc = Document(
        name=display,
        kind=kind,
        collection_id=collection_id,
        created_by=current_user.id,
        updated_by=current_user.id,
    )

    if kind is DocumentKind.MARKDOWN:
        # Editable text docs keep their content in the DB, not as a blob.
        data = await file.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="upload_too_large")
        doc.body = data.decode("utf-8", errors="replace")
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return _read(session, current_user, doc)

    # Binary doc — store the blob keyed by the new row id.
    session.add(doc)
    session.commit()
    session.refresh(doc)
    safe = _safe_filename(raw)
    backend = get_backend()
    key = backend.document_file_key(doc.id, safe)
    written = await run_in_threadpool(backend.write_stream, file.file, key)
    if written > settings.max_upload_bytes:
        backend.delete(key)
        session.delete(doc)
        session.commit()
        raise HTTPException(status_code=413, detail="upload_too_large")
    doc.filename = safe
    doc.size_bytes = written
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return _read(session, current_user, doc)


@router.get("/{document_id}", response_model=DocumentRead, summary="Get a document")
def get_document(
    document_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> DocumentRead:
    doc = _require_doc(session, current_user, document_id, CollectionRole.VIEW)
    return _read(session, current_user, doc)


@router.put(
    "/{document_id}",
    response_model=DocumentRead,
    dependencies=[Depends(require_auth)],
    summary="Update a document (rename / edit markdown body)",
)
def update_document(
    document_id: int,
    payload: DocumentUpdate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DocumentRead:
    doc = _require_doc(session, current_user, document_id, CollectionRole.EDIT)
    if payload.name is not None:
        doc.name = payload.name.strip()
    if payload.body is not None:
        if doc.kind is not DocumentKind.MARKDOWN:
            raise HTTPException(status_code=400, detail="not_a_markdown_document")
        doc.body = payload.body
    doc.updated_at = utcnow()
    doc.updated_by = current_user.id
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return _read(session, current_user, doc)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Delete a document",
)
def delete_document(
    document_id: int,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    doc = _require_doc(session, current_user, document_id, CollectionRole.EDIT)
    doc.deleted_at = utcnow()
    doc.deleted_by = current_user.id
    session.add(doc)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{document_id}/file", summary="Serve a binary document's blob")
def get_document_file(
    document_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    doc = _require_doc(session, current_user, document_id, CollectionRole.VIEW)
    if not doc.filename:
        raise HTTPException(status_code=404, detail="no_file")
    ext = "." + doc.filename.rsplit(".", 1)[-1].lower()
    backend = get_backend()
    key = backend.document_file_key(doc.id, doc.filename)
    if not backend.exists(key):
        raise HTTPException(status_code=404, detail="file_blob_missing")
    return _serve_file(
        key,
        doc.filename,
        _BINARY_TYPES.get(ext, "application/octet-stream"),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/{document_id}/images",
    response_model=DocumentImageUpload,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Upload an image to embed in a markdown document",
)
async def upload_document_image(
    document_id: int,
    file: UploadFile = FileParam(...),
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DocumentImageUpload:
    doc = _require_doc(session, current_user, document_id, CollectionRole.EDIT)
    raw = file.filename or ""
    ext = ("." + raw.rsplit(".", 1)[-1].lower()) if "." in raw else ""
    if ext not in _IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="unsupported_image_type")

    image_cap = min(10 * 1024 * 1024, settings.max_upload_bytes)
    if file.size is not None and file.size > image_cap:
        raise HTTPException(status_code=413, detail="upload_too_large")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(data) > image_cap:
        raise HTTPException(status_code=413, detail="upload_too_large")

    name = f"{hashlib.sha256(data).hexdigest()}{'.jpg' if ext == '.jpeg' else ext}"
    backend = get_backend()
    key = backend.document_image_key(doc.id, name)
    if not backend.exists(key):
        backend.write_bytes(data, key)
    return DocumentImageUpload(url=f"/api/v1/documents/{doc.id}/images/{name}")


@router.get(
    "/{document_id}/images/{name}", summary="Serve an image embedded in a document"
)
def get_document_image(
    document_id: int,
    name: str,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not _IMAGE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="image_not_found")
    doc = _require_doc(session, current_user, document_id, CollectionRole.VIEW)
    backend = get_backend()
    key = backend.document_image_key(doc.id, name)
    if not backend.exists(key):
        raise HTTPException(status_code=404, detail="image_not_found")
    media_type = _IMAGE_TYPES[f".{name.rsplit('.', 1)[-1]}"]
    return _serve_file(
        key,
        name,
        media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
