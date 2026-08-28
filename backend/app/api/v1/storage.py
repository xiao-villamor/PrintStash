"""Public metadata for storage-provider selection."""

from fastapi import APIRouter

from app.services.storage_providers import StorageProvider, provider_catalogue

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get(
    "/providers",
    response_model=list[StorageProvider],
    summary="List storage providers",
)
def list_storage_providers() -> list[StorageProvider]:
    """Return metadata only; credentials and configured values are never exposed."""
    return provider_catalogue()
