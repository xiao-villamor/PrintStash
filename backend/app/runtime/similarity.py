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

logger = get_logger(__name__)


def process_one() -> bool:
    if not maintenance.begin_mutating_operation():
        return False
    try:
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
        unit = asyncio.create_task(asyncio.to_thread(process_one))
        try:
            await asyncio.shield(unit)
        except asyncio.CancelledError:
            # Let a bounded unit release its sessions/retention/capacity before
            # lifecycle teardown fences the database or closes storage clients.
            await unit
            raise
        except Exception:
            logger.warning("Similarity analysis paused; inspect its run status")
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=1)
        except TimeoutError:
            pass
