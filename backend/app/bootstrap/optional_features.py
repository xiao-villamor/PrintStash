"""Assemble optional feature routes and their explicit library/ingestion ports."""

from importlib.util import find_spec

from fastapi import APIRouter

from app.modules.ingestion.extensions import bind_derivatives
from app.modules.library.model_views.extensions import bind_annotations


def similarity_available() -> bool:
    return all(
        find_spec(package) is not None
        for package in ("app.modules.similarity", "app.modules.inference")
    )


def install_optional_routes(router: APIRouter) -> None:
    if not similarity_available():
        bind_annotations(None)
        bind_derivatives(None)
        return

    from app.api.v1.similarity import router as similarity_router
    from app.modules.similarity import ingestion, projections

    bind_annotations(projections)
    bind_derivatives(ingestion)
    router.include_router(similarity_router)
