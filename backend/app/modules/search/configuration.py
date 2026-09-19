"""Independent opt-ins for retrieval, local models, captions and NL filters."""

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import InferenceEndpoint
from app.modules.administration import audit
from app.modules.administration.config_repository import get_or_create
from app.modules.inference.configuration import list_endpoints
from app.modules.inference.environment import configured
from app.modules.search.settings import settings
from app.schemas.inference import SearchSettings, SearchSettingsRead

__all__ = ["settings", "read", "update"]


def read(session: Session) -> SearchSettingsRead:
    return SearchSettingsRead(
        settings=settings(session),
        endpoints=list_endpoints(session),
        environment_endpoints=configured(),
    )


def update(
    session: Session, value: SearchSettings, *, actor_id: int | None = None
) -> SearchSettingsRead:
    endpoint = (
        session.get(InferenceEndpoint, value.chat_endpoint_id)
        if value.chat_endpoint_id
        else None
    )
    if value.chat_endpoint_id and (endpoint is None or endpoint.kind != "chat"):
        raise OperationError("inference_chat_unavailable", kind=ErrorKind.INVALID)
    if value.nl_filters_enabled and endpoint is None:
        raise OperationError("inference_chat_required", kind=ErrorKind.INVALID)
    if value.captions_enabled and (
        endpoint is None
        or not endpoint.supports_images
        or not value.send_rendered_images
    ):
        raise OperationError(
            "inference_caption_consent_required", kind=ErrorKind.INVALID
        )
    if value.sparse_expansion_enabled:
        from printstash_core.inference import EmbeddingError

        from app.modules.inference import model_cache, model_registry
        from app.modules.inference.manifest import SparseModelManifest

        if not value.local_models_enabled or not value.sparse_model_id:
            raise OperationError("search_sparse_model_required", kind=ErrorKind.INVALID)
        try:
            entry = model_registry.require(value.sparse_model_id)
            model = model_cache.resolve(entry.id)
            if not isinstance(model.manifest, SparseModelManifest):
                raise EmbeddingError("embedding_sparse_required")
        except EmbeddingError as exc:
            raise OperationError(exc.code, kind=ErrorKind.INVALID) from None
    row = get_or_create(session, commit=False)
    row.ai_search_settings_json = value.model_dump_json()
    if actor_id is not None:
        row.ai_search_configured_by = actor_id
    session.add(row)
    audit.record(
        session,
        action="ai_search_settings_changed",
        resource_type="ai_search",
        diff=value.model_dump(),
    )
    return read(session)
