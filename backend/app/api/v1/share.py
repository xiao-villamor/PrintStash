"""Share-link endpoints.

Two routers with very different trust levels:

* ``router`` (prefix ``/share``) — **unauthenticated**, **GET-only**. The entire
  public surface. Every handler resolves the token, re-scopes file access to the
  shared model, and is rate-limited per IP.
* ``admin_router`` — authenticated management (create/list/revoke), guarded by
  the model's collection RBAC.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v1.files import _serve_file, stl_response, thumbnail_response
from app.core.security import require_user
from app.db.models import CollectionRole, FileType, Model, ShareLink, User
from app.db.session import get_session
from app.schemas.share import ShareLinkCreate, ShareLinkCreated, ShareLinkRead
from app.services import rbac, share
from app.services.storage_backend import get_backend
from sqlmodel import Session

_MESH_TYPES = {FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP}


# ---------------------------------------------------------------------------
# Per-IP rate limiter (defense-in-depth against token enumeration).
# ---------------------------------------------------------------------------


class _RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self._limit = limit
        self._window = window_s
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits[key] if now - t < self._window]
            if len(hits) >= self._limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


_limiter = _RateLimiter(limit=120, window_s=60.0)


def _rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    if not _limiter.check(client):
        raise HTTPException(status_code=429, detail="rate_limited")


# ---------------------------------------------------------------------------
# Public router — unauthenticated, GET only.
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/share", tags=["share"], dependencies=[Depends(_rate_limit)]
)


@router.get("/{token}", summary="Public read-only view of a shared model")
def get_shared_model(token: str, session: Session = Depends(get_session)):
    link = share.resolve_share(session, token)
    share.record_access(session, link)
    return share.public_detail(session, link)


@router.get("/{token}/thumbnail", summary="Thumbnail of a shared model")
def get_shared_thumbnail(token: str, session: Session = Depends(get_session)):
    link = share.resolve_share(session, token)
    model = session.get(Model, link.model_id)
    if model is None or model.thumbnail_file_id is None:
        raise HTTPException(status_code=404, detail="not_found")
    return thumbnail_response(model.thumbnail_file_id)


@router.get(
    "/{token}/files/{file_id}/stl",
    summary="Serve a shared mesh file as STL for the public viewer",
)
def get_shared_stl(
    token: str,
    file_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    link = share.resolve_share(session, token)
    f = share.share_file_or_404(session, link, file_id)
    if f.file_type not in _MESH_TYPES:
        raise HTTPException(status_code=404, detail="not_found")
    return stl_response(f, request)


@router.get(
    "/{token}/files/{file_id}/download",
    summary="Download a shared original file (only when the link allows it)",
)
def download_shared_file(
    token: str,
    file_id: int,
    session: Session = Depends(get_session),
):
    link = share.resolve_share(session, token)
    if not link.allow_download:
        raise HTTPException(status_code=403, detail="download_disabled")
    f = share.share_file_or_404(session, link, file_id)
    if not get_backend().exists(f.path):
        raise HTTPException(status_code=410, detail="file_blob_missing")
    return _serve_file(f.path, f.original_filename)


@router.get(
    "/{token}/files/{file_id}/gcode",
    summary="Serve a shared G-code file for the public toolpath preview",
)
def get_shared_gcode(
    token: str,
    file_id: int,
    session: Session = Depends(get_session),
):
    link = share.resolve_share(session, token)
    f = share.share_file_or_404(session, link, file_id)
    if f.file_type != FileType.GCODE:
        raise HTTPException(status_code=404, detail="not_found")
    if not get_backend().exists(f.path):
        raise HTTPException(status_code=410, detail="file_blob_missing")
    return _serve_file(f.path, f.original_filename, media_type="text/plain")


# ---------------------------------------------------------------------------
# Admin router — authenticated share management.
# ---------------------------------------------------------------------------

admin_router = APIRouter(tags=["share"])


def _require_model(
    session: Session, user: User, model_id: int, role: CollectionRole
) -> Model:
    model = session.get(Model, model_id)
    if model is None or model.deleted_at is not None:
        raise HTTPException(status_code=404, detail="model_not_found")
    rbac.require_model_collection_role(session, user, model.collection_id, role)
    return model


@admin_router.post(
    "/models/{model_id}/shares",
    response_model=ShareLinkCreated,
    summary="Create a public share link for a model",
)
def create_model_share(
    model_id: int,
    body: ShareLinkCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ShareLinkCreated:
    _require_model(session, current_user, model_id, CollectionRole.EDIT)
    link, raw_token = share.create_share(
        session,
        model_id=model_id,
        expires_in_days=body.expires_in_days,
        allow_download=body.allow_download,
        revision_file_ids=body.revision_file_ids,
        created_by=current_user.id,
    )
    read = share.to_read(link)
    return ShareLinkCreated(
        **read.model_dump(), token=raw_token, url=f"/share/{raw_token}"
    )


@admin_router.get(
    "/models/{model_id}/shares",
    response_model=list[ShareLinkRead],
    summary="List a model's share links",
)
def list_model_shares(
    model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ShareLinkRead]:
    _require_model(session, current_user, model_id, CollectionRole.VIEW)
    return [share.to_read(link) for link in share.list_shares(session, model_id)]


@admin_router.delete(
    "/shares/{share_id}",
    response_model=ShareLinkRead,
    summary="Revoke a share link",
)
def revoke_model_share(
    share_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ShareLinkRead:
    link = session.get(ShareLink, share_id)
    if link is None:
        raise HTTPException(status_code=404, detail="share_not_found")
    _require_model(session, current_user, link.model_id, CollectionRole.EDIT)
    return share.to_read(share.revoke_share(session, link))
