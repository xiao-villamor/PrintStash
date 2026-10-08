"""Bounded read-only library sidebar endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.core.security import require_auth, require_user
from app.db.models import User
from app.db.session import get_session
from app.modules.library import outliner
from app.schemas.outliner import (
    OutlinerCollectionPage,
    OutlinerEntryPage,
    OutlinerQuery,
    OutlinerRestoreQuery,
    OutlinerRestoreRead,
    OutlinerSearchPage,
)

router = APIRouter(
    prefix="/outliner", tags=["outliner"], dependencies=[Depends(require_auth)]
)


def _validate(query: OutlinerQuery, user: User, scope: outliner.Scope) -> None:
    if (
        query.printer_id is not None or query.printer_presence is not None
    ) and not user.is_superuser:
        raise HTTPException(403, "admin_required")
    if query.collection is not None or query.direct:
        raise HTTPException(422, "outliner_use_collection_id")
    if scope == outliner.Scope.SEARCH:
        if query.q is None or not query.q.strip():
            raise HTTPException(422, "outliner_search_required")
        if (
            query.parent_id is not None
            or query.collection_id is not None
            or query.reveal_id is not None
        ):
            raise HTTPException(422, "outliner_search_is_global")
    elif query.q is not None:
        raise HTTPException(422, "outliner_use_search")
    if scope == outliner.Scope.COLLECTIONS and query.collection_id is not None:
        raise HTTPException(422, "outliner_use_parent_id")
    if scope == outliner.Scope.ENTRIES and (
        query.parent_id is not None or query.reveal_id is not None
    ):
        raise HTTPException(422, "outliner_use_collection_id")


@router.get("/collections", response_model=OutlinerCollectionPage)
def collections(
    query: Annotated[OutlinerQuery, Query()],
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    _validate(query, user, outliner.Scope.COLLECTIONS)
    return outliner.collections(session, user, query)


@router.get("/entries", response_model=OutlinerEntryPage)
def entries(
    query: Annotated[OutlinerQuery, Query()],
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    _validate(query, user, outliner.Scope.ENTRIES)
    return outliner.entries(session, user, query)


@router.get("/search", response_model=OutlinerSearchPage)
def search(
    query: Annotated[OutlinerQuery, Query()],
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    _validate(query, user, outliner.Scope.SEARCH)
    return outliner.search(session, user, query)


@router.post("/restore", response_model=OutlinerRestoreRead)
def restore(
    query: OutlinerRestoreQuery,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    # A read with a body avoids unbounded URL lengths for persisted paths.
    _validate(query, user, outliner.Scope.COLLECTIONS)
    if (
        query.cursor is not None
        or query.parent_id is not None
        or query.reveal_id is not None
    ):
        raise HTTPException(422, "outliner_restore_is_first_pages")
    return outliner.restore(session, user, query)
