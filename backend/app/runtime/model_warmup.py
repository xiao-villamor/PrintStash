"""Local-only warm-up drains promptly on shutdown and honors restore admission."""

import asyncio
from functools import partial

from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.search.model_warmup import ModelWarmup
from app.runtime import maintenance
from app.runtime.workers import run_unit

logger = get_logger(__name__)


def process_one(processor):
    if not maintenance.begin_mutating_operation():
        return False
    try:
        return processor.work_one()
    finally:
        maintenance.end_mutating_operation()


async def run_model_warmup():
    processor = ModelWarmup(get_session_factory())
    try:
        while True:
            worked = False
            try:
                worked = await run_unit(
                    partial(process_one, processor), on_cancel=processor.stop
                )
            except Exception:
                logger.warning("Local model warm-up deferred")
            await asyncio.sleep(0 if worked else 1)
    finally:
        processor.stop()
