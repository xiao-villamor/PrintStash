"""One durable, bounded sparse-index lease; publication rechecks its authority."""

import secrets
from datetime import timedelta
from pathlib import Path

from printstash_core.inference import EmbeddingError, InferenceContext
from sqlalchemy import delete, or_, update
from sqlmodel import select

from app.core.config import settings
from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import (
    SearchExpansion,
    SearchExpansionTerm,
    SearchPassage,
    SystemConfig,
    User,
)
from app.modules.inference import model_cache, model_registry
from app.modules.inference.manifest import SparseModelManifest
from app.modules.inference.sparse import LocalSparseProvider
from app.modules.search import configuration, expansion
from app.modules.search.access import visible_passage_ids
from app.modules.search.lexical_index import canonical_passage
from app.modules.storage.capacity import CapacityManager, CapacityResource


def provider(sessions, identity):
    entry = model_registry.require(identity)
    model = model_cache.resolve(entry.id)
    if not isinstance(model.manifest, SparseModelManifest):
        raise EmbeddingError("embedding_sparse_required")
    return LocalSparseProvider(
        sessions,
        model.directory,
        model.manifest.model_key,
        settings.embedding_onnx_threads,
    )


def authority(session):
    recipe = expansion.identity(session)
    config = session.get(SystemConfig, 1, populate_existing=True)
    actor = (
        session.get(User, config.ai_search_configured_by, populate_existing=True)
        if config and config.ai_search_configured_by
        else None
    )
    if recipe is None or actor is None or not actor.is_active or not actor.is_superuser:
        return None
    return recipe, actor


class ExpansionProcessor:
    def __init__(self, sessions, *, provider_factory=provider):
        self.sessions, self.provider_factory = sessions, provider_factory

    def work_one(self):
        with self.sessions.scoped_session() as session:
            # Serialize only claim/publication transactions across processes.
            session.exec(
                update(SystemConfig)
                .where(SystemConfig.id == 1)
                .values(ai_search_configured_by=SystemConfig.ai_search_configured_by)
            )
            auth = authority(session)
            if auth is None:
                return False
            recipe, actor = auth
            actor_id, auth_version = actor.id, actor.auth_version
            now = utcnow()
            expired = session.exec(
                select(SearchExpansion.passage_id)
                .where(
                    SearchExpansion.phase == "running",
                    SearchExpansion.lease_until <= now,
                    SearchExpansion.attempts >= 3,
                )
                .limit(8)
            ).all()
            if expired:
                session.exec(
                    update(SearchExpansion)
                    .where(SearchExpansion.passage_id.in_(expired))
                    .values(
                        phase="failed",
                        token=None,
                        lease_until=None,
                        error_code="embedding_worker_expired",
                    )
                )
            passage = session.exec(
                select(SearchPassage)
                .outerjoin(
                    SearchExpansion, SearchExpansion.passage_id == SearchPassage.id
                )
                .where(
                    SearchPassage.id.in_(visible_passage_ids(session, actor)),
                    canonical_passage(),
                    or_(
                        SearchExpansion.passage_id.is_(None),
                        SearchExpansion.recipe != recipe,
                        SearchExpansion.input_hash != SearchPassage.content_hash,
                        (SearchExpansion.attempts < 3)
                        & or_(
                            (SearchExpansion.phase == "running")
                            & (SearchExpansion.lease_until <= now),
                            (SearchExpansion.phase == "failed")
                            & (SearchExpansion.retry_at <= now),
                        ),
                    ),
                )
                .order_by(SearchPassage.id)
                .limit(1)
            ).first()
            if passage is None:
                session.commit()
                return False
            row = session.get(SearchExpansion, passage.id)
            if (
                row is None
                or row.recipe != recipe
                or row.input_hash != passage.content_hash
            ):
                if row is None:
                    row = SearchExpansion(
                        passage_id=passage.id,
                        input_hash=passage.content_hash,
                        recipe=recipe,
                    )
                else:
                    row.input_hash, row.recipe, row.attempts = (
                        passage.content_hash,
                        recipe,
                        0,
                    )
            row.phase, row.token = "running", secrets.token_hex(24)
            row.attempts += 1
            row.lease_until, row.retry_at = now + timedelta(seconds=180), None
            row.updated_at, row.error_code = now, None
            session.add(row)
            passage_id, input_hash, token, text = (
                passage.id,
                passage.content_hash,
                row.token,
                passage.text,
            )
            session.commit()
        reservation = None
        try:
            with self.sessions.scoped_session() as session:
                from app.modules.search.generations import occupied_bytes

                available = max(
                    0,
                    configuration.settings(session).max_index_bytes
                    - occupied_bytes(session),
                )
                resources = [
                    CapacityResource.for_budget(
                        "search-index",
                        expansion.BYTES_PER_PASSAGE,
                        available,
                        role="sparse expansion",
                    )
                ]
                url = session.get_bind().url
                if url.get_backend_name() == "sqlite" and url.database not in {
                    None,
                    "",
                    ":memory:",
                }:
                    resources.append(
                        CapacityResource.for_path(
                            Path(url.database),
                            expansion.BYTES_PER_PASSAGE,
                            role="sparse expansion",
                        )
                    )
            reservation = CapacityManager(self.sessions).reserve(
                "search-sparse:" + token, resources
            )
            context = InferenceContext.bounded(
                120,
                priority="background",
                cancelled=lambda: (
                    not self.current(
                        recipe, actor_id, auth_version, passage_id, input_hash, token
                    )
                ),
            )
            context.remaining()
            result = self.provider_factory(self.sessions, recipe).expand(
                text, context=context
            )
            with self.sessions.scoped_session() as session:
                session.exec(
                    update(SystemConfig)
                    .where(SystemConfig.id == 1)
                    .values(
                        ai_search_configured_by=SystemConfig.ai_search_configured_by
                    )
                )
                if not self.matches(
                    session,
                    recipe,
                    actor_id,
                    auth_version,
                    passage_id,
                    input_hash,
                    token,
                ):
                    return True
                row = session.get(SearchExpansion, passage_id)
                session.exec(
                    delete(SearchExpansionTerm).where(
                        SearchExpansionTerm.passage_id == passage_id
                    )
                )
                session.add_all(
                    [
                        SearchExpansionTerm(
                            passage_id=passage_id, term=term.term, weight=term.weight
                        )
                        for term in result.terms
                    ]
                )
                row.phase, row.token, row.lease_until = "ready", None, None
                row.truncated, row.updated_at = result.truncated, utcnow()
                session.add(row)
                session.commit()
        except Exception as exc:
            # Persist only the approved error vocabulary, never passage text.
            code = (
                exc.code
                if isinstance(exc, EmbeddingError)
                or (
                    isinstance(exc, OperationError)
                    and exc.code == "storage_capacity_exceeded"
                )
                else "embedding_sparse_failed"
            )
            deferred = code in {
                "embedding_compute_busy",
                "inference_cancelled",
                "storage_capacity_exceeded",
            }
            with self.sessions.scoped_session() as session:
                session.exec(
                    update(SearchExpansion)
                    .where(
                        SearchExpansion.passage_id == passage_id,
                        SearchExpansion.token == token,
                    )
                    .values(
                        phase="failed",
                        token=None,
                        lease_until=None,
                        retry_at=utcnow() + timedelta(seconds=30),
                        attempts=SearchExpansion.attempts - 1
                        if deferred
                        else SearchExpansion.attempts,
                        error_code=code,
                        updated_at=utcnow(),
                    )
                )
                session.commit()
        finally:
            if reservation is not None:
                reservation.release()
        return True

    def current(self, *args):
        with self.sessions.scoped_session() as session:
            return self.matches(session, *args)

    @staticmethod
    def matches(session, recipe, actor_id, auth_version, passage_id, input_hash, token):
        auth = authority(session)
        if auth is None or (auth[0], auth[1].id, auth[1].auth_version) != (
            recipe,
            actor_id,
            auth_version,
        ):
            return False
        return (
            session.exec(
                select(SearchPassage.id)
                .join(SearchExpansion, SearchExpansion.passage_id == SearchPassage.id)
                .where(
                    SearchPassage.id == passage_id,
                    SearchPassage.content_hash == input_hash,
                    SearchPassage.id.in_(visible_passage_ids(session, auth[1])),
                    canonical_passage(),
                    SearchExpansion.input_hash == input_hash,
                    SearchExpansion.recipe == recipe,
                    SearchExpansion.token == token,
                    SearchExpansion.phase == "running",
                    SearchExpansion.lease_until > utcnow(),
                )
            ).first()
            is not None
        )
