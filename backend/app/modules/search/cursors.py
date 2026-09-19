"""Short-lived signed query contexts; cursors contain neither text nor vectors."""

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError


def context_key(
    user_id: int,
    query: str,
    mode: str,
    generations: tuple[int, ...] = (),
    *,
    scope: tuple = (),
) -> str:
    return hashlib.sha256(
        json.dumps(
            [user_id, query, mode, generations, scope], separators=(",", ":")
        ).encode()
    ).hexdigest()


def encode(context: str, offset: int, *, generations: tuple[int, ...] = ()) -> str:
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                [context, offset, int(time.time()) + 900, generations],
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        settings.jwt_secret.encode(), ("search:" + body).encode(), hashlib.sha256
    ).hexdigest()
    return body + "." + signature


def decode(cursor: str, context: str, *, generations: tuple[int, ...] = ()) -> int:
    try:
        if len(cursor) > 512:
            raise ValueError
        body, signature = cursor.split(".")
        expected = hmac.new(
            settings.jwt_secret.encode(), ("search:" + body).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        stored, offset, expires, previous_generations = json.loads(
            base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        )
        if (
            type(offset) is not int
            or not 0 <= offset <= 2048
            or type(expires) is not int
            or not isinstance(previous_generations, list)
            or any(type(value) is not int for value in previous_generations)
        ):
            raise ValueError
        if expires < time.time() or previous_generations != list(generations):
            raise OperationError("search_cursor_expired", kind=ErrorKind.CONFLICT)
        if stored != context:
            raise ValueError
        return offset
    except (ValueError, TypeError, OverflowError) as exc:
        raise OperationError("search_cursor_invalid") from exc
