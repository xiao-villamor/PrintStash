"""Search requires a signed-in user; share capabilities never cross this boundary."""

import asyncio
import threading
from contextvars import copy_context
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from printstash_core.inference import EmbeddingError
from printstash_core.inference.images import decode_image
from printstash_core.search.passages import SubjectType
from pydantic import ValidationError
from sqlmodel import Session

from app.api.image_body import read_image
from app.core.errors import ErrorKind, OperationError
from app.core.security import get_current_user, oauth2_scheme
from app.db.models import User
from app.db.session import get_session, get_session_factory
from app.modules.search.retrieval import search
from app.schemas.models import ModelFilters, ModelSort
from app.schemas.search import SearchResponse, SearchStatus
from app.schemas.search_parsing import (
    ParsedSearch,
    ParseSearchRequest,
    SearchPreferencesPatch,
    SearchPreferencesRead,
)

router = APIRouter(prefix="/search", tags=["search"])
_image_slots = threading.BoundedSemaphore(2)


def require_search_user(
    user: User | None = Depends(get_current_user),
    credential: str | None = Depends(oauth2_scheme),
) -> User:
    if user is None:
        # An opaque share credential authorizes only its share route. This
        # uniform rejection does not look up or reveal whether a share exists.
        raise HTTPException(
            status_code=403 if credential else 401, detail="search_user_required"
        )
    return user


@router.get("/status", response_model=SearchStatus)
def search_status(
    user: User = Depends(require_search_user), session: Session = Depends(get_session)
):
    from app.modules.search.status import read

    return read(session, user)


@router.post(
    "/image",
    response_model=SearchResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                **{
                    mime: {
                        "schema": {
                            "type": "string",
                            "format": "binary",
                            "maxLength": 8 * 1024**2,
                        }
                    }
                    for mime in ("image/png", "image/jpeg", "image/webp")
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["image"],
                        "properties": {"image": {"type": "string", "format": "binary"}},
                        "additionalProperties": False,
                    }
                },
            },
        },
    },
)
async def search_image(
    request: Request,
    response: Response,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    user: User = Depends(require_search_user),
) -> SearchResponse:
    from app.modules.search.configuration import settings

    user_id, auth_version = user.id, user.auth_version
    sessions = get_session_factory()
    with sessions.scoped_session() as session:
        if not settings(session).enabled:
            raise OperationError("search_ai_disabled", kind=ErrorKind.CONFLICT)
    if not _image_slots.acquire(blocking=False):
        raise OperationError("inference_query_busy", kind=ErrorKind.BUSY)
    pending = None
    try:
        async with asyncio.timeout(15):
            body, mime = await read_image(
                request.stream(),
                request.headers.get("content-type", ""),
                request.headers.get("content-length"),
            )

        # Clear encoded bytes before retrieval. This mutable holder prevents the
        # executor's argument tuple from retaining the original for the whole query.
        encoded = [body]
        del body

        def perform():
            try:
                image = decode_image(encoded.pop(), mime)
            except EmbeddingError as exc:
                if exc.code == "embedding_image_type_unsupported":
                    raise HTTPException(status_code=415, detail=exc.code) from None
                raise OperationError(
                    exc.code,
                    kind=ErrorKind.TOO_LARGE
                    if exc.code == "embedding_image_too_large"
                    else ErrorKind.INVALID,
                ) from None
            with sessions.scoped_session() as session:
                actor = session.get(User, user_id, populate_existing=True)
                if (
                    actor is None
                    or not actor.is_active
                    or actor.auth_version != auth_version
                ):
                    raise OperationError(
                        "search_user_required", kind=ErrorKind.FORBIDDEN
                    )
                return search(
                    session, actor, "", image=image, limit=limit, cursor=cursor
                )

        pending = asyncio.get_running_loop().run_in_executor(
            None, copy_context().run, perform
        )
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.shield(pending)
    except TimeoutError:
        raise OperationError(
            "search_image_upload_timeout", kind=ErrorKind.TIMEOUT
        ) from None
    finally:
        if pending is not None and not pending.done():
            pending.add_done_callback(lambda _future: _image_slots.release())
        else:
            _image_slots.release()


@router.get("", response_model=SearchResponse)
def search_library(
    q: str = Query("", max_length=512),
    mode: Literal["lexical", "hybrid"] = "hybrid",
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    types: list[SubjectType] | None = Query(None, alias="types[]", max_length=4),
    legs: list[
        Literal["lexical", "semantic_text", "thumbnail", "multiview", "point_cloud"]
    ]
    | None = Query(None, alias="legs[]", max_length=4),
    instant: bool = False,
    filters: str | None = Query(
        None,
        max_length=8192,
        description="Canonical ModelFilters JSON; q is supplied separately. Restricts results to Models.",
    ),
    sort: ModelSort = ModelSort.RELEVANCE,
    user: User = Depends(require_search_user),
    session: Session = Depends(get_session),
) -> SearchResponse:
    try:
        parsed_filters = (
            ModelFilters.model_validate_json(filters) if filters is not None else None
        )
    except ValidationError:
        raise HTTPException(status_code=422, detail="model_filters_invalid") from None
    return search(
        session,
        user,
        q,
        mode=mode,
        limit=limit,
        cursor=cursor,
        types=tuple(types) if types is not None else tuple(SubjectType),
        legs=tuple(legs)
        if legs is not None
        else ("lexical", "semantic_text", "thumbnail", "multiview", "point_cloud"),
        instant=instant,
        filters=parsed_filters,
        sort=sort,
    )


@router.get("/preferences", response_model=SearchPreferencesRead)
def read_preferences(
    response: Response,
    user: User = Depends(require_search_user),
    session: Session = Depends(get_session),
):
    from app.modules.search.preferences import read

    response.headers["Cache-Control"] = "no-store"
    return read(session, user)


@router.patch("/preferences", response_model=SearchPreferencesRead)
def patch_preferences(
    value: SearchPreferencesPatch,
    response: Response,
    user: User = Depends(require_search_user),
    session: Session = Depends(get_session),
):
    from app.modules.search.preferences import update

    response.headers["Cache-Control"] = "no-store"
    return update(session, user, value)


@router.post("/parse", response_model=ParsedSearch)
def parse_search(
    value: ParseSearchRequest,
    response: Response,
    user: User = Depends(require_search_user),
    session: Session = Depends(get_session),
):
    from app.modules.search.parsing import parse

    response.headers["Cache-Control"] = "no-store"
    return parse(session, user, value.query)
