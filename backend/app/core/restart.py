"""Gracefully hand process restart back to the deployment supervisor."""

from __future__ import annotations

import os
import signal

from app.core.logging import get_logger

logger = get_logger(__name__)


def request_restart() -> None:
    """Ask uvicorn to shut down cleanly so the configured supervisor relaunches it."""
    logger.warning("restart requested by an administrator")
    os.kill(os.getpid(), signal.SIGTERM)
