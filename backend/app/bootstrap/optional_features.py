"""Assemble optional feature routes and their explicit library/ingestion ports."""

from importlib.util import find_spec

from fastapi import APIRouter

from app.modules.ingestion.extensions import bind_derivatives
from app.modules.library.model_views.extensions import bind_annotations, bind_families


def install_family_routes(router: APIRouter) -> None:
    if find_spec("app.modules.library.families") is None:
        bind_families(None)
        return
    from app.api.v1.families import router as family_router
    from app.modules.library.model_views import families

    bind_families(families)
    router.include_router(family_router)


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


def inference_available() -> bool:
    return find_spec("app.modules.inference") is not None


def install_search_routes(router: APIRouter) -> None:
    """Search does not depend on Similar Models; stripped installs keep library work."""
    if not inference_available():
        return
    from app.api.v1 import captions, inference, inference_models, search

    router.include_router(search.router)
    router.include_router(captions.router)
    router.include_router(inference.router)
    router.include_router(inference.search_router)
    router.include_router(inference_models.router)
