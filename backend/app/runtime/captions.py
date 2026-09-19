"""Separate optional VLM lane; slow captions never block passage/vector repair."""

import asyncio

from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.search.caption_worker import CaptionProcessor
from app.runtime import maintenance
from app.runtime.workers import run_unit

logger = get_logger(__name__)


def process_one():
    if not maintenance.begin_mutating_operation():
        return False
    try:
        return CaptionProcessor(get_session_factory()).work_one()
    finally:
        maintenance.end_mutating_operation()


async def run_captions():
    while True:
        try:
            await run_unit(process_one)
        except Exception:
            logger.warning("Caption work paused; retrying a bounded unit")
        await asyncio.sleep(5)
