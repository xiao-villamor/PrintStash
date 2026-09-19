"""Visual work uses the shared generation lease, vector store and compute budget."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import struct
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext
from printstash_core.mesh.similarity import GeometryError
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.visual_inputs import VisualRecipe, mean_pool
from sqlalchemy import Integer, cast, delete, literal, or_
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    EmbeddingSpace,
    File,
    IndexGeneration,
    Model,
    PassageVector,
    SearchIndexFailure,
    ThumbnailGeneration,
    ThumbnailGenerationState,
)
from app.db.scopes import live
from app.db.session import SessionFactory
from app.modules.inference.configuration import embedding_provider
from app.modules.inference.local import acquire_slot
from app.modules.media import compute_slots, visual_render
from app.modules.media.geometry_analysis import VisualViews, thumbnail_input
from app.modules.search import (
    configuration,
    generations,
    vector_index,
    vector_store,
    visual_sources,
)
from app.modules.storage import artifact_content
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.storage.storage_backend.runtime import get_backend


@dataclass(frozen=True)
class VisualSource:
    file_id: int
    model_id: int
    input_hash: str


def pending(session: Session, generation: IndexGeneration) -> VisualSource | None:
    space = generations.contract(session, generation)
    deferred = (
        select(SearchIndexFailure.id)
        .where(
            SearchIndexFailure.generation_id == generation.id,
            SearchIndexFailure.file_id == File.id,
            SearchIndexFailure.input_hash == File.sha256,
            or_(
                SearchIndexFailure.state == "quarantined",
                SearchIndexFailure.retry_after > utcnow(),
            ),
        )
        .exists()
    )
    statement = (
        select(File)
        .where(
            File.id.in_(visual_sources.missing(session, generation.id, space)),
            ~deferred,
        )
        .order_by(File.id)
        .limit(1)
    )
    row = session.exec(statement.where(File.id > generation.passage_after_id)).first()
    if row is None and generation.passage_after_id:
        row = session.exec(statement).first()
    return VisualSource(row.id, row.model_id, row.sha256) if row is not None else None


def copied_views(
    session: Session,
    generation: IndexGeneration,
    item: VisualSource,
    recipe: VisualRecipe | PointRecipe,
):
    if isinstance(recipe, PointRecipe):
        # Point recipes include both the export and sampling identity. Copy only
        # complete native units from the exact same Space.
        row = session.exec(
            select(PassageVector)
            .join(IndexGeneration, IndexGeneration.id == PassageVector.generation_id)
            .where(
                IndexGeneration.space_id == generation.space_id,
                IndexGeneration.id != generation.id,
                PassageVector.unit_kind == "point_cloud",
                PassageVector.unit_key == f"file:{item.file_id}:point",
                PassageVector.input_hash == item.input_hash,
                PassageVector.file_id == item.file_id,
            )
            .limit(1)
        ).first()
        if row is None:
            return None
        vector = struct.unpack(f"<{row.native_dimension}f", row.vector_blob)
        return (vector,), None
    candidates = session.exec(
        select(IndexGeneration, EmbeddingSpace)
        .join(EmbeddingSpace, EmbeddingSpace.id == IndexGeneration.space_id)
        .where(
            IndexGeneration.id != generation.id,
            EmbeddingSpace.profile.in_(visual_sources.PROFILES),
        )
        .order_by(IndexGeneration.id.desc())
        .limit(100)
    ).all()
    for prior, row in candidates:
        try:
            previous = VisualRecipe(**json.loads(row.recipe_json))
        except (TypeError, ValueError):
            continue
        thumbnail_reuse = (
            recipe.profile == "thumbnail"
            and previous.profile == "multiview"
            and (
                previous.encoder_space_hash == recipe.encoder_space_hash
                and previous.image_size == recipe.image_size
                and previous.version == recipe.version
            )
        )
        if previous.view_identity != recipe.view_identity and not thumbnail_reuse:
            continue
        vectors = session.exec(
            select(PassageVector)
            .where(
                PassageVector.generation_id == prior.id,
                PassageVector.file_id == item.file_id,
                PassageVector.input_hash == item.input_hash,
            )
            .order_by(PassageVector.unit_key)
        ).all()
        by_kind = {}
        for vector in vectors:
            by_kind.setdefault(vector.unit_kind, []).append(
                struct.unpack(f"<{row.native_dimension}f", vector.vector_blob)
            )
        if thumbnail_reuse and len(by_kind.get("visual_thumbnail", [])) == 1:
            thumbnail = by_kind["visual_thumbnail"][0]
            return (thumbnail,), thumbnail
        if recipe.profile == "thumbnail" and len(by_kind.get("visual_mean", [])) == 1:
            thumbnail = by_kind["visual_mean"][0]
            return (thumbnail,), thumbnail
        if (
            len(by_kind.get("visual_view", [])) == 6
            and len(by_kind.get("visual_thumbnail", [])) == 1
        ):
            return tuple(by_kind["visual_view"]), by_kind["visual_thumbnail"][0]
    return None


def render(
    sessions: SessionFactory,
    file: File,
    recipe: VisualRecipe | PointRecipe,
    context: InferenceContext,
):
    token = "search-render:" + secrets.token_hex(16)
    with sessions.scoped_session() as session:
        if recipe.profile == "thumbnail":
            cached = cached_thumbnail(session, file, recipe, context)
            if cached is not None:
                return VisualViews(cached, (cached,))
        slot = acquire_slot(session, token, context)
        try:
            with ExitStack() as resources:
                if file.file_type.value == "step":
                    temporary = CapacityManager(sessions).reserve(
                        token + ":step",
                        [
                            CapacityResource.for_path(
                                Path(tempfile.gettempdir()),
                                MAX_ANALYSIS_FACES * 96 + 1024 * 1024,
                                role="Search STEP tessellation",
                            )
                        ],
                    )
                    resources.callback(temporary.release)
                path = resources.enter_context(
                    artifact_content.resolve(file).materialize()
                )

                def check_source():
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        while block := stream.read(1024 * 1024):
                            context.remaining()
                            digest.update(block)
                    if digest.hexdigest() != file.sha256:
                        raise EmbeddingError("embedding_source_changed")

                check_source()
                result = visual_render.render(
                    path, file_type=file.file_type.value, recipe=recipe, context=context
                )
                check_source()
                return result
        finally:
            compute_slots.release(session, slot.id, token)
            session.commit()


def cached_thumbnail(session, file, recipe, context):
    row = session.exec(
        select(ThumbnailGeneration)
        .where(
            ThumbnailGeneration.file_id == file.id,
            ThumbnailGeneration.source_sha256 == file.sha256,
            ThumbnailGeneration.recipe_fingerprint == recipe.thumbnail_recipe,
            ThumbnailGeneration.state == ThumbnailGenerationState.READY,
            ThumbnailGeneration.complete.is_(True),
            ThumbnailGeneration.strategy == "full",
            ThumbnailGeneration.width == 640,
            ThumbnailGeneration.height == 480,
        )
        .limit(1)
    ).first()
    if (
        row is None
        or not row.storage_key
        or not row.output_size_bytes
        or row.output_size_bytes > 8 * 1024**2
    ):
        return None
    try:
        payload = bytearray()
        for chunk in get_backend().stream_chunks(row.storage_key, 64 * 1024):
            context.remaining()
            if len(payload) + len(chunk) > row.output_size_bytes:
                return None
            payload.extend(chunk)
        if (
            len(payload) != row.output_size_bytes
            or hashlib.sha256(payload).hexdigest() != row.output_sha256
        ):
            return None
        return thumbnail_input(bytes(payload), recipe.image_size)
    except (OSError, ValueError, EmbeddingError):
        return None


def publish(
    session: Session,
    generation_id: int,
    token: str,
    item: VisualSource,
    views,
    thumbnail,
    *,
    copied=False,
) -> bool:

    if (
        not generations.lock_owned(session, generation_id, token)
        or not configuration.settings(session).enabled
    ):
        return False
    generation = session.get(IndexGeneration, generation_id, populate_existing=True)
    space = generations.contract(session, generation)
    recipe = visual_sources.recipe_for(space)
    if len(views) != (1 if isinstance(recipe, PointRecipe) else recipe.view_count):
        raise EmbeddingError("embedding_view_count_mismatch")
    # Lock both source rows before the insert-from-select fence on PostgreSQL.
    current = session.exec(
        select(File)
        .join(Model, Model.id == File.model_id)
        .where(
            File.id == item.file_id,
            File.sha256 == item.input_hash,
            File.model_id == item.model_id,
            live(File),
            live(Model),
        )
        .with_for_update()
    ).first()
    if current is None:
        return False
    source = select(
        literal("model").label("subject_type"),
        File.model_id.label("subject_id"),
        File.model_id.label("model_id"),
        File.id.label("file_id"),
        cast(literal(None), Integer).label("passage_id"),
    ).where(
        File.id == item.file_id,
        File.sha256 == item.input_hash,
        File.model_id == item.model_id,
        File.id.in_(visual_sources.eligible(session)),
        select(IndexGeneration.id)
        .where(*generations.owned(generation_id, token))
        .exists(),
    )
    units = (
        [("point_cloud", "point", views[0])]
        if isinstance(recipe, PointRecipe)
        else [("visual_mean", "mean", mean_pool(tuple(views), space.dimension))]
    )
    if recipe.profile == "multiview":
        units += [("visual_view", f"view:{i}", v) for i, v in enumerate(views)]
        units.append(("visual_thumbnail", "thumbnail", thumbnail))
    # All views and the completion marker commit together; partial frames never
    # become a ready Model, even if a worker is killed during publication.
    with session.begin_nested():
        for kind, suffix, vector in units:
            if not vector_store.publish(
                session,
                generation_id=generation_id,
                space=space,
                unit_kind=kind,
                unit_key=f"file:{item.file_id}:{suffix}",
                input_hash=item.input_hash,
                vector=vector,
                source=source,
            ):
                raise EmbeddingError("embedding_source_changed")
        session.exec(
            delete(SearchIndexFailure).where(
                SearchIndexFailure.generation_id == generation_id,
                SearchIndexFailure.file_id == item.file_id,
            )
        )
        generation.processed += 1
        generation.copied += int(copied)
        generation.passage_after_id = item.file_id
        generation.verified_at = None
        generation.phase = "backfill"
        generation.error_code = None
        session.add(generation)
    session.commit()
    return True


def record_failure(session, generation_id, token, item, code):

    if not generations.lock_owned(session, generation_id, token):
        return
    file = session.get(File, item.file_id, populate_existing=True)
    if file is None or file.sha256 != item.input_hash:
        return
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) is None:
        code = "embedding_render_failed"
    failure = session.exec(
        select(SearchIndexFailure).where(
            SearchIndexFailure.generation_id == generation_id,
            SearchIndexFailure.file_id == item.file_id,
            SearchIndexFailure.input_hash == item.input_hash,
        )
    ).first()
    if failure is None:
        failure = SearchIndexFailure(
            generation_id=generation_id,
            file_id=item.file_id,
            input_hash=item.input_hash,
            error_code=code,
        )
    failure.attempts += 1
    failure.error_code = code
    failure.state = "quarantined" if failure.attempts >= 3 else "retry"
    failure.retry_after = utcnow() + timedelta(seconds=min(60, 2**failure.attempts))
    session.add(failure)
    generation = session.get(IndexGeneration, generation_id, populate_existing=True)
    generation.passage_after_id = item.file_id
    generation.error_code = code
    session.add(generation)
    session.commit()


def process(
    sessions: SessionFactory, generation_id: int, token: str, context: InferenceContext
):
    from app.modules.storage.capacity import CapacityManager, CapacityReservationHandle

    with sessions.scoped_session() as session:
        generation = session.get(IndexGeneration, generation_id)
        space = generations.contract(session, generation)
        recipe = visual_sources.recipe_for(space)
        item = pending(session, generation)
        if item is None:
            if not generations.lock_owned(session, generation_id, token):
                return
            total, indexed, quarantine = visual_sources.counts(
                session, generation_id, space
            )
            if total != indexed:
                generation.phase = "verify_failed" if quarantine else "backfill"
                generation.error_code = (
                    "search_generation_incomplete"
                    if quarantine
                    else generation.error_code
                )
            elif generation.index_state == "building":
                vector_index.rebuild_partition(session, generation)
            elif generation.state == "active" and generation.phase == "ready":
                return
            else:
                # The exact encoder is canary-checked even for an empty library.
                provider = embedding_provider(session, space)
                session.rollback()
                provider.validate(context=context)
                if not generations.lock_owned(session, generation_id, token):
                    return
                generation = session.get(
                    IndexGeneration, generation_id, populate_existing=True
                )
                generations._lock_cutover(session, generation_id)
                generations.verify_counts(session, generation)
                sample = session.exec(
                    select(PassageVector)
                    .where(
                        PassageVector.id.in_(
                            visual_sources.current_vectors(
                                session, generation_id, space
                            )
                        )
                    )
                    .order_by(PassageVector.id)
                    .limit(1)
                ).first()
                if sample is not None:
                    result = vector_store.query(
                        session,
                        generation_id=generation_id,
                        space=space,
                        vector=struct.unpack(
                            f"<{space.dimension}f", sample.vector_blob
                        ),
                        allowed_ids=select(PassageVector.id).where(
                            PassageVector.id == sample.id
                        ),
                        limit=1,
                        states=("building", "active"),
                    )
                    if not result.items or result.items[0].score < 0.99:
                        raise EmbeddingError("search_smoke_failed")
                generation.phase = "ready"
                generation.verified_at = utcnow()
                generation.verified_count = total
                generation.error_code = None
            session.add(generation)
            session.commit()
            return
        copied = copied_views(session, generation, item, recipe)
        file = session.get(File, item.file_id)
        session.expunge(file)
        provider = embedding_provider(session, space)
        total, indexed, _ = visual_sources.counts(session, generation_id, space)
        capacity = generations.resources(
            session,
            space,
            generations.estimate_bytes(
                (total - indexed) * visual_sources.units_per_file(recipe),
                space.dimension,
            ),
        )
        manager = CapacityManager(sessions)
        if generation.state == "building" and generation.reservation_id:
            CapacityReservationHandle(
                manager, generation.reservation_id, capacity, ()
            ).renew(capacity)
            reservation = None
        else:
            reservation = manager.reserve(
                f"search-visual:{generation_id}:{token}", capacity
            )
    try:
        if copied is not None:
            views, thumbnail = copied
        else:
            rendered = render(sessions, file, recipe, context)
            inputs = rendered.views + (
                (rendered.thumbnail,) if recipe.profile == "multiview" else ()
            )
            vectors = provider.embed(inputs, space, context=context)
            views = vectors[
                : 1 if isinstance(recipe, PointRecipe) else recipe.view_count
            ]
            thumbnail = vectors[-1] if recipe.profile == "multiview" else vectors[0]
        context.remaining()
        with sessions.scoped_session() as session:
            publish(
                session,
                generation_id,
                token,
                item,
                views,
                thumbnail,
                copied=copied is not None,
            )
    except (
        EmbeddingError,
        GeometryError,
        artifact_content.ArtifactContentError,
    ) as exc:
        code = getattr(exc, "code", "embedding_source_unavailable")
        if code in {"embedding_compute_busy", "inference_cancelled"}:
            raise EmbeddingError(code) from None
        with sessions.scoped_session() as session:
            record_failure(session, generation_id, token, item, code)
    finally:
        if reservation is not None:
            reservation.release()
