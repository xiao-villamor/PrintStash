"""Stable SQL pages bound to the authenticated user, filters and card kind."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import datetime

from sqlalchemy import and_, case, func, or_
from sqlmodel import Session, select

from app.core.errors import OperationError
from app.core.time import ensure_utc


def context_key(user_id: int, mode: str, filters: dict, sort: str) -> str:
    data = json.dumps(
        [user_id, mode, filters, sort], sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(data).hexdigest()


def _decode(cursor: str, key: str, value_type: str):
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(data, dict) or data.get("context") != key:
            raise ValueError
        kind, row_id, value = data["kind"], data["id"], data["value"]
        if (
            kind not in ("model", "family", "member")
            or type(row_id) is not int
            or not 0 < row_id < 2**63
        ):
            raise ValueError
        if value is not None:
            if value_type == "date":
                value = ensure_utc(datetime.fromisoformat(value))
            elif value_type == "text":
                if not isinstance(value, str) or len(value) > 255:
                    raise ValueError
            elif type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError
        return kind, row_id, value
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise OperationError("family_cursor_invalid") from exc


def page_rows(
    session: Session,
    statement,
    *,
    key: str,
    cursor: str | None,
    limit: int,
    descending: bool,
    value_type: str,
):
    """The statement exposes kind, card_id, sort_value, plus optional payload."""
    rows = statement.subquery("family_page_rows")
    # Recount against current permissions; unsigned cursors never provide totals.
    total = int(session.exec(select(func.count()).select_from(rows)).one())
    stmt = select(rows)
    value, row_id, kind = rows.c.sort_value, rows.c.card_id, rows.c.kind
    if cursor:
        old_kind, old_id, old_value = _decode(cursor, key, value_type)
        id_after = row_id < old_id if descending else row_id > old_id
        tie_after = or_(kind > old_kind, and_(kind == old_kind, id_after))
        if old_value is None:
            stmt = stmt.where(value.is_(None), tie_after)
        else:
            after = value < old_value if descending else value > old_value
            stmt = stmt.where(
                or_(after, and_(value == old_value, tie_after), value.is_(None))
            )
    stmt = stmt.order_by(
        case((value.is_(None), 1), else_=0),
        value.desc() if descending else value.asc(),
        kind,
        row_id.desc() if descending else row_id.asc(),
    ).limit(limit + 1)
    result = session.execute(stmt).mappings().all()
    page, next_cursor = result[:limit], None
    if len(result) > limit:
        last = page[-1]
        last_value = last["sort_value"]
        if isinstance(last_value, datetime):
            last_value = ensure_utc(last_value).isoformat()
        payload = json.dumps(
            {
                "context": key,
                "kind": last["kind"],
                "id": last["card_id"],
                "value": last_value,
            },
            separators=(",", ":"),
        ).encode()
        next_cursor = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return page, next_cursor, total
