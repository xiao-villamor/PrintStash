"""Optional index-time sparse inference under the shared local compute budget."""

import asyncio

from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.search.expansion_worker import ExpansionProcessor
from app.runtime import maintenance
from app.runtime.workers import run_unit

logger = get_logger(__name__)


def process_one():
    if not maintenance.begin_mutating_operation():
        return False
    try:
        return ExpansionProcessor(get_session_factory()).work_one()
    finally:
        maintenance.end_mutating_operation()


async def run_expansion():
    while True:
        worked = False
        try:
            worked = await run_unit(process_one)
        except Exception:
            logger.warning("Sparse expansion paused; retrying a bounded unit")
        await asyncio.sleep(0 if worked else 5)
