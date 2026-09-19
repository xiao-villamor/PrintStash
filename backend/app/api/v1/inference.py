"""Administrator-only AI configuration; ordinary queries never accept endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends
from printstash_core.inference import EmbeddingError
from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.core.security import get_current_user, require_superuser
from app.db.models import IndexGeneration, User
from app.db.session import get_session
from app.modules.inference.configuration import create
from app.modules.search import configuration, generations
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
def read_settings(session: Session = Depends(get_session)):
    return configuration.read(session)


@router.put("", response_model=SearchSettingsRead)
def update_settings(
    body: SearchSettings,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return configuration.update(session, body, actor_id=user.id)


@search_router.patch("/settings", response_model=SearchSettingsRead)
def patch_settings(
    body: SearchSettings,
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    merged = configuration.settings(session).model_dump() | body.model_dump(
        exclude_unset=True
    )
    return configuration.update(
        session, SearchSettings.model_validate(merged), actor_id=user.id
    )


@router.post("/endpoints", response_model=EndpointRead, status_code=201)
def create_endpoint(body: EndpointProposal, session: Session = Depends(get_session)):
    return create(session, body)


@router.post(
    "/endpoints/from-environment/{kind}", response_model=EndpointRead, status_code=201
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


@router.post("/generations", response_model=GenerationRead, status_code=202)
@search_router.post("/generations", response_model=GenerationRead, status_code=202)
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


@router.post("/generations/{generation_id}/activate", response_model=GenerationRead)
@search_router.post(
    "/generations/{generation_id}/activate", response_model=GenerationRead
)
def activate_generation(
    generation_id: int, body: GenerationAction, session: Session = Depends(get_session)
):
    return generations.activate(session, generation_id, body.version_token)


@router.post("/generations/{generation_id}/cancel", response_model=GenerationRead)
@search_router.post(
    "/generations/{generation_id}/cancel", response_model=GenerationRead
)
def cancel_generation(
    generation_id: int, body: GenerationAction, session: Session = Depends(get_session)
):
    return generations.cancel(session, generation_id, body.version_token)


@router.post("/generations/{generation_id}/retry", response_model=GenerationRead)
@search_router.post("/generations/{generation_id}/retry", response_model=GenerationRead)
def retry_generation(
    generation_id: int, body: GenerationAction, session: Session = Depends(get_session)
):
    return generations.retry_quarantine(session, generation_id, body.version_token)
