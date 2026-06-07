from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    backup,
    config,
    files,
    filaments,
    health,
    ingest,
    models,
    printers,
    setup,
    taxonomy,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(ingest.router)
api_router.include_router(models.router)
api_router.include_router(files.router)
api_router.include_router(filaments.router)
api_router.include_router(taxonomy.router)
api_router.include_router(printers.router)
api_router.include_router(backup.router)
api_router.include_router(config.router)
