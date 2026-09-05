"""Blocking remote-storage adapter for the supported OpenDAL transports."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import BinaryIO

from app.services.remote_io_adapters import _RemoteAdapter
from app.services.storage_backend import (
    CreationReceipt,
    ObjectIdentity,
    StorageBackend,
    StorageCapabilities,
    StorageCollisionError,
    StorageConfigurationError,
)
from app.services.storage_providers import TransportKind, TransportSpec


class OpenDALStorageBackend(_RemoteAdapter, StorageBackend):
    """Managed remote Vault storage with explicit create-only publication."""

    def __init__(self, spec: TransportSpec, *, operator=None) -> None:
        super().__init__(spec, operator=operator)
        self._capabilities = StorageCapabilities(
            # WebDAV's no-overwrite MOVE and AsyncSSH's exclusive ``x`` mode
            # prevent competing publishers from replacing a winner.  They do not provide a durable
            # object identity/conditional replacement, so the adapter is
            # Guarded rather than Verified.
            # Do not advertise conditional publication until the configured
            # endpoint proves it.  A provider that silently overwrites a
            # duplicate key must remain unguarded/read-only.
            conditional_create=False,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        )
        self._probe_diagnostics: dict[str, object] = {
            "transport": spec.kind.value,
            "publication": "conditional_create",
            "verified_mutation": False,
        }

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return self._key(f"files/{slug}/v{version}/{filename}")

    def thumbnail_key(self, file_id: int) -> str:
        return self._key(f"thumbs/{file_id}.webp")

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return self._key(f"thumbs/{file_id}.png")

    def source_cover_key(self, provenance_source_id: int) -> str:
        return self._key(f"source-covers/{provenance_source_id}.webp")

    def capture_upload_slot_key(self, slot_id: str) -> str:
        return self._key(f"capture-slots/{slot_id}")

    def stl_cache_key(self, sha256: str) -> str:
        return self._key(f"cache/stl/{sha256}.stl")

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return self._key(f"collection-images/{collection_id}/{name}")

    def document_file_key(self, document_id: int, name: str) -> str:
        return self._key(f"documents/{document_id}/{name}")

    def document_image_key(self, document_id: int, name: str) -> str:
        return self._key(f"document-images/{document_id}/{name}")

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        return self._key(f"multipart-covers/{multipart_model_id}/{name}")

    def move(self, src_key: str, dest_key: str) -> None:
        source = self._relative(src_key)
        destination = self._relative(dest_key)
        if self._spec.kind is TransportKind.WEBDAV and self._webdav_endpoint.startswith(
            ("http://", "https://")
        ):
            self._webdav_ensure_parent(destination)
            self._webdav_move_create_only(source, destination)
            return
        if self._spec.kind is TransportKind.SFTP:
            # SFTP rename is allowed to replace a destination.  The existence
            # check below would be a TOCTOU window, so do not expose it as a
            # create-only move until the server provides a no-replace rename.
            raise StorageConfigurationError("atomic_move_not_supported")
        if self._operator.exists(destination):
            raise StorageCollisionError(dest_key)
        self._operator.rename(source, destination)

    def download_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as output:
            for chunk in self.stream_chunks(key):
                output.write(chunk)
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        self._operator.check()
        from io import BytesIO

        probe_directory = f".printstash-probe/{uuid.uuid4().hex}"
        probe = f"{probe_directory}/proof"
        first = b"printstash-conditional-create-proof"
        second = b"printstash-conditional-create-collision"

        def cleanup() -> None:
            try:
                self._operator.delete(self._relative(self._key(probe)))
            except Exception:
                pass

        try:
            self.create_stream(BytesIO(first), self._key(probe))
        except Exception:
            self._probe_diagnostics["probed"] = True
            self._probe_diagnostics["conditional_create"] = False
            raise
        try:
            self.create_stream(BytesIO(second), self._key(probe))
        except StorageCollisionError:
            collision_proven = True
        except Exception:
            self._probe_diagnostics["probed"] = True
            self._probe_diagnostics["conditional_create"] = False
            cleanup()
            raise
        else:
            collision_proven = False
        try:
            observed = self.read_bytes(self._key(probe))
            observed_size = self.stat_size(self._key(probe))
            listed = self._key(probe) in self.walk_keys(self._key(probe_directory))
        except Exception:
            self._probe_diagnostics["probed"] = True
            self._probe_diagnostics["conditional_create"] = False
            cleanup()
            raise
        if not listed:
            cleanup()
            raise StorageConfigurationError("remote_conditional_create_unproven")
        if observed_size != len(first):
            if collision_proven:
                cleanup()
                raise StorageConfigurationError("remote_conditional_create_unproven")
            # Reached endpoint, but its duplicate write changed the object.
            cleanup()
            self._capabilities = StorageCapabilities(
                conditional_create=False,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=False,
            )
            self._probe_diagnostics.update(
                {"probed": True, "conditional_create": False, "read_only": True}
            )
            self._read_only = True
            return
        if observed != first:
            if collision_proven:
                cleanup()
                raise StorageConfigurationError("remote_conditional_create_unproven")
            cleanup()
            self._capabilities = StorageCapabilities(
                conditional_create=False,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=False,
            )
            self._probe_diagnostics.update(
                {"probed": True, "conditional_create": False, "read_only": True}
            )
            self._read_only = True
            return
        cleanup()
        self._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        )
        self._probe_diagnostics["probed"] = True
        self._probe_diagnostics["destructive_access"] = True
        self._probe_diagnostics["conditional_create"] = True

    def provision_root(self) -> None:
        """Create the configured SFTP root during an explicitly authorized setup.

        Startup and health checks intentionally call only ``check``.  A missing
        enrolled root therefore remains a fail-closed condition instead of being
        silently replaced by an empty directory.  The first-run setup wizard is
        the sole caller of this mutating seam.
        """
        if self._spec.kind is not TransportKind.SFTP:
            raise StorageConfigurationError("remote_root_provisioning_unsupported")
        provision = getattr(self._operator, "provision_root", None)
        if provision is None:
            raise StorageConfigurationError("sftp_root_provisioning_unavailable")
        provision()
        self._operator.check()

    def verify_destructive_access(self, keys: list[str]) -> None:
        """Probe create/delete on a fresh key, never on a caller's object."""
        del keys
        probe = f".printstash-probe/{uuid.uuid4().hex}"
        try:
            if self._spec.kind is TransportKind.SFTP:
                from io import BytesIO

                if not hasattr(self._operator, "write_exclusive"):
                    raise StorageConfigurationError("sftp_exclusive_create_unavailable")
                self._operator.write_exclusive(probe, BytesIO())
            elif self._spec.kind is TransportKind.WEBDAV:
                from io import BytesIO

                self.create_stream(BytesIO(), self._key(probe))
            self._operator.delete(probe)
        except Exception as exc:
            try:
                self._operator.delete(probe)
            except Exception:
                pass
            raise StorageConfigurationError(
                "remote_destructive_access_unavailable"
            ) from exc

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        return list(self.walk_keys(prefix))

    def list_prefix(self, prefix: str = "") -> list[str]:
        """Return full storage keys below a namespace-relative prefix."""
        return self.list_keys(prefix)

    def usage(self, prefix: str = "") -> dict:
        total = count = 0
        for key in self.walk_keys(prefix):
            info = self.object_info(key)
            if info is not None:
                count += 1
                total += info.size
        return {"bytes": total, "objects": count}

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        del key, filename
        return None

    def health_probe(self) -> dict:
        try:
            self._operator.check()
            return {
                "backend": self.backend_name,
                "provider": self._spec.provider,
                "ok": True,
                "tier": self.capabilities.tier.value,
                "capabilities": self.capabilities.as_dict(),
                "warnings": list(self.capabilities.warnings),
                "diagnostics": self.probe_diagnostics,
            }
        except Exception as exc:
            return {
                "backend": self.backend_name,
                "provider": self._spec.provider,
                "ok": False,
                "error": exc.__class__.__name__,
                "tier": self.capabilities.tier.value,
                "capabilities": self.capabilities.as_dict(),
                "warnings": list(self.capabilities.warnings),
                "diagnostics": self.probe_diagnostics,
            }

    def direct_path(self, key: str) -> Path | None:
        self._relative(key)
        return None

    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        del expected_version_id
        info = self.object_info(key)
        if info is None:
            return True
        if info.size != expected_size:
            return False
        if expected_etag is not None and info.etag != expected_etag:
            return False
        if expected_sha256 is not None:
            digest = hashlib.sha256()
            for chunk in self.stream_chunks(key):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256.lower():
                return False
        # Guarded transports expose no immutable object identity and no
        # conditional delete/quarantine primitive.  The proof above can be
        # invalidated by a replacement before delete, so retain the bytes and
        # let the ownership ledger record a durable blocked cleanup outcome.
        return False

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        extension = self.managed_creation
        if extension is None:
            raise StorageConfigurationError("atomic_create_not_supported")
        return extension.create_stream(src, key)

    def delete_versioned(self, key: str, version_id: str) -> None:
        extension = self.exact_deletion
        if extension is None:
            raise StorageConfigurationError("conditional_delete_unavailable")
        extension.delete_versioned(key, version_id)
