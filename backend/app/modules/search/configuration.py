"""Independent opt-ins for retrieval, local models, captions and NL filters."""

from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.db.models import InferenceEndpoint, JobKind, User
from app.db.transactions import rollback_on_failure
from app.modules.administration import audit
from app.modules.derivatives import policy
from app.modules.inference.configuration import list_endpoints
from app.modules.inference.environment import configured
from app.modules.search import settings_edits
from app.modules.search.settings import settings
from app.schemas.editing import EditingBase
from app.schemas.inference import SearchSettings, SearchSettingsRead

__all__ = ["settings", "read", "update"]


def read(session: Session) -> SearchSettingsRead:
    base, value = settings_edits.read(session)
    return SearchSettingsRead(
        **base.model_dump(),
        settings=value,
        endpoints=list_endpoints(session),
        environment_endpoints=configured(),
    )


def update(
    session: Session,
    value: SearchSettings,
    *,
    actor: User | None = None,
    base: EditingBase | None = None,
    partial: bool = False,
) -> SearchSettingsRead:
    with rollback_on_failure(session):
        row = policy.lock(session)
        settings_edits.claim(session, actor, row, base)
        if partial:
            value = SearchSettings.model_validate(
                settings(session).model_dump() | value.model_dump(exclude_unset=True)
            )
        _validate(session, value)
        row.ai_search_settings_json = value.model_dump_json()
        if actor is not None:
            row.ai_search_configured_by = actor.id
        session.add(row)
        session.flush()
        receipt = read(session)
        # Register before audit.record commits the settings and audit together.
        from app.modules.work.submission import nudge_after_commit

        for definition in (
            JobKind.SEARCH_PROJECT,
            JobKind.SEARCH_INDEX,
            JobKind.SEARCH_REPAIR,
            JobKind.SEARCH_CAPTION_QUEUE,
            JobKind.SEARCH_EXPAND,
        ):
            nudge_after_commit(session, definition)
        audit.record(
            session,
            action="ai_search_settings_changed",
            resource_type="ai_search",
            diff=value.model_dump(),
        )
        return receipt


def _validate(session: Session, value: SearchSettings) -> None:
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
