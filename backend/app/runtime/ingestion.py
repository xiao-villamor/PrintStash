"""Local dispatcher for durable imports; blocking SQL never owns the event loop."""

import asyncio
from contextlib import suppress

from app.db.session import get_session_factory
from app.modules.ingestion import command_executor, commands
from app.modules.storage.storage_backend import generations
from app.runtime import maintenance
from app.runtime.jobs import registry
from app.runtime.workers import run_unit


async def process_one(*, enrichment: bool = False) -> bool:
    if not maintenance.begin_mutating_operation():
        return False
    sessions = get_session_factory()

    async def transaction(operation, *args, **kwargs):
        def run():
            with sessions.scoped_session() as session:
                return operation(session, *args, **kwargs)

        return await run_unit(run)

    def fail(claim, reason):
        with commands.execution_scope(claim):
            registry.finish(claim.job_id, state="failed", error=reason, retryable=True)

    try:
        with generations.use(generations.pin()):
            claim = await transaction(commands.claim_next, enrichment=enrichment)
            if claim is None:
                return False

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(commands.LEASE_SECONDS / 3)
                    if not await transaction(commands.renew, claim):
                        return

            pulse = asyncio.create_task(heartbeat())
            try:
                await command_executor.execute(claim, sessions)
            except commands.DependencyPending:
                await transaction(commands.defer, claim, waiting=True)
            except commands.CommandDeferred:
                if not await transaction(commands.defer, claim):
                    await asyncio.to_thread(fail, claim, "capture_enrichment_failed")
            except Exception as error:
                from app.modules.ingestion.importer import ImportError_

                reason = (
                    str(error)
                    if isinstance(error, ImportError_)
                    else "ingestion_command_failed"
                )
                try:
                    await asyncio.to_thread(fail, claim, reason)
                except RuntimeError as exc:
                    if str(exc) != "ingestion_claim_lost":
                        raise
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse
                await transaction(commands.release, claim)
            return True
    finally:
        maintenance.end_mutating_operation()


async def run_ingestion() -> None:
    from app.runtime.workers import run_async_worker

    await run_async_worker(process_one, idle_seconds=0.25)
