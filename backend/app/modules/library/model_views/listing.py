"""Authorized library listings with batched projections."""

from __future__ import annotations

from typing import List, Literal, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import (
    Model,
    User,
)
from app.schemas.models import (
    ModelFilters,
    ModelListItem,
    OutlinerModelRead,
)

from .access import accessible_live_model_ids_stmt
from .filters import _filtered_stmt, filtered_with_rank
from .projections import _hydrate_list_rows, collection_name_for


def read_items_by_ids(session: Session, user: User, model_ids: list[int]) -> list[ModelListItem]:
    """Reuse authorized Model cards in heterogeneous, bounded result pages."""
    if len(model_ids) > 2048:
        raise ValueError("model_projection_limit")
    if not model_ids or not user.is_active:
        return []
    rows = session.exec(
        select(Model).where(
            Model.id.in_(model_ids),
            Model.id.in_(
                accessible_live_model_ids_stmt(session, user).where(Model.id.in_(model_ids))
            ),
        )
    ).all()
    return _hydrate_list_rows(session, user, list(rows))


def list_items(
    session: Session,
    user: User,
    *,
    collection: Optional[str] = None,
    direct: bool = False,
    tags: Optional[List[str]] = None,
    q: Optional[str] = None,
    printer_id: Optional[int] = None,
    printer_presence: Optional[Literal["any", "none"]] = None,
    favorites: bool = False,
    filters: ModelFilters | None = None,
    limit: int = 50,
    offset: int = 0,
) -> List[ModelListItem]:
    """Filtered, paginated library browse with batched per-model facets."""
    # Exclude the external-job sentinel model — it's internal bookkeeping for
    # print jobs that don't map to a real vault model and must never surface in
    # the library grid (vault_stats/export already exclude it, which is why the
    # header count and the grid would otherwise disagree).
    filters = filters or ModelFilters(
        collection=collection,
        direct=direct,
        tag=tags or [],
        q=q,
        printer_id=printer_id,
        printer_presence=printer_presence,
        favorites=favorites,
    )
    stmt, rank = filtered_with_rank(session, user, filters)
    if rank is not None:
        stmt = stmt.order_by(rank.desc())

    # Model.id is the stable tiebreaker: without it, models sharing an
    # updated_at (e.g. a batch ZIP import) sort non-deterministically, so
    # pagination can repeat or skip rows across page boundaries.
    stmt = (
        stmt.order_by(Model.updated_at.desc(), Model.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    )
    rows = session.exec(stmt).all()
    return _hydrate_list_rows(session, user, list(rows))


def outliner_items(
    session: Session,
    user: User,
    *,
    filters: ModelFilters,
    limit: int = 500,
) -> list[OutlinerModelRead]:
    """Minimal desktop outliner rows; no Artifact/Metadata/print hydration."""
    rows = session.exec(
        _filtered_stmt(session, user, filters)
        .order_by(func.lower(Model.name).asc(), Model.id.asc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [
        OutlinerModelRead(
            id=model.id,
            name=model.name,
            collection=collection_name_for(model),
            collection_id=model.collection_id,
        )
        for model in rows
    ]
