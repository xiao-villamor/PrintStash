"""Durable proposals, verification and atomic replacement of serving generations."""

from __future__ import annotations

import json
import secrets
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from printstash_core.inference import EmbeddingError
from printstash_core.inference import EmbeddingSpace as Space
from printstash_core.inference.model_capabilities import (
    capabilities_for,
    capabilities_for_identity,
)
from printstash_core.inference.transforms import IndexTransform
from printstash_core.search.visual_inputs import VisualRecipe
from sqlalchemy import delete, func, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    EmbeddingSpace,
    IndexGeneration,
    InferenceEndpoint,
    PassageVector,
    SearchGenerationLease,
    SearchIndexFailure,
    SearchPassage,
    SearchReconciliationState,
    SubjectCaption,
    SystemConfig,
    User,
)
from app.db.session import get_session_factory
from app.db.transactions import begin_write
from app.modules.administration import audit
from app.modules.inference.configuration import load as load_endpoint
from app.modules.search import configuration, vector_index, vector_store, visual_sources
from app.modules.search.access import indexable_passage_ids, passage_in_scope
from app.modules.search.text_inputs import TextRecipe
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.runtime.jobs import registry
from app.schemas.search_generations import (
    GenerationEstimate,
    GenerationProposal,
    GenerationRead,
)

LEASE_SECONDS = 180
READER_SECONDS = 180  # Greater than the hard 120-second inference deadline.


def owned(generation_id: int, token: str):
    return (
        IndexGeneration.id == generation_id,
        IndexGeneration.lease_token == token,
        IndexGeneration.lease_expires_at > utcnow(),
        col(IndexGeneration.cancel_requested).is_(False),
        col(IndexGeneration.state).in_(("active", "building")),
    )


def lock_owned(session: Session, generation_id: int, token: str) -> bool:
    result = session.connection().execute(
        update(IndexGeneration)
        .where(*owned(generation_id, token))
        .values(last_activity_at=utcnow())
    )
    return result.rowcount == 1


def contract(session: Session, generation: IndexGeneration) -> Space:
    row = session.get(EmbeddingSpace, generation.space_id)
    if row is None:
        raise EmbeddingError("embedding_space_unavailable")
    return Space(**json.loads(row.config_json))


def eligible(session: Session, space: Space):
    if space.profile in visual_sources.PROFILES:
        return visual_sources.eligible(session)
    recipe = TextRecipe.for_space(space)
    return select(SearchPassage.id).where(
        SearchPassage.recipe_version == recipe.passage_version,
        passage_in_scope(indexable_passage_ids(session)),
    )


def current_vectors(session: Session, generation_id: int, space: Space):
    if space.profile in visual_sources.PROFILES:
        return visual_sources.current_vectors(session, generation_id, space)
    return (
        select(PassageVector.id)
        .join(SearchPassage, SearchPassage.id == PassageVector.passage_id)
        .where(
            PassageVector.generation_id == generation_id,
            PassageVector.unit_kind == "passage",
            PassageVector.input_hash == SearchPassage.content_hash,
            SearchPassage.id.in_(eligible(session, space)),
            PassageVector.native_dimension == space.dimension,
        )
    )


def missing(session: Session, generation_id: int, space: Space):
    if space.profile in visual_sources.PROFILES:
        return visual_sources.missing(session, generation_id, space)
    present = (
        select(PassageVector.id)
        .where(
            PassageVector.generation_id == generation_id,
            PassageVector.passage_id == SearchPassage.id,
            PassageVector.unit_kind == "passage",
            PassageVector.input_hash == SearchPassage.content_hash,
            PassageVector.native_dimension == space.dimension,
        )
        .exists()
    )
    return eligible(session, space).where(~present)


def counts(session: Session, generation: IndexGeneration) -> tuple[int, int, int]:
    space = contract(session, generation)
    if space.profile in visual_sources.PROFILES:
        return visual_sources.counts(session, generation.id, space)
    total = session.exec(
        select(func.count()).select_from(eligible(session, space).subquery())
    ).one()
    indexed = session.exec(
        select(func.count()).select_from(
            current_vectors(session, generation.id, space).subquery()
        )
    ).one()
    quarantine = session.exec(
        select(func.count())
        .select_from(SearchIndexFailure)
        .join(SearchPassage, SearchPassage.id == SearchIndexFailure.passage_id)
        .where(
            SearchIndexFailure.generation_id == generation.id,
            SearchIndexFailure.state == "quarantined",
            SearchIndexFailure.input_hash == SearchPassage.content_hash,
        )
    ).one()
    return total, indexed, quarantine


def read(session: Session, generation: IndexGeneration) -> GenerationRead:
    space = contract(session, generation)
    total, indexed, quarantined = counts(session, generation)
    return GenerationRead(
        id=generation.id,
        state=generation.state,
        phase=generation.phase,
        version_token=generation.version_token,
        modality=space.modality,
        profile=space.profile,
        model=space.model_key,
        config_hash=space.config_hash,
        native_dimension=space.dimension,
        index_dimension=generation.index_dimension,
        quantization=generation.quantization,
        index_backend=generation.index_backend,
        effective_backend=vector_index.serving_backend(generation),
        index_state=generation.index_state,
        index_error=generation.index_error,
        error_code=generation.error_code,
        processed=generation.processed,
        copied=generation.copied,
        truncated_count=generation.truncated_count,
        eligible=total,
        indexed=indexed,
        quarantined=quarantined,
        estimated_bytes=generation.estimated_bytes,
        job_id=generation.job_id,
        verified_at=ensure_utc(generation.verified_at)
        if generation.verified_at
        else None,
        retain_until=ensure_utc(generation.retain_until)
        if generation.retain_until
        else None,
        created_at=ensure_utc(generation.created_at),
        last_activity_at=ensure_utc(generation.last_activity_at)
        if generation.last_activity_at
        else None,
        eta_seconds=work_seconds(generation, max(0, total - indexed))
        if generation.state == "building" and generation.phase == "backfill"
        else None,
    )


def list_generations(session: Session) -> list[GenerationRead]:
    rows = session.exec(
        select(IndexGeneration)
        .where(IndexGeneration.version_token.is_not(None))
        .order_by(IndexGeneration.id.desc())
        .limit(100)
    ).all()
    return [read(session, row) for row in rows]


def work_seconds(generation: IndexGeneration, remaining: int) -> int | None:
    """A measured average, including local overhead, rather than a model-size guess."""
    if not generation.last_activity_at or generation.processed <= 0:
        return None
    elapsed = (
        ensure_utc(generation.last_activity_at) - ensure_utc(generation.created_at)
    ).total_seconds()
    if elapsed <= 0:
        return None
    return max(0, round(remaining * elapsed / generation.processed))


def occupied_bytes(session: Session) -> int:
    from app.modules.search.expansion import occupied_bytes as sparse_bytes

    return (
        sparse_bytes(session)
        + session.exec(
            select(
                func.coalesce(
                    func.sum(func.length(PassageVector.vector_blob) * 3 + 1024), 0
                )
            )
        ).one()
    )


def estimate_bytes(passages: int, dimension: int) -> int:
    return (passages + 128) * (dimension * 4 * 3 + 1024) + 1024**2


def source_units(space: Space, count: int) -> int:
    return (
        count * visual_sources.units_per_file(visual_sources.recipe_for(space))
        if space.profile in visual_sources.PROFILES
        else count
    )


def estimate(session: Session, proposal: GenerationProposal) -> GenerationEstimate:
    space = proposal_space(session, proposal)
    transform_backend(session, proposal, space)
    total = session.exec(
        select(func.count()).select_from(eligible(session, space).subquery())
    ).one()
    required = estimate_bytes(source_units(space, total), space.dimension)
    occupied = occupied_bytes(session)
    budget = configuration.settings(session).max_index_bytes
    prior = session.exec(
        select(IndexGeneration)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            EmbeddingSpace.config_hash == space.config_hash,
            IndexGeneration.processed > 0,
        )
        .order_by(IndexGeneration.id.desc())
        .limit(1)
    ).first()
    return GenerationEstimate(
        passages=total,
        estimated_bytes=required,
        existing_bytes=occupied,
        budget_bytes=budget,
        fits_budget=required + occupied <= budget,
        estimated_seconds=work_seconds(prior, total) if prior else None,
    )


def require(
    session: Session, generation_id: int, version_token: str
) -> IndexGeneration:
    row = session.get(IndexGeneration, generation_id, populate_existing=True)
    if row is None or row.version_token is None:
        raise OperationError("search_generation_not_found", kind=ErrorKind.NOT_FOUND)
    if not secrets.compare_digest(row.version_token, version_token):
        raise OperationError("search_proposal_changed", kind=ErrorKind.CONFLICT)
    return row


def _locked_require(
    session: Session, generation_id: int, version_token: str
) -> IndexGeneration:
    # Acquire a write lock on both supported databases before reading state.
    # A concurrent activation must finish before cancellation/retry is admitted.
    session.exec(
        update(IndexGeneration)
        .where(IndexGeneration.id == generation_id)
        .values(last_activity_at=IndexGeneration.last_activity_at)
    )
    return require(session, generation_id, version_token)


def resources(
    session: Session, space: Space, estimated_bytes: int
) -> list[CapacityResource]:
    # Include existing native floats, row overhead and derived-index allowance.
    occupied = occupied_bytes(session)
    available = max(0, configuration.settings(session).max_index_bytes - occupied)
    result = [
        CapacityResource.for_budget(
            "search-index", estimated_bytes, available, role="search index generations"
        )
    ]
    url = session.get_bind().url
    if url.get_backend_name() == "sqlite" and url.database not in {
        None,
        "",
        ":memory:",
    }:
        result.append(
            CapacityResource.for_path(
                Path(url.database), estimated_bytes, role="search index generations"
            )
        )
    return result


def prepare(
    session: Session, actor: User, proposal: GenerationProposal
) -> GenerationRead:
    actor = session.get(User, actor.id, populate_existing=True)
    if actor is None or not actor.is_active or not actor.is_superuser:
        raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    if not configuration.settings(session).enabled:
        raise OperationError("search_ai_disabled", kind=ErrorKind.CONFLICT)
    from app.modules.inference import model_cache

    if (
        proposal.local_model_id
        and not configuration.settings(session).local_models_enabled
    ):
        raise OperationError("embedding_local_disabled", kind=ErrorKind.CONFLICT)
    with model_cache.cache_lock() if proposal.local_model_id else nullcontext():
        space = proposal_space(session, proposal)
        if (
            space.profile == "point_cloud"
            and session.exec(
                select(IndexGeneration.id)
                .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
                .where(
                    col(EmbeddingSpace.profile).in_(("thumbnail", "multiview")),
                    IndexGeneration.state == "active",
                )
                .limit(1)
            ).first()
            is None
        ):
            raise OperationError(
                "search_visual_fallback_required", kind=ErrorKind.CONFLICT
            )
        result = _prepare_space(session, actor, proposal, space)
        if (
            space.profile == "multiview"
            and session.exec(
                select(IndexGeneration.id)
                .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
                .where(
                    EmbeddingSpace.profile == "thumbnail",
                    col(IndexGeneration.state).in_(("active", "building")),
                )
                .limit(1)
            ).first()
            is None
        ):
            # A multiview proposal also prepares a real serving fallback. Both
            # builds retain independent leases, states and atomic activation.
            reservation_id = session.get(IndexGeneration, result.id).reservation_id
            try:
                fallback = proposal.model_copy(
                    update={"profile": "thumbnail", "aggregation": "mean"}
                )
                _prepare_space(
                    session, actor, fallback, proposal_space(session, fallback)
                )
            except Exception:
                session.rollback()
                if reservation_id:
                    CapacityManager(get_session_factory()).release(reservation_id)
                raise
        if proposal.local_model_id:
            # Keep the reference pin until the new generation is durable.
            session.commit()
        return result


def proposal_space(session: Session, proposal: GenerationProposal) -> Space:
    """Resolve an immutable proposal without inference, jobs or database writes."""
    passage_version = proposal.passage_recipe_version or (
        2
        if configuration.settings(session).captions_enabled
        or session.exec(select(SubjectCaption.id).limit(1)).first() is not None
        else 1
    )
    if proposal.local_model_id:
        from app.modules.inference import model_cache
        from app.modules.inference.manifest import (
            LocalModelManifest,
            PointModelManifest,
            TextModelManifest,
        )

        model = model_cache.resolve(proposal.local_model_id)
        if proposal.profile == "point_cloud":
            if not isinstance(model.manifest, PointModelManifest):
                raise OperationError(
                    "embedding_alignment_unavailable", kind=ErrorKind.INVALID
                )
            return model.manifest.space()
        if proposal.profile in visual_sources.PROFILES:
            if (
                not isinstance(model.manifest, LocalModelManifest)
                or model.manifest.family != "clip"
            ):
                raise OperationError(
                    "embedding_alignment_unavailable", kind=ErrorKind.INVALID
                )
            return VisualRecipe.space(
                model.manifest.space(),
                image_size=model.manifest.image.image_size,
                profile=proposal.profile,
                aggregation=proposal.aggregation,
            )
        if not isinstance(model.manifest, TextModelManifest):
            raise OperationError("embedding_text_unavailable", kind=ErrorKind.INVALID)
        original = model.manifest.space()
        recipe = replace(
            TextRecipe.for_space(original),
            passage_version=passage_version,
        )
        space = replace(
            original,
            render_recipe=recipe.encode(),
            query_prefix=proposal.query_prefix
            if proposal.query_prefix is not None
            else original.query_prefix,
            document_prefix=proposal.document_prefix
            if proposal.document_prefix is not None
            else original.document_prefix,
        )
        return space
    endpoint_row = session.get(InferenceEndpoint, proposal.endpoint_id)
    if endpoint_row is None or endpoint_row.kind != "embedding":
        raise OperationError("inference_endpoint_unavailable", kind=ErrorKind.INVALID)
    endpoint = load_endpoint(endpoint_row)
    model_capabilities = capabilities_for_identity(
        endpoint.model_repo, endpoint.revision, endpoint_row.native_dimension
    )
    recipe = TextRecipe(passage_version, endpoint.max_input_characters)
    space = Space(
        model_key=endpoint.model,
        model_revision=endpoint.revision,
        dimension=endpoint_row.native_dimension,
        modality="text",
        render_recipe=recipe.encode(),
        provider="openai_compatible",
        profile=proposal.profile,
        query_prefix=proposal.query_prefix
        if proposal.query_prefix is not None
        else model_capabilities.query_prefix
        if model_capabilities
        else "",
        document_prefix=proposal.document_prefix
        if proposal.document_prefix is not None
        else model_capabilities.document_prefix
        if model_capabilities
        else "",
        provider_config_hash=endpoint.identity,
        model_repo=endpoint.model_repo,
    )
    return space


def transform_backend(
    session: Session, proposal: GenerationProposal, space: Space
) -> tuple[IndexTransform, str]:
    if space.profile in visual_sources.PROFILES:
        visual_sources.recipe_for(space)
    else:
        recipe = TextRecipe.for_space(space)
        if len(space.document_prefix) >= recipe.max_input_characters:
            raise OperationError("search_prefix_exceeds_budget", kind=ErrorKind.INVALID)
    dimension = proposal.index_dimension or space.dimension
    capabilities = capabilities_for(space)
    transform = IndexTransform.approved(
        space.dimension,
        dimension,
        proposal.quantization,
        mrl_dimensions=capabilities.mrl_dimensions if capabilities else (),
    )
    backend = proposal.index_backend
    dialect = session.get_bind().dialect.name
    if backend == "auto":
        backend = "sqlite_vec" if dialect == "sqlite" else "pgvector"
    if (backend == "sqlite_vec" and dialect != "sqlite") or (
        backend == "pgvector" and dialect != "postgresql"
    ):
        raise OperationError("search_backend_incompatible", kind=ErrorKind.INVALID)
    return transform, backend


def _prepare_space(
    session: Session,
    actor: User,
    proposal: GenerationProposal,
    space: Space,
) -> GenerationRead:
    transform, backend = transform_backend(session, proposal, space)
    dimension = transform.index_dimension
    key = f"{space.modality}/{space.profile}"
    active = session.exec(
        select(IndexGeneration).where(IndexGeneration.active_profile_key == key)
    ).first()
    total = session.exec(
        select(func.count()).select_from(eligible(session, space).subquery())
    ).one()
    estimate = estimate_bytes(source_units(space, total), space.dimension)
    version = secrets.token_hex(16)
    reservation_id = "search-generation:" + version
    manager = CapacityManager(get_session_factory())
    reservation = manager.reserve(
        reservation_id, resources(session, space, estimate), durable=True
    )
    try:
        stored_space = vector_store.register_space(session, space)
        generation = IndexGeneration(
            space_id=stored_space.id,
            state="building",
            phase="reconcile",
            building_profile_key=key,
            version_token=version,
            actor_id=actor.id,
            replaces_generation_id=active.id if active else None,
            index_dimension=dimension,
            quantization=proposal.quantization,
            transform_json=transform.metadata(),
            index_backend=backend,
            reservation_id=reservation_id,
            estimated_bytes=estimate,
            last_activity_at=utcnow(),
            auto_activate=proposal.auto_activate,
        )
        session.add(generation)
        session.flush()  # Unique building/profile rejects a competing proposal here.
        generation.job_id = registry.create(actor.id, kind="ai_search", session=session)
        vector_index.prepare(session, generation)
        session.add(generation)
        audit.record(
            session,
            action="search_generation_proposed",
            resource_type="search_generation",
            resource_id=generation.id,
            diff={
                "space": space.config_hash,
                "profile": space.profile,
                "backend": backend,
            },
        )
    except Exception as exc:
        session.rollback()
        reservation.release()
        if isinstance(exc, IntegrityError):
            raise OperationError(
                "search_generation_building", kind=ErrorKind.CONFLICT
            ) from None
        raise
    return read(session, generation)


def _lock_cutover(session: Session, generation_id: int) -> None:
    # PostgreSQL table SHARE locks drain in-flight passage/vector writes and
    # prevent new ones until the flip commits. Readers remain unblocked. SQLite
    # takes its ordinary write transaction. No inference happens under this lock.
    begin_write(session)
    if session.get_bind().dialect.name == "postgresql":
        session.exec(
            text(
                "LOCK TABLE models, files, search_passages, passage_vectors IN SHARE MODE"
            )
        )
    else:
        # BEGIN is deferred on SQLite. Take its writer lock before verification
        # establishes a read snapshot, which cannot be upgraded after another
        # connection commits in WAL mode. This stages no metadata change.
        session.exec(
            update(IndexGeneration)
            .where(IndexGeneration.id == generation_id)
            .values(last_activity_at=IndexGeneration.last_activity_at)
        )


def verify_counts(session: Session, generation: IndexGeneration) -> tuple[int, int]:
    total, indexed, quarantined = counts(session, generation)
    if total != indexed or quarantined:
        raise OperationError("search_generation_incomplete", kind=ErrorKind.CONFLICT)
    return total, indexed


def activate(
    session: Session, generation_id: int, version_token: str
) -> GenerationRead:
    candidate = require(session, generation_id, version_token)
    locked_ids = [candidate.id] + (
        [candidate.replaces_generation_id] if candidate.replaces_generation_id else []
    )
    session.exec(
        select(IndexGeneration)
        .where(col(IndexGeneration.id).in_(locked_ids))
        .order_by(IndexGeneration.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    _lock_cutover(session, generation_id)
    generation = require(session, generation_id, version_token)
    if not configuration.settings(session).enabled:
        raise OperationError("search_ai_disabled", kind=ErrorKind.CONFLICT)
    if (
        generation.state != "building"
        or generation.phase != "ready"
        or generation.verified_at is None
        or generation.cancel_requested
    ):
        raise OperationError("search_generation_not_ready", kind=ErrorKind.CONFLICT)
    if (
        generation.lease_token
        and generation.lease_expires_at
        and ensure_utc(generation.lease_expires_at) > utcnow()
    ):
        raise OperationError("search_generation_busy", kind=ErrorKind.CONFLICT)
    verify_counts(session, generation)
    key = generation.building_profile_key
    active = session.exec(
        select(IndexGeneration).where(IndexGeneration.active_profile_key == key)
    ).first()
    if (active.id if active else None) != generation.replaces_generation_id:
        raise OperationError("search_active_changed", kind=ErrorKind.CONFLICT)
    now = utcnow()
    if active:
        active.state = "retired"
        active.phase = "draining"
        active.active_profile_key = None
        active.retired_at = now
        active.retain_until = now + timedelta(
            hours=configuration.settings(session).rollback_retention_hours
        )
        session.add(active)
        session.flush()
    generation.state = "active"
    generation.phase = "ready"
    generation.building_profile_key = None
    generation.active_profile_key = key
    generation.activated_at = now
    session.add(generation)
    audit.record(
        session,
        action="search_generation_activated",
        resource_type="search_generation",
        resource_id=generation.id,
        diff={"replaced": active.id if active else None},
    )
    if generation.reservation_id:
        CapacityManager(get_session_factory()).release(generation.reservation_id)
    if generation.job_id:
        registry.update(
            generation.job_id,
            state="completed",
            processed=generation.processed,
            progress=100,
            result={"generation_id": generation.id},
        )
    return read(session, generation)


def cancel(session: Session, generation_id: int, version_token: str) -> GenerationRead:
    row = _locked_require(session, generation_id, version_token)
    if row.state != "building":
        raise OperationError("search_generation_not_building", kind=ErrorKind.CONFLICT)
    row.cancel_requested = True
    row.state = "cancelled"
    row.phase = "cancelled"
    row.building_profile_key = None
    row.lease_token = None
    row.lease_expires_at = None
    row.retired_at = utcnow()
    row.retain_until = utcnow() + timedelta(seconds=READER_SECONDS)
    session.add(row)
    audit.record(
        session,
        action="search_generation_cancelled",
        resource_type="search_generation",
        resource_id=row.id,
    )
    if row.job_id:
        registry.update(
            row.job_id,
            state="failed",
            error="search_generation_cancelled",
            retryable=False,
            result={"state": "cancelled", "generation_id": row.id},
        )
    return read(session, row)


def retry_quarantine(
    session: Session, generation_id: int, version_token: str
) -> GenerationRead:
    row = _locked_require(session, generation_id, version_token)
    if row.state not in {"active", "building"}:
        raise OperationError("search_generation_unavailable", kind=ErrorKind.CONFLICT)
    session.exec(
        delete(SearchIndexFailure).where(SearchIndexFailure.generation_id == row.id)
    )
    row.phase = "backfill"
    row.error_code = None
    row.verified_at = None
    session.add(row)
    audit.record(
        session,
        action="search_quarantine_retried",
        resource_type="search_generation",
        resource_id=row.id,
    )
    return read(session, row)


def pin(session: Session, generation_id: int) -> str:
    token = secrets.token_hex(24)
    # Lock the generation row before the lease insert so retirement/prune cannot
    # race a reader admitted to the old active generation.
    result = session.connection().execute(
        update(IndexGeneration)
        .where(IndexGeneration.id == generation_id, IndexGeneration.state == "active")
        .values(last_activity_at=IndexGeneration.last_activity_at)
    )
    if result.rowcount != 1:
        raise EmbeddingError("search_generation_changed")
    session.add(
        SearchGenerationLease(
            token=token,
            generation_id=generation_id,
            expires_at=utcnow() + timedelta(seconds=READER_SECONDS),
        )
    )
    session.commit()
    return token


def unpin(session: Session, token: str) -> None:
    session.exec(
        delete(SearchGenerationLease).where(SearchGenerationLease.token == token)
    )
    session.commit()


def prune_one(session: Session) -> bool:
    now = utcnow()
    session.exec(
        delete(SearchGenerationLease).where(SearchGenerationLease.expires_at <= now)
    )
    row = session.exec(
        select(IndexGeneration)
        .where(
            col(IndexGeneration.state).in_(("retired", "cancelled", "failed")),
            IndexGeneration.version_token.is_not(None),
            IndexGeneration.retain_until <= now,
            ~select(SearchGenerationLease.token)
            .where(
                SearchGenerationLease.generation_id == IndexGeneration.id,
                SearchGenerationLease.expires_at > now,
            )
            .exists(),
        )
        .order_by(IndexGeneration.id)
        .limit(1)
        .with_for_update()
    ).first()
    if row is None:
        session.commit()
        return False
    vector_index.drop(session, row)
    ids = session.exec(
        select(PassageVector.id)
        .where(PassageVector.generation_id == row.id)
        .order_by(PassageVector.id)
        .limit(128)
    ).all()
    if ids:
        session.exec(delete(PassageVector).where(col(PassageVector.id).in_(ids)))
        row.phase = "pruning"
    else:
        row.phase = "pruned"
        row.retain_until = None
        session.exec(
            delete(SearchIndexFailure).where(SearchIndexFailure.generation_id == row.id)
        )
        session.exec(
            delete(SearchReconciliationState).where(
                col(SearchReconciliationState.subject_type).like(f"g{row.id}:%")
            )
        )
    session.add(row)
    session.commit()
    if not ids and row.reservation_id:
        CapacityManager(get_session_factory()).release(row.reservation_id)
    return True


def ensure_caption_recipe(session: Session) -> bool:
    """One automatic v2 blue/green proposal; failed builds remain reviewable.

    Existing v1 vectors keep serving. An explicit administrator rollback is
    respected: a previous v2 proposal prevents repeated automatic proposals.
    """
    config = session.get(SystemConfig, 1)
    if config is None or not configuration.settings(session).enabled:
        return False
    if (
        not configuration.settings(session).captions_enabled
        and session.exec(select(SubjectCaption.id).limit(1)).first() is None
    ):
        return False
    generations = (
        select(IndexGeneration)
        .join(EmbeddingSpace)
        .where(EmbeddingSpace.profile == "semantic_text")
    )
    if (
        session.exec(
            generations.where(
                (IndexGeneration.state == "building")
                | EmbeddingSpace.recipe_json.contains('"passage_version":2')
            ).limit(1)
        ).first()
        is not None
    ):
        return False
    active = session.exec(
        generations.where(IndexGeneration.state == "active").limit(1)
    ).first()
    if active is None:
        return False
    actor_id = config.ai_search_configured_by or active.actor_id
    actor = session.get(User, actor_id) if actor_id else None
    if actor is None or not actor.is_active or not actor.is_superuser:
        return False
    space = contract(session, active)
    provider = {}
    if space.provider == "onnx_cpu":
        if not configuration.settings(session).local_models_enabled:
            return False
        from app.modules.inference import model_cache
        from app.modules.inference.manifest import TextModelManifest

        recipe = TextRecipe.for_space(space)
        model = next(
            (
                entry
                for entry in model_cache.inventory()
                if isinstance(entry.manifest, TextModelManifest)
                and TextRecipe.for_space(entry.manifest.space()).encoder_manifest_sha256
                == recipe.encoder_manifest_sha256
            ),
            None,
        )
        if model is None:
            return False
        provider["local_model_id"] = model.id
    else:
        endpoint = session.exec(
            select(InferenceEndpoint).where(
                InferenceEndpoint.config_hash == space.provider_config_hash
            )
        ).first()
        if endpoint is None:
            return False
        provider["endpoint_id"] = endpoint.id
    proposal = GenerationProposal(
        **provider,
        passage_recipe_version=2,
        query_prefix=space.query_prefix,
        document_prefix=space.document_prefix,
        index_backend=active.index_backend,
        index_dimension=active.index_dimension,
        quantization=active.quantization,
    )
    prepare(session, actor, proposal)
    return True
