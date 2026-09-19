"""Advance real ingestion stages explicitly in ASGI tests without lifespan."""

import asyncio

from app.db.session import get_session_factory
from app.modules.media.enrichment import EnrichmentProcessor
from app.modules.storage.storage_backend.runtime import get_backend
from app.runtime.ingestion import process_one


async def drain_sources():
    for _ in range(100):
        if not await process_one():
            return
    raise AssertionError("source commands did not become idle")


async def drain_enrichment():
    processor = EnrichmentProcessor(get_session_factory(), get_backend())
    for _ in range(100):
        if not await asyncio.to_thread(processor.work_one):
            return
    raise AssertionError("artifact enrichment did not become idle")
