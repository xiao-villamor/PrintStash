"""Credential-free Vault namespace identity and candidate isolation."""

import hashlib
import json
from pathlib import Path

from app.core.config import settings
from app.modules.storage.storage_backend.contracts import StorageBackend
from app.modules.storage.storage_backend.local import LocalStorageBackend


def namespace_ref(backend: StorageBackend) -> str:
    target = backend.storage_target
    if target is None:
        raise ValueError("migration_destination_identity_required")
    payload = [
        target.target_ref,
        backend.blob_key("__identity__", 1, "object"),
        backend.thumbnail_key(0),
    ]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()
    ).hexdigest()


def validate_isolation(source: StorageBackend, destination: StorageBackend) -> None:
    if namespace_ref(source) == namespace_ref(destination):
        raise ValueError("migration_destination_matches_source")
    if isinstance(destination, LocalStorageBackend):
        roots = [destination.data_dir.resolve(), destination.thumb_dir.resolve()]
        excluded = [
            Path(settings.staging_dir).resolve(),
            Path(settings.backup_dir).resolve(),
            Path(settings.artifact_cache_root).resolve(),
        ]
        if isinstance(source, LocalStorageBackend):
            excluded += [source.data_dir.resolve(), source.thumb_dir.resolve()]
        for index, root in enumerate(roots):
            for protected in excluded + roots[index + 1 :]:
                if root.is_relative_to(protected) or protected.is_relative_to(root):
                    raise ValueError("migration_destination_roots_overlap")
    elif source.storage_target == destination.storage_target:
        # Different prefixes on one target are permitted only when disjoint.
        old = source.blob_key("__identity__", 1, "object").removesuffix(
            "__identity__/v1/object"
        )
        new = destination.blob_key("__identity__", 1, "object").removesuffix(
            "__identity__/v1/object"
        )
        if old.startswith(new) or new.startswith(old):
            raise ValueError("migration_destination_roots_overlap")
