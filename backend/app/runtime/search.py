"""Bounded local projection repair, coordinated with database maintenance."""

from __future__ import annotations

import asyncio
from functools import partial
from itertools import cycle

from printstash_core.inference import EmbeddingError
from printstash_core.search.passages import SubjectType

from app.core.errors import OperationError
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.inference import model_cache
from app.modules.inference.worker_pool import pool as model_workers
from app.modules.search.generations import ensure_caption_recipe
from app.modules.search.indexing import IndexProcessor
from app.modules.search.lexical_index import rebuild_partition
from app.modules.search.projection import process_pending
from app.modules.search.reconciliation import reconcile_partition
from app.modules.search.vector_index import repair_partition as repair_vectors
from app.runtime import maintenance

logger = get_logger(__name__)


def process_one(kind: SubjectType) -> int:
    if (
        maintenance.foreground_mutations_pending()
        or not maintenance.begin_mutating_operation()
    ):
        return 0
    try:
        with get_session_factory().scoped_session() as session:
            model_workers.prune_idle(
                tuple(
                    model.directory
                    for model in model_cache.inventory()
                    if model_cache.referenced(session, model)
                )
            )
            # Projection repair shares SQLite's writer with interactive reader
            # leases. Keep these transactions short; embedding bursts have their
            # own separate batch budget below.
            changed = process_pending(session)
            if not changed:
                changed = reconcile_partition(session, kind, limit=1)
            rebuild_partition(session)
            repair_vectors(session)
            session.commit()
        with get_session_factory().scoped_session() as session:
            try:
                ensure_caption_recipe(session)
            except (OperationError, EmbeddingError):
                session.rollback()
                logger.debug("Caption recipe proposal deferred")
        IndexProcessor(get_session_factory()).work_one()
        return changed
    finally:
        maintenance.end_mutating_operation()


async def run_search() -> None:
    for kind in cycle(SubjectType):
        changed = 0
        try:
            changed = await _run_unit(partial(process_one, kind))
            # Repair runs once per tick. Drain at most 128 embedding inputs
            # (16 default batches), yielding between units and pausing between
            # bursts so a busy or unavailable provider cannot spin forever.
            for _ in range(15):
                if not await _run_unit(_index_one):
                    break
        except Exception:
            logger.warning("Search indexing paused; retrying a bounded repair unit")
        await asyncio.sleep(0 if changed else 1)


def _index_one() -> bool:
    if (
        maintenance.foreground_mutations_pending()
        or not maintenance.begin_mutating_operation()
    ):
        return False
    try:
        return IndexProcessor(get_session_factory()).work_one()
    finally:
        maintenance.end_mutating_operation()


async def _run_unit(operation):
    from app.runtime.workers import run_unit

    return await run_unit(operation)
