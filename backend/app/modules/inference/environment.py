"""Environment endpoint presets require the same explicit admin probe as UI input."""

from typing import Literal

from sqlmodel import Session

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.modules.inference.configuration import create
from app.schemas.inference import EndpointProposal, EndpointRead


def configured() -> list[Literal["embedding", "chat"]]:
    result: list[Literal["embedding", "chat"]] = []
    if (
        settings.embedding_provider == "openai_compatible"
        and settings.embedding_endpoint
        and settings.embedding_model
    ):
        result.append("embedding")
    if settings.chat_endpoint and settings.chat_model:
        result.append("chat")
    return result


def import_endpoint(
    session: Session, kind: Literal["embedding", "chat"]
) -> EndpointRead:
    if kind not in configured():
        raise OperationError(
            "inference_environment_unconfigured", kind=ErrorKind.INVALID
        )
    if kind == "embedding":
        proposal = EndpointProposal(
            base_url=settings.embedding_endpoint,
            model=settings.embedding_model,
            revision=settings.embedding_revision,
            model_repo=settings.embedding_model_repo,
            api_key=settings.embedding_api_key,
            headers=settings.embedding_headers,
            native_dimension=settings.embedding_native_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
            max_input_characters=settings.embedding_max_input_characters,
        )
    else:
        proposal = EndpointProposal(
            kind="chat",
            base_url=settings.chat_endpoint,
            model=settings.chat_model,
            revision=settings.chat_revision,
            api_key=settings.chat_api_key,
            headers=settings.chat_headers,
            timeout_seconds=settings.chat_timeout_seconds,
            supports_images=settings.chat_supports_images,
            prefer_responses=settings.chat_prefer_responses,
        )
    return create(session, proposal)
