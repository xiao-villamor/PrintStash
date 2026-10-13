"""Product operation failures independent from a transport or persistence SDK."""

from enum import Enum


class ErrorKind(Enum):
    INVALID = "invalid"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PRECONDITION_FAILED = "precondition_failed"
    PRECONDITION_REQUIRED = "precondition_required"
    GONE = "gone"
    CAPACITY = "capacity"
    TOO_LARGE = "too_large"
    UNPROCESSABLE = "unprocessable"
    BUSY = "busy"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"
    UNAVAILABLE = "unavailable"


class OperationError(Exception):
    def __init__(
        self,
        detail: str,
        *,
        kind: ErrorKind = ErrorKind.INVALID,
        retry_after_seconds: int | None = None,
        capacity: dict[str, int | None] | None = None,
    ) -> None:
        self.detail = detail
        self.code = detail
        self.kind = kind
        self.retry_after_seconds = retry_after_seconds
        self.capacity = capacity
        super().__init__(self.code)
