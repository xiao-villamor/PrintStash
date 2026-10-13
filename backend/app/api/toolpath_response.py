"""The HTTP shape of a toolpath, shared by the owner and share-link routes."""

from __future__ import annotations

from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session, col, select

from app.core.config import settings
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    FileType,
    JobKind,
)
from app.modules.derivatives import policy, records
from app.modules.media import toolpath
from app.modules.storage.storage_backend.runtime import get_backend

_HEADERS = {"Cache-Control": "private, no-store"}


def toolpath_response(session: Session, file: File) -> Response:
    """ASCII G-code from the Artifact; binary G-code from its derivative.

    Nothing here converts: a binary toolpath still being derived answers 202
    with its derivative state, and a failed conversion answers 422 with the
    reason the derivative recorded.
    """
    if file.file_type != FileType.GCODE:
        raise HTTPException(status_code=404, detail="toolpath_not_gcode")
    if not toolpath.is_binary_gcode(file):
        return Response(
            content=toolpath.read_ascii(file), media_type="text/plain", headers=_HEADERS
        )
    ready = session.exec(
        select(ArtifactDerivative)
        .where(
            ArtifactDerivative.file_id == file.id,
            ArtifactDerivative.kind == DerivativeKind.TOOLPATH,
            ArtifactDerivative.state == DerivativeState.READY,
            col(ArtifactDerivative.storage_key).is_not(None),
        )
        .order_by(
            col(ArtifactDerivative.recipe_version).desc(),
            col(ArtifactDerivative.updated_at).desc(),
        )
        .limit(1)
    ).first()
    if ready is not None:
        assert ready.storage_key is not None
        content = get_backend().read_bytes(ready.storage_key)
        if len(content) > settings.toolpath_output_max_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail="toolpath_output_too_large")
        return Response(content=content, media_type="text/plain", headers=_HEADERS)
    policy.require_enabled(session, JobKind.DERIVATIVES_TOOLPATH)
    row = records.rows_for(session, file).get(DerivativeKind.TOOLPATH)
    if row is not None and row.state is DerivativeState.FAILED:
        # A failed derivative always records why (a database constraint).
        raise HTTPException(status_code=422, detail=row.failure_reason)
    if row is None or row.state is not DerivativeState.READY or not row.storage_key:
        state = "pending" if row is None else str(row.state)
        return JSONResponse(status_code=202, content={"state": state}, headers=_HEADERS)
    content = get_backend().read_bytes(row.storage_key)
    if len(content) > settings.toolpath_output_max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="toolpath_output_too_large")
    return Response(content=content, media_type="text/plain", headers=_HEADERS)
