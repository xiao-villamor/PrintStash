"""Maintenance-aware local execution of durable Artifact enrichment."""

from app.core.errors import ErrorKind, OperationError
from app.db.session import get_session_factory
from app.modules.media.enrichment import EnrichmentProcessor
from app.modules.storage.storage_backend import generations
from app.runtime import maintenance
from app.runtime.workers import run_unit


def process_one() -> bool:
    if (
        maintenance.foreground_mutations_pending()
        or not maintenance.begin_mutating_operation()
    ):
        return False
    try:
        from app.runtime.work_priority import defer_background

        with get_session_factory().scoped_session() as session:
            if defer_background(session, "enrichment"):
                return False
        with generations.use(generations.pin()):
            from app.modules.storage.storage_backend.runtime import get_backend

            return EnrichmentProcessor(
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


async def run_enrichment() -> None:
    from app.runtime.ingestion import process_one as process_command
    from app.runtime.workers import run_async_worker

    async def turn() -> bool:
        worked = await run_unit(process_one)
        covered = await process_command(enrichment=True)
        return worked or covered

    await run_async_worker(turn)
