"""Administrator-only AI configuration; ordinary queries never accept endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, Response
from printstash_core.inference import EmbeddingError
from sqlmodel import Session

from app.api.edit_preconditions import edit_precondition
from app.core.errors import ErrorKind, OperationError
from app.core.security import get_current_user, require_auth, require_superuser
from app.db.models import IndexGeneration, User
from app.db.session import get_session
from app.modules.inference.configuration import create
from app.modules.search import configuration, generations, settings_edits
from app.schemas.editing import EditPrecondition
from app.schemas.inference import (
    EndpointProposal,
    EndpointRead,
    SearchSettings,
    SearchSettingsRead,
)
from app.schemas.search_generations import (
    GenerationAction,
    GenerationEstimate,
    GenerationProposal,
    GenerationRead,
)

router = APIRouter(
    prefix="/config/ai-search",
    tags=["config"],
    dependencies=[Depends(require_superuser)],
)
search_router = APIRouter(
    prefix="/search", tags=["search"], dependencies=[Depends(require_superuser)]
)


@router.get("", response_model=SearchSettingsRead)
@search_router.get("/settings", response_model=SearchSettingsRead)
def read_settings(response: Response, session: Session = Depends(get_session)):
    result = configuration.read(session)
    response.headers["ETag"] = settings_edits.etag(result)
    return result


@router.put("", response_model=SearchSettingsRead, dependencies=[Depends(require_auth)])
def update_settings(
    body: SearchSettings,
    response: Response,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    precondition: EditPrecondition = Depends(edit_precondition),
):
    result = configuration.update(
        session, body, actor=user, base=settings_edits.expected_base(precondition)
    )
    response.headers["ETag"] = settings_edits.etag(result)
    return result


@search_router.patch(
    "/settings", response_model=SearchSettingsRead, dependencies=[Depends(require_auth)]
)
def patch_settings(
    body: SearchSettings,
    response: Response,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    precondition: EditPrecondition = Depends(edit_precondition),
):
    result = configuration.update(
        session,
        body,
        actor=user,
        base=settings_edits.expected_base(precondition),
        partial=True,
    )
    response.headers["ETag"] = settings_edits.etag(result)
    return result


@router.post(
    "/endpoints",
    response_model=EndpointRead,
    status_code=201,
    dependencies=[Depends(require_auth)],
)
def create_endpoint(body: EndpointProposal, session: Session = Depends(get_session)):
    return create(session, body)


@router.post(
    "/endpoints/from-environment/{kind}",
    response_model=EndpointRead,
    status_code=201,
    dependencies=[Depends(require_auth)],
)
def import_environment_endpoint(
    kind: Literal["embedding", "chat"], session: Session = Depends(get_session)
):
    from app.modules.inference.environment import import_endpoint

    return import_endpoint(session, kind)


@router.get("/generations", response_model=list[GenerationRead])
@search_router.get("/generations", response_model=list[GenerationRead])
def read_generations(session: Session = Depends(get_session)):
    return generations.list_generations(session)


@router.post("/generations/estimate", response_model=GenerationEstimate)
@search_router.post("/generations/estimate", response_model=GenerationEstimate)
def estimate_generation(
    body: GenerationProposal, session: Session = Depends(get_session)
):
    try:
        return generations.estimate(session, body)
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.INVALID) from None


@router.post(
    "/generations",
    response_model=GenerationRead,
    status_code=202,
    dependencies=[Depends(require_auth)],
)
@search_router.post(
    "/generations",
    response_model=GenerationRead,
    status_code=202,
    dependencies=[Depends(require_auth)],
)
def propose_generation(
    body: GenerationProposal,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return generations.prepare(session, user, body)
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.INVALID) from None


@router.get("/generations/{generation_id}", response_model=GenerationRead)
@search_router.get("/generations/{generation_id}", response_model=GenerationRead)
def read_generation(generation_id: int, session: Session = Depends(get_session)):
    generation = session.get(IndexGeneration, generation_id)
    if generation is None or generation.version_token is None:
        raise OperationError("search_generation_not_found", kind=ErrorKind.NOT_FOUND)
    return generations.read(session, generation)


@router.post(
    "/generations/{generation_id}/activate",
    response_model=GenerationRead,
    dependencies=[Depends(require_auth)],
)
@search_router.post(
    "/generations/{generation_id}/activate",
    response_model=GenerationRead,
    dependencies=[Depends(require_auth)],
)
def activate_generation(
    generation_id: int, body: GenerationAction, session: Session = Depends(get_session)
):
    return generations.activate(session, generation_id, body.version_token)


@router.post(
    "/generations/{generation_id}/cancel",
    response_model=GenerationRead,
    dependencies=[Depends(require_auth)],
)
@search_router.post(
    "/generations/{generation_id}/cancel",
    response_model=GenerationRead,
    dependencies=[Depends(require_auth)],
)
def cancel_generation(
    generation_id: int, body: GenerationAction, session: Session = Depends(get_session)
):
    return generations.cancel(session, generation_id, body.version_token)


@router.post(
    "/generations/{generation_id}/retry",
    response_model=GenerationRead,
    dependencies=[Depends(require_auth)],
)
@search_router.post(
    "/generations/{generation_id}/retry",
    response_model=GenerationRead,
    dependencies=[Depends(require_auth)],
)
def retry_generation(
    generation_id: int, body: GenerationAction, session: Session = Depends(get_session)
):
    return generations.retry_quarantine(session, generation_id, body.version_token)
