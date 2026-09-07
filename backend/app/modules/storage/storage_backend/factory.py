"""Construct an isolated Vault adapter from explicit typed configuration."""

from pathlib import Path

from app.modules.storage.storage_providers import (
    StorageProviderConfig,
    TransportKind,
    resolve_transport,
)

from .contracts import StorageBackend
from .local import LocalStorageBackend
from .s3 import S3StorageBackend


def build_configured_backend(config: StorageProviderConfig) -> StorageBackend:
    """No binding, settings mutation, setup probes or namespace enrollment.

    The caller owns capability validation before allowing writes. This factory
    also freezes local roots so retained generations never follow settings edits.
    """
    spec = resolve_transport(config)
    if spec.kind == TransportKind.LOCAL:
        return LocalStorageBackend(
            data_dir=Path(str(spec.options["data_dir"])),
            thumb_dir=Path(str(spec.options["thumb_dir"])),
        )
    if spec.kind == TransportKind.S3:
        return S3StorageBackend(transport=spec, check_bucket=False)
    if spec.kind in {TransportKind.WEBDAV, TransportKind.SFTP}:
        from app.modules.storage.storage_opendal import OpenDALStorageBackend

        return OpenDALStorageBackend(spec)
    raise ValueError("storage_provider_not_available_for_vault")
