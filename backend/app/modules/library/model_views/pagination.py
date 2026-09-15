"""Cursor pagination over stable library ordering."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from sqlalchemy import Float, case, cast, func
from sqlmodel import Session, select

from app.core.time import ensure_utc
from app.db.models import (
    File,
    FileType,
    Metadata,
    Model,
    PrintJob,
    PrintJobState,
    User,
)
from app.db.scopes import live
from app.schemas.models import (
    ModelFilters,
    ModelPageRead,
    ModelSort,
)

from .filters import filtered_with_rank
from .projections import _hydrate_list_rows


def _job_sort_stats():
    completed = func.sum(case((PrintJob.state == PrintJobState.COMPLETED, 1), else_=0))
    decided = func.sum(
        case(
            (
                PrintJob.state.in_([PrintJobState.COMPLETED, PrintJobState.FAILED]),
                1,
            ),
            else_=0,
        )
    )
    return (
        select(
            PrintJob.model_id.label("model_id"),
            (cast(completed, Float) / func.nullif(decided, 0)).label("success_rate"),
            func.max(PrintJob.finished_at).label("last_printed_at"),
            func.avg(PrintJob.actual_duration_s).label("average_duration_s"),
            func.sum(PrintJob.cost).label("total_cost"),
        )
        .where(live(PrintJob))
        .group_by(PrintJob.model_id)
        .subquery("browse_job_stats")
    )


def _latest_gcode_metadata():
    ranked = (
        select(
            File.model_id.label("model_id"),
            Metadata.estimated_time_s.label("estimated_time_s"),
            Metadata.filament_weight_g.label("filament_weight_g"),
            func.row_number()
            .over(
                partition_by=File.model_id,
                order_by=(File.uploaded_at.desc(), File.id.desc()),  # type: ignore[attr-defined]
            )
            .label("row_number"),
        )
        .join(Metadata, Metadata.file_id == File.id)
        .where(File.file_type == FileType.GCODE, live(File))
        .subquery("ranked_gcode_metadata")
    )
    return (
        select(
            ranked.c.model_id,
            ranked.c.estimated_time_s,
            ranked.c.filament_weight_g,
        )
        .where(ranked.c.row_number == 1)
        .subquery("latest_gcode_metadata")
    )


def _sort_value_and_statement(stmt, sort: ModelSort, rank=None):
    if sort == ModelSort.RELEVANCE:
        return stmt, rank if rank is not None else Model.updated_at
    if sort in (ModelSort.DATE_DESC, ModelSort.DATE_ASC):
        return stmt, Model.updated_at
    if sort in (ModelSort.NAME_ASC, ModelSort.NAME_DESC):
        return stmt, func.lower(Model.name)
    if sort == ModelSort.FILAMENT_ASC:
        metadata = _latest_gcode_metadata()
        return (
            stmt.outerjoin(metadata, metadata.c.model_id == Model.id),
            metadata.c.filament_weight_g,
        )

    jobs = _job_sort_stats()
    stmt = stmt.outerjoin(jobs, jobs.c.model_id == Model.id)
    if sort == ModelSort.SUCCESS_DESC:
        return stmt, jobs.c.success_rate
    if sort == ModelSort.PRINTED_DESC:
        return stmt, jobs.c.last_printed_at
    if sort == ModelSort.COST_ASC:
        return stmt, jobs.c.total_cost

    metadata = _latest_gcode_metadata()
    stmt = stmt.outerjoin(metadata, metadata.c.model_id == Model.id)
    if sort == ModelSort.DURATION_ASC:
        return stmt, func.coalesce(
            jobs.c.average_duration_s, metadata.c.estimated_time_s
        )
    raise ValueError(f"unsupported_model_sort:{sort}")


def _encode_model_cursor(
    sort: ModelSort,
    value: object,
    model_id: int,
    total: int,
    filter_key: str,
) -> str:
    encoded_value = value
    if isinstance(value, datetime):
        encoded_value = ensure_utc(value).isoformat()
    payload = json.dumps(
        {
            "sort": sort.value,
            "value": encoded_value,
            "id": model_id,
            "total": total,
            "filters": filter_key,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_model_cursor(
    cursor: str, sort: ModelSort, filter_key: str
) -> tuple[object, int, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if payload.get("sort") != sort.value or payload.get("filters") != filter_key:
            raise ValueError
        value = payload["value"]
        model_id = int(payload["id"])
        total = int(payload["total"])
        if model_id <= 0 or total < 0:
            raise ValueError
        if value is not None and sort in (
            ModelSort.DATE_DESC,
            ModelSort.DATE_ASC,
            ModelSort.PRINTED_DESC,
        ):
            value = datetime.fromisoformat(str(value))
        elif value is not None and sort not in (
            ModelSort.NAME_ASC,
            ModelSort.NAME_DESC,
        ):
            value = float(value)
        return value, model_id, total
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_model_cursor") from exc


def _model_filter_key(filters: ModelFilters, user: User) -> str:
    payload = json.dumps(
        {
            "filters": filters.model_dump(mode="json"),
            "user_id": user.id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _apply_model_cursor(
    stmt,
    expression,
    sort: ModelSort,
    cursor: tuple[object, int, int] | None,
):
    descending = sort in (
        ModelSort.RELEVANCE,
        ModelSort.DATE_DESC,
        ModelSort.NAME_DESC,
        ModelSort.SUCCESS_DESC,
        ModelSort.PRINTED_DESC,
    )
    if cursor:
        value, model_id, _total = cursor
        id_after = Model.id < model_id if descending else Model.id > model_id
        if value is None:
            stmt = stmt.where(expression.is_(None), id_after)
        else:
            value_after = expression < value if descending else expression > value
            stmt = stmt.where(
                value_after | ((expression == value) & id_after) | expression.is_(None)
            )
    null_rank = case((expression.is_(None), 1), else_=0)
    order_value = expression.desc() if descending else expression.asc()
    order_id = Model.id.desc() if descending else Model.id.asc()  # type: ignore[attr-defined]
    return stmt.order_by(null_rank.asc(), order_value, order_id)


def page_items(
    session: Session,
    user: User,
    *,
    filters: ModelFilters,
    sort: ModelSort = ModelSort.DATE_DESC,
    cursor: str | None = None,
    limit: int = 60,
) -> ModelPageRead:
    """Globally sorted keyset page; the browser never drains the full library."""
    filtered, rank = filtered_with_rank(session, user, filters)
    if rank is not None and sort == ModelSort.DATE_DESC:
        sort = ModelSort.RELEVANCE
    elif rank is None and sort == ModelSort.RELEVANCE:
        sort = ModelSort.DATE_DESC
    filter_key = _model_filter_key(filters, user)
    decoded_cursor = _decode_model_cursor(cursor, sort, filter_key) if cursor else None
    if decoded_cursor is None:
        total = int(
            session.exec(
                select(func.count()).select_from(
                    filtered.with_only_columns(Model.id).subquery()
                )
            ).one()
        )
    else:
        total = decoded_cursor[2]
    stmt, sort_value = _sort_value_and_statement(filtered, sort, rank)
    stmt = _apply_model_cursor(stmt, sort_value, sort, decoded_cursor)
    raw_rows = session.execute(stmt.add_columns(sort_value).limit(limit + 1)).all()
    has_more = len(raw_rows) > limit
    page_rows = raw_rows[:limit]
    models = [row[0] for row in page_rows]
    next_cursor = None
    if has_more and page_rows:
        last_model, last_value = page_rows[-1]
        next_cursor = _encode_model_cursor(
            sort, last_value, last_model.id, total, filter_key
        )
    return ModelPageRead(
        items=_hydrate_list_rows(session, user, models),
        next_cursor=next_cursor,
        total=total,
    )


def ordered_ids(
    session: Session,
    user: User,
    filters: ModelFilters,
    sort: ModelSort,
    *,
    ids: list[int] | None = None,
    limit: int = 2049,
) -> list[int]:
    """Bounded canonical ordering for the heterogeneous search materializer."""
    filtered, rank = filtered_with_rank(session, user, filters)
    if ids is not None:
        if len(ids) > 2048:
            raise ValueError("model_projection_limit")
        filtered = filtered.where(Model.id.in_(ids))
    statement, expression = _sort_value_and_statement(filtered, sort, rank)
    statement = _apply_model_cursor(statement, expression, sort, None)
    return list(session.exec(statement.with_only_columns(Model.id).limit(limit)).all())
