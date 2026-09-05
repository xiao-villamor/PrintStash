"""Storage capability values stay truthful across local and remote adapters.

The tier and warning payloads are operator-facing safety information. Keeping
these pure tests beside the capability unit makes regressions visible without
requiring a storage service.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterator

import pytest

from app.services.storage_backend import (
    CreationReceipt,
    ObjectIdentity,
    StorageBackend,
    StorageCapabilities,
    StorageConfigurationError,
    StorageObjectInfo,
    StorageTier,
    UnavailableStorageBackend,
)


class TestStorageCapabilities:
    @pytest.mark.parametrize(
        ("conditional_create", "verified_delete", "conditional_replace", "tier"),
        [
            pytest.param(False, False, False, StorageTier.UNGUARDED, id="unguarded"),
            pytest.param(True, False, True, StorageTier.GUARDED, id="guarded"),
            pytest.param(True, True, True, StorageTier.VERIFIED, id="verified"),
        ],
    )
    def test_derives_the_strongest_safe_tier(
        self,
        conditional_create: bool,
        verified_delete: bool,
        conditional_replace: bool,
        tier: StorageTier,
    ) -> None:
        capabilities = StorageCapabilities(
            conditional_create=conditional_create,
            object_identity=ObjectIdentity.NONE,
            verified_delete=verified_delete,
            conditional_replace=conditional_replace,
            namespace_ownership=False,
            direct_path=False,
        )

        assert capabilities.tier is tier

    def test_serializes_capability_flags(self) -> None:
        capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.ETAG,
            verified_delete=False,
            conditional_replace=True,
            namespace_ownership=False,
            direct_path=False,
        )

        assert capabilities.as_dict() == {
            "conditional_create": True,
            "object_identity": "etag",
            "verified_delete": False,
            "conditional_replace": True,
            "namespace_ownership": False,
            "direct_path": False,
            "tier": "guarded",
            "warnings": [
                "Interrupted uploads can leave retained bytes requiring storage-specific cleanup.",
                "PrintStash cannot confirm that a file is inside its owned storage root.",
            ],
        }

    def test_reports_all_missing_guarantee_warnings(self) -> None:
        capabilities = StorageCapabilities(
            conditional_create=False,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=False,
            direct_path=False,
        )

        assert len(capabilities.warnings) == 5


class TestUnavailableStorageBackend:
    """An invalid provider never turns into a partially working adapter."""

    @pytest.mark.parametrize(
        ("operation", "argument"),
        [
            ("blob_key", ("model", 1, "part.stl")),
            ("thumbnail_key", (1,)),
            ("source_cover_key", (1,)),
            ("capture_upload_slot_key", ("slot",)),
            ("legacy_thumbnail_key", (1,)),
            ("stl_cache_key", ("a" * 64,)),
            ("collection_image_key", (1, "hero.webp")),
            ("document_file_key", (1, "manual.pdf")),
            ("document_image_key", (1, "figure.webp")),
            ("multipart_model_cover_key", (1, "cover.webp")),
        ],
    )
    def test_rejects_key_derivation(
        self, operation: str, argument: tuple[object, ...]
    ) -> None:
        backend = UnavailableStorageBackend("invalid_provider")

        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            getattr(backend, operation)(*argument)

    @pytest.mark.parametrize(
        ("operation", "argument"),
        [
            ("exists", ("any-key",)),
            ("create_stream", (BytesIO(b"payload"), "any-key")),
            (
                "replace_stream",
                (
                    BytesIO(b"payload"),
                    CreationReceipt(
                        key="any-key",
                        size=7,
                        token="token",
                        backend="unavailable",
                        namespace="unavailable",
                    ),
                ),
            ),
            ("move", ("source", "destination")),
            ("stat_size", ("any-key",)),
            ("read_bytes", ("any-key",)),
            ("download_to_path", ("any-key", Path("destination"))),
            ("upload_file", (Path("source"), "any-key")),
            ("delete", ("any-key",)),
            ("list_keys", ("prefix",)),
            ("usage", ("prefix",)),
            ("presigned_download_url", ("any-key", "part.stl")),
        ],
    )
    def test_rejects_storage_io(
        self, operation: str, argument: tuple[object, ...]
    ) -> None:
        backend = UnavailableStorageBackend("invalid_provider")

        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            getattr(backend, operation)(*argument)

    def test_rejects_stream_iteration(self) -> None:
        backend = UnavailableStorageBackend("invalid_provider")

        with pytest.raises(StorageConfigurationError, match="storage_unavailable"):
            list(backend.stream_chunks("any-key"))

    def test_exposes_a_safe_health_payload(self) -> None:
        backend = UnavailableStorageBackend("invalid_provider")

        assert backend.health_probe() == {
            "backend": "unavailable",
            "ok": False,
            "error": "invalid_provider",
            "capabilities": backend.capabilities.as_dict(),
            "diagnostics": {"available": False, "error": "invalid_provider"},
        }
        backend.ensure_setup()
        assert backend.direct_path("any-key") is None


class _ProbeBackend(StorageBackend):
    """Tiny concrete adapter for provider-independent base methods."""

    backend_name = "probe"
    provider_id = "probe"
    transport = "probe"

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return f"{slug}/{version}/{filename}"

    def thumbnail_key(self, file_id: int) -> str:
        return f"thumb/{file_id}"

    def source_cover_key(self, provenance_source_id: int) -> str:
        return f"cover/{provenance_source_id}"

    def capture_upload_slot_key(self, slot_id: str) -> str:
        return f"slot/{slot_id}"

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return f"legacy/{file_id}"

    def stl_cache_key(self, sha256: str) -> str:
        return f"cache/{sha256}"

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return f"collection/{collection_id}/{name}"

    def document_file_key(self, document_id: int, name: str) -> str:
        return f"document/{document_id}/{name}"

    def document_image_key(self, document_id: int, name: str) -> str:
        return f"document-image/{document_id}/{name}"

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        return f"multipart-cover/{multipart_model_id}/{name}"

    def exists(self, key: str) -> bool:
        return key == "present"

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key

    def stat_size(self, key: str) -> int:
        del key
        return 12

    def read_bytes(self, key: str) -> bytes:
        del key
        return b"payload"

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        del key, chunk_size
        yield b"payload"

    def download_to_path(self, key: str, dest: Path) -> Path:
        del key
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        del src, key

    def ensure_setup(self) -> None:
        return None

    def delete(self, key: str) -> None:
        del key

    def list_keys(self, prefix: str = "") -> list[str]:
        return [prefix]

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        yield prefix

    def usage(self, prefix: str = "") -> dict:
        return {"prefix": prefix}

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        return f"{key}/{filename}"

    def health_probe(self) -> dict:
        return {"backend": self.backend_name, "ok": True}

    def direct_path(self, key: str) -> Path | None:
        return Path(key) if key == "present" else None


class TestStorageBackendDefaults:
    def test_uses_safe_capability_defaults_for_legacy_adapters(self) -> None:
        backend = _ProbeBackend()

        assert backend.capabilities.tier.value == "unguarded"
        assert backend.probe_diagnostics == {}
        assert backend.destructive_lifecycle_findings() == []
        assert (
            backend.reclaim_unverified("key", expected_size=1, expected_etag=None)
            is False
        )

    def test_requires_provider_namespace_for_restore_validation(self) -> None:
        backend = _ProbeBackend()

        with pytest.raises(NotImplementedError, match="storage_namespace"):
            backend.namespace_for("key")
        with pytest.raises(NotImplementedError, match="storage_namespace"):
            backend.validate_restore_key("key")

    def test_fails_closed_for_unsupported_compatibility_writes(self) -> None:
        backend = _ProbeBackend()
        receipt = CreationReceipt(
            key="key",
            size=1,
            token="token",
            backend="probe",
            namespace="probe",
        )

        with pytest.raises(NotImplementedError, match="atomic_create"):
            backend.write_stream(BytesIO(b"payload"), "key")
        with pytest.raises(NotImplementedError, match="atomic_create"):
            backend.write_bytes(b"payload", "key")
        with pytest.raises(NotImplementedError, match="atomic_replace"):
            backend.replace_stream(BytesIO(b"payload"), receipt)
        with pytest.raises(NotImplementedError, match="atomic_replace"):
            backend.replace_bytes(b"payload", receipt)
        with pytest.raises(NotImplementedError, match="existing_storage_adoption"):
            backend.adopt_existing("key", expected_size=1, expected_sha256="0" * 64)
        with pytest.raises(
            NotImplementedError, match="destructive_access_probe_not_supported"
        ):
            backend.verify_destructive_access(["key"])
        assert backend.rollback_create(receipt) is False
        assert backend.creation_matches(receipt) is False

    def test_returns_object_info_only_for_existing_keys(self) -> None:
        backend = _ProbeBackend()

        assert backend.object_info("missing") is None
        assert backend.object_info("present") == StorageObjectInfo(size=12)

    def test_list_prefix_delegates_to_the_adapter(self) -> None:
        assert _ProbeBackend().list_prefix("vault/") == ["vault/"]
