"""Library Documents and Model-owned Documents, stored and served privately.

Access follows the owning Collection or the Model's current Collection.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import (
    File as FileParam,
)
from sqlmodel import Session, col, or_, select

from app.api.artifact_responses import serve_stored_file
from app.api.command_actor import CommandActor, command_session, require_command_writer
from app.api.command_execution import bounded_command
from app.api.edit_preconditions import edit_precondition
from app.core.config import settings
from app.core.http import get_or_404
from app.core.security import require_auth, require_superuser, require_user
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionRole,
    Document,
    DocumentKind,
    Model,
    MultipartModel,
    User,
)
from app.db.projections import content_changed
from app.db.scopes import live, trashed
from app.db.session import get_session
from app.modules.identity import rbac
from app.modules.library import document_access, document_uploads, multipart_models
from app.modules.library.edit_preconditions import EditKind, etag
from app.modules.library.edit_preconditions import claim as claim_edit
from app.modules.library.model_views.access import accessible_live_model_ids_stmt
from app.modules.library.trash import (
    StorageRiskConfirmationRequired,
    hard_delete_document,
    restore_document,
)
from app.modules.storage.artifact_delivery import PRIVATE_NO_STORE
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import process_storage_delete_intents
from app.modules.storage.storage_ownership import (
    UnsafeStorageDeleteError,
    abandon_publication,
    adopt_publication,
    prepare_bytes,
)
from app.schemas.documents import (
    DocumentCreate,
    DocumentImageUpload,
    DocumentListItem,
    DocumentRead,
    DocumentUpdate,
)
from app.schemas.editing import EditPrecondition

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

_BINARY_TYPES = {".pdf": "application/pdf", **_IMAGE_TYPES}


def _collection_path(session: Session, collection_id: Optional[int]) -> Optional[str]:
    if collection_id is None:
        return None
    col = session.get(Collection, collection_id)
    return col.path if col else None


def _item_for_scope(
    doc: Document,
    collection_id: int | None,
    collection_path: str | None,
    role: CollectionRole | None,
) -> DocumentListItem:
    return DocumentListItem(
        edit_epoch=doc.edit_epoch,
        edit_version=doc.edit_version,
        id=doc.id,
        name=doc.name,
        kind=doc.kind,
        collection=collection_path,
        collection_id=collection_id,
        multipart_model_id=doc.multipart_model_id,
        model_id=doc.model_id,
        filename=doc.filename,
        effective_role=role,
        updated_at=doc.updated_at,
    )


def _live_model(session: Session, model_id: int) -> Model:
    model = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if model is None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return model


def _item(session: Session, user: User, doc: Document) -> DocumentListItem:
    collection_id = document_access.collection_id(session, doc)
    return _item_for_scope(
        doc,
        collection_id,
        _collection_path(session, collection_id),
        rbac.effective_collection_role(session, user, collection_id),
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
    rbac.require_collection_role(
        session, user, document_access.collection_id(session, doc), minimum
    )
    return doc


def _require_collection_edit(
    session: Session, user: User, collection_id: Optional[int]
) -> None:
    if collection_id is not None:
        get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, user, collection_id, CollectionRole.EDIT)


def _guide_collection(
    session: Session,
    user: User,
    multipart_model_id: int | None,
    collection_id: int | None,
) -> tuple[int | None, MultipartModel | None]:
    if multipart_model_id is None:
        _require_collection_edit(session, user, collection_id)
        return collection_id, None
    aggregate = multipart_models.require(
        session, user, multipart_model_id, CollectionRole.EDIT
    )
    if collection_id is not None and collection_id != aggregate.collection_id:
        raise HTTPException(
            status_code=400, detail="multipart_guide_collection_mismatch"
        )
    return aggregate.collection_id, aggregate


@router.get("", response_model=List[DocumentListItem], summary="List documents")
def list_documents(
    collection: Optional[str] = Query(None),
    model_id: int | None = Query(None, gt=0),
    direct: bool = Query(False),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[DocumentListItem]:
    stmt = select(Document).where(live(Document))
    if q:
        stmt = stmt.where(col(Document.name).ilike(f"%{q}%"))
    stmt = stmt.order_by(col(Document.updated_at).desc(), col(Document.id).desc())
    if model_id is not None:
        if collection is not None or direct:
            raise HTTPException(status_code=400, detail="document_scope_conflict")
        model = _live_model(session, model_id)
        role = rbac.require_model_collection_role(
            session, current_user, model.collection_id, CollectionRole.VIEW
        )
        collection_path = _collection_path(session, model.collection_id)
        docs = session.exec(
            stmt.where(Document.model_id == model_id).offset(offset).limit(limit)
        ).all()
        return [
            _item_for_scope(doc, model.collection_id, collection_path, role)
            for doc in docs
        ]

    stmt = stmt.where(col(Document.model_id).is_(None))

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
        stmt = stmt.where(
            Document.collection_id.in_(  # type: ignore[union-attr]
                rbac.accessible_collection_ids_stmt(session, current_user)
            )
        )
    docs = session.exec(stmt.offset(offset).limit(limit)).all()
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
    collection_id = None
    if payload.model_id is not None:
        if payload.collection_id is not None or payload.multipart_model_id is not None:
            raise HTTPException(status_code=400, detail="document_scope_conflict")
        model = _live_model(session, payload.model_id)
        rbac.require_model_collection_role(
            session, current_user, model.collection_id, CollectionRole.EDIT
        )
    else:
        collection_id, _aggregate = _guide_collection(
            session, current_user, payload.multipart_model_id, payload.collection_id
        )
    doc = Document(
        name=payload.name.strip(),
        kind=DocumentKind.MARKDOWN,
        collection_id=collection_id,
        multipart_model_id=payload.multipart_model_id,
        model_id=payload.model_id,
        body=payload.body or "",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    session.add(doc)
    content_changed(session, "document", (row.id for row in (doc,)))
    session.commit()
    session.refresh(doc)
    return _read(session, current_user, doc)


@router.get(
    "/trash", response_model=List[DocumentListItem], summary="List trashed documents"
)
def list_document_trash(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[DocumentListItem]:
    standalone = col(Document.model_id).is_(None)
    if not current_user.is_superuser:
        standalone = standalone & col(Document.collection_id).in_(
            rbac.accessible_collection_ids_stmt(session, current_user)
        )
    stmt = select(Document).where(
        trashed(Document),
        or_(
            standalone,
            col(Document.model_id).in_(
                accessible_live_model_ids_stmt(session, current_user)
            ),
        ),
    )
    documents = session.exec(stmt.order_by(Document.deleted_at.desc())).all()  # type: ignore[union-attr]
    return [_item(session, current_user, document) for document in documents]


@router.post("/{document_id}/restore", response_model=DocumentRead)
def restore_trashed_document(
    document_id: int,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DocumentRead:
    document = session.exec(
        select(Document).where(Document.id == document_id, trashed(Document))
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    rbac.require_collection_role(
        session,
        current_user,
        document_access.collection_id(session, document),
        CollectionRole.EDIT,
    )
    restore_document(session, document)
    session.commit()
    session.refresh(document)
    return _read(session, current_user, document)


@router.delete(
    "/{document_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def permanently_delete_document(
    document_id: int,
    confirm_storage_risk: bool = Query(False),
    _current_user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
) -> Response:
    document = session.exec(
        select(Document).where(Document.id == document_id, trashed(Document))
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="document_not_found")
    try:
        hard_delete_document(
            session,
            document,
            confirm_storage_risk=confirm_storage_risk,
        )
    except StorageRiskConfirmationRequired as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    except UnsafeStorageDeleteError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="storage_ownership_unverified",
        ) from exc
    session.commit()
    process_storage_delete_intents()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/upload",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document file (PDF, MD, or other)",
)
@bounded_command
def upload_document(
    file: UploadFile = FileParam(...),
    name: Optional[str] = Form(None),
    collection_id: Optional[int] = Form(None),
    multipart_model_id: Optional[int] = Form(None),
    model_id: Annotated[int | None, Form(gt=0)] = None,
    actor: CommandActor = Depends(require_command_writer),
) -> DocumentRead:
    with command_session(actor) as (session, current_user):
        model = None
        aggregate = None
        if model_id is not None:
            if collection_id is not None or multipart_model_id is not None:
                raise HTTPException(status_code=400, detail="document_scope_conflict")
            model = _live_model(session, model_id)
        else:
            collection_id, aggregate = _guide_collection(
                session, current_user, multipart_model_id, collection_id
            )
        raw = file.filename or "document"
        doc = document_uploads.publish(
            session,
            current_user,
            stream=file.file,
            filename=raw,
            size=file.size,
            name=(name or Path(raw).stem or "Document").strip(),
            collection_id=collection_id,
            aggregate=aggregate,
            model=model,
        )
        return _read(session, current_user, doc)


@router.get("/{document_id}", response_model=DocumentRead, summary="Get a document")
def get_document(
    document_id: int,
    response: Response,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> DocumentRead:
    doc = _require_doc(session, current_user, document_id, CollectionRole.VIEW)
    result = _read(session, current_user, doc)
    response.headers["ETag"] = etag(
        EditKind.DOCUMENT, result.id, result.edit_version, result.edit_epoch
    )
    return result


@router.put(
    "/{document_id}",
    response_model=DocumentRead,
    dependencies=[Depends(require_auth)],
    summary="Update a document (rename / edit markdown body)",
)
def update_document(
    document_id: int,
    response: Response,
    payload: DocumentUpdate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
    precondition: EditPrecondition = Depends(edit_precondition),
) -> DocumentRead:
    doc = _require_doc(session, current_user, document_id, CollectionRole.EDIT)
    claim_edit(session, current_user, doc, precondition)
    if payload.name is not None:
        doc.name = payload.name.strip()
    if payload.body is not None:
        if doc.kind is not DocumentKind.MARKDOWN:
            raise HTTPException(status_code=400, detail="not_a_markdown_document")
        doc.body = payload.body
    doc.updated_at = utcnow()
    doc.updated_by = current_user.id
    session.add(doc)
    content_changed(session, "document", (row.id for row in (doc,)))
    session.commit()
    session.refresh(doc)
    result = _read(session, current_user, doc)
    response.headers["ETag"] = etag(
        EditKind.DOCUMENT, result.id, result.edit_version, result.edit_epoch
    )
    return result


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
    content_changed(session, "document", (row.id for row in (doc,)))
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
    return serve_stored_file(
        key,
        doc.filename,
        _BINARY_TYPES.get(ext, "application/octet-stream"),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/{document_id}/images",
    response_model=DocumentImageUpload,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to embed in a markdown document",
)
@bounded_command
def upload_document_image(
    document_id: int,
    file: UploadFile = FileParam(...),
    actor: CommandActor = Depends(require_command_writer),
) -> DocumentImageUpload:
    with command_session(actor) as (session, current_user):
        doc = _require_doc(session, current_user, document_id, CollectionRole.EDIT)
        raw = file.filename or ""
        ext = ("." + raw.rsplit(".", 1)[-1].lower()) if "." in raw else ""
        if ext not in _IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="unsupported_image_type")

        image_cap = min(10 * 1024 * 1024, settings.max_upload_bytes)
        if file.size is not None and file.size > image_cap:
            raise HTTPException(status_code=413, detail="upload_too_large")
        data = file.file.read(image_cap + 1)
        if not data:
            raise HTTPException(status_code=400, detail="empty_file")
        if len(data) > image_cap:
            raise HTTPException(status_code=413, detail="upload_too_large")

        name = f"{hashlib.sha256(data).hexdigest()}{'.jpg' if ext == '.jpeg' else ext}"
        backend = get_backend()
        key = backend.document_image_key(doc.id, name)
        candidate = None
        try:
            candidate = prepare_bytes(
                session,
                backend,
                key,
                data,
                object_kind="document_image",
            )
            session.flush()
            adopt_publication(session, candidate)
            session.commit()
        except StorageCollisionError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="storage_destination_exists",
            ) from exc
        except Exception:
            session.rollback()
            if candidate is not None and abandon_publication(session, candidate):
                backend.rollback_create(candidate.receipt)
            raise
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
    return serve_stored_file(
        key,
        name,
        media_type,
        headers={
            "Cache-Control": PRIVATE_NO_STORE
            if doc.model_id is not None
            else "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
