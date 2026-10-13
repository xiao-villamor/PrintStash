"""Serve a persisted STL preview or report its durable preparation outcome."""

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.api.artifact_responses import delivery_request, render_delivery
from app.db.models import SENTINEL_FILE_HASH, DerivativeKind, DerivativeState, File
from app.modules.derivatives import records, viewer_stl
from app.modules.derivatives.kinds import VIEWER_TYPES
from app.modules.storage.artifact_delivery import (
    DeliveryPurpose,
    plan_stored_representation,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.schemas.jobs import DerivativeStatus


def derived_stl_response(
    session: Session, file: File, request: Request, purpose: DeliveryPurpose
) -> Response:
    if file.file_type not in VIEWER_TYPES:
        raise HTTPException(status_code=404, detail="unsupported_format")
    if file.sha256 == SENTINEL_FILE_HASH:
        raise HTTPException(status_code=409, detail="file_hash_pending")
    row = records.rows_for(session, file).get(DerivativeKind.VIEWER_STL)
    backend = get_backend()
    if row is not None and row.state is DerivativeState.READY:
        if row.storage_key is None:
            raise RuntimeError("ready_viewer_stl_without_storage_key")
        if backend.exists(row.storage_key):
            from pathlib import Path

            delivery = delivery_request(
                request,
                f"{Path(file.original_filename).stem}.stl",
                "application/sla",
                purpose,
            )
            return render_delivery(
                plan_stored_representation(backend, row.storage_key, delivery)
            )
        records.invalidate(session, file, [DerivativeKind.VIEWER_STL])
        session.commit()
    viewer_stl.request(session, file)
    status = next(
        read
        for read in records.read(session, file)
        if read.kind is DerivativeKind.VIEWER_STL
    )
    content = status.model_dump(mode="json")
    headers = {"Cache-Control": "private, no-store"}
    if status.state in {
        DerivativeStatus.FAILED,
        DerivativeStatus.CANCELLED,
        DerivativeStatus.SKIPPED,
    }:
        content["detail"] = (
            status.failure_reason
            if status.failure_reason is not None
            else status.state.value
        )
        return JSONResponse(status_code=422, content=content, headers=headers)
    return JSONResponse(
        status_code=202, content=content, headers={**headers, "Retry-After": "1"}
    )
