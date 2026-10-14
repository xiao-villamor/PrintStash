"""Translate stable operation failures into the existing HTTP error contract."""

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.errors import ErrorKind, OperationError
from app.modules.sources.root_binding import ExternalRootBindingError

_STATUS = {
    ErrorKind.INVALID: 400,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.CONFLICT: 409,
    ErrorKind.PRECONDITION_FAILED: 412,
    ErrorKind.PRECONDITION_REQUIRED: 428,
    ErrorKind.GONE: 410,
    ErrorKind.CAPACITY: 507,
    ErrorKind.TOO_LARGE: 413,
    ErrorKind.UNPROCESSABLE: 422,
    ErrorKind.BUSY: 429,
    ErrorKind.TIMEOUT: 504,
    ErrorKind.UPSTREAM: 502,
    ErrorKind.UNAVAILABLE: 503,
}


async def operation_error_response(
    request: Request, exc: OperationError
) -> JSONResponse:
    headers = (
        {"Retry-After": str(exc.retry_after_seconds)}
        if exc.retry_after_seconds is not None
        else None
    )
    return JSONResponse(
        status_code=_STATUS[exc.kind],
        content={
            "detail": exc.detail,
            **({"capacity": exc.capacity} if exc.capacity is not None else {}),
        },
        headers=headers,
    )


async def external_root_binding_response(
    request: Request, exc: ExternalRootBindingError
) -> JSONResponse:
    """A write into a Library source that cannot take it is a conflict, not a crash."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})
