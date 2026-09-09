"""Local durable migration scheduler; journals fence restart before new work."""

import asyncio

from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.modules.storage.vault_migration import VaultMigrations
from app.runtime.maintenance import restore_in_progress

logger = get_logger(__name__)


async def run_migrations() -> None:
    while True:
        if not restore_in_progress():
            try:
                await asyncio.to_thread(
                    VaultMigrations(get_session_factory()).work_pending
                )
            except Exception:
                # Per-object error codes are persisted by the owner. Adapter
                # exceptions may contain credentials and are never interpolated.
                logger.warning(
                    "Vault migration paused; inspect its safe progress report"
                )
        await asyncio.sleep(1)
