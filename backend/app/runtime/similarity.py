"""Local scheduler hint plus durable scan units that yield to restore and GC."""

from __future__ import annotations

import asyncio

from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.storage.storage_backend.runtime import get_backend
from app.runtime import maintenance
from app.runtime.work_wakeup import WorkWakeup
from app.runtime.workers import run_unit

logger = get_logger(__name__)


def process_one() -> bool:
    if (
        maintenance.foreground_mutations_pending()
        or not maintenance.begin_mutating_operation()
    ):
        return False
    try:
        from app.runtime.work_priority import defer_background

        with get_session_factory().scoped_session() as session:
            if defer_background(session, "similarity"):
                return False
        return SimilarityProcessor(
            get_session_factory(),
            get_backend(),
            retain_storage=maintenance.retain_storage_objects,
        ).work_one()
    except OperationError as exc:
        if exc.kind is ErrorKind.BUSY:
            return False
        raise
    finally:
        maintenance.end_mutating_operation()


async def run_similarity(wakeup: WorkWakeup) -> None:
    while True:
        worked = False
        try:
            worked = await run_unit(process_one)
        except Exception:
            logger.warning("Similarity analysis paused; inspect its run status")
        if worked:
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=1)
        except TimeoutError:
            pass
