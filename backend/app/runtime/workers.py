"""Local draining policy for durable work owners.

Owners claim and checkpoint work in SQL. This loop only schedules bounded units,
yields between them, and drains an in-flight unit before lifecycle teardown.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)


T = TypeVar("T")


async def run_unit(
    operation: Callable[[], T], *, on_cancel: Callable[[], None] | None = None
) -> T:
    unit = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(unit)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        try:
            await unit
        except Exception:
            # A failing unit must never turn shutdown into another retry loop.
            logger.warning("Background unit failed while draining")
        raise


async def run_async_worker(
    operation: Callable[[], Awaitable[bool]], *, idle_seconds: float = 1
) -> None:
    """Retry infrastructure failures without abandoning in-flight work at shutdown."""
    while True:
        worked = False
        unit = asyncio.create_task(operation())
        try:
            worked = await asyncio.shield(unit)
        except asyncio.CancelledError:
            try:
                await unit
            except Exception:
                logger.warning("Background unit failed while draining")
            raise
        except Exception:
            logger.warning("Background unit deferred; inspect its durable status")
        await asyncio.sleep(0 if worked else idle_seconds)
