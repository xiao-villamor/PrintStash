from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    backup,
    config,
    documents,
    external_libraries,
    filaments,
    files,
    fleet,
    health,
    inbox,
    ingest,
    maintenance,
    models,
    notifications,
    printer_profiles,
    printers,
    saved_views,
    setup,
    share,
    spoolman,
    taxonomy,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(ingest.router)
api_router.include_router(inbox.router)
api_router.include_router(maintenance.router)
api_router.include_router(models.router)
api_router.include_router(saved_views.router)
api_router.include_router(files.router)
api_router.include_router(filaments.router)
api_router.include_router(printer_profiles.router)
api_router.include_router(taxonomy.router)
api_router.include_router(documents.router)
api_router.include_router(printers.router)
api_router.include_router(backup.router)
api_router.include_router(config.router)
api_router.include_router(external_libraries.router)
api_router.include_router(fleet.router)
api_router.include_router(notifications.router)
api_router.include_router(spoolman.router)
api_router.include_router(share.router)
api_router.include_router(share.admin_router)
