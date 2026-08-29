"""Local storage namespaces and ownership evidence stay fail-closed.

Reclaim and legacy adoption may remove bytes only after the path, size, and
content belong to a configured managed root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services import storage_backend
from app.services.storage_backend import LocalStorageBackend, StorageCollisionError


class TestLocalNamespaces:
    def test_direct_adapter_identity_is_256_bit_when_unconfigured(
        self,
        configured_backend: LocalStorageBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(storage_backend.settings, "storage_identity", "")

        assert len(configured_backend._installation_identity()) == 64
        monkeypatch.setattr(storage_backend.settings, "storage_identity", "malformed")
        assert len(configured_backend._installation_identity()) == 64

    @pytest.mark.parametrize(
        "key_kind",
        [
            pytest.param("data", id="data"),
            pytest.param("thumb", id="thumb"),
            pytest.param("backup", id="backup"),
        ],
    )
    def test_reports_the_owned_namespace_for_managed_key(
        self, configured_backend: LocalStorageBackend, key_kind: str
    ) -> None:
        root = getattr(storage_backend.settings, f"{key_kind}_dir")
        key = str(root / "nested" / "object.bin")

        assert configured_backend.namespace_for(key) == f"{key_kind}:{root}"

    def test_rejects_a_key_outside_managed_roots(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            configured_backend.namespace_for(str(tmp_path / "not-owned.bin"))


class TestLocalKeyDerivation:
    @pytest.mark.parametrize(
        ("derive", "root", "expected"),
        [
            pytest.param(
                lambda backend: backend.blob_key("bracket", 2, "part.stl"),
                "data",
                "bracket/v2/part.stl",
                id="artifact",
            ),
            pytest.param(
                lambda backend: backend.thumbnail_key(7),
                "thumb",
                "7.webp",
                id="thumbnail",
            ),
            pytest.param(
                lambda backend: backend.legacy_thumbnail_key(7),
                "thumb",
                "7.png",
                id="legacy-thumbnail",
            ),
            pytest.param(
                lambda backend: backend.source_cover_key(9),
                "thumb",
                "source-covers/9.webp",
                id="source-cover",
            ),
            pytest.param(
                lambda backend: backend.capture_upload_slot_key("slot-1"),
                "data",
                "capture-slots/slot-1",
                id="capture-slot",
            ),
            pytest.param(
                lambda backend: backend.stl_cache_key("a" * 64),
                "thumb",
                f"stl-cache/{'a' * 64}.stl",
                id="stl-cache",
            ),
            pytest.param(
                lambda backend: backend.collection_image_key(3, "hero.webp"),
                "thumb",
                "collection-images/3/hero.webp",
                id="collection-image",
            ),
            pytest.param(
                lambda backend: backend.document_file_key(4, "manual.pdf"),
                "data",
                "documents/4/manual.pdf",
                id="document-file",
            ),
            pytest.param(
                lambda backend: backend.document_image_key(4, "figure.webp"),
                "thumb",
                "document-images/4/figure.webp",
                id="document-image",
            ),
        ],
    )
    def test_puts_each_object_kind_under_the_expected_root(
        self,
        configured_backend: LocalStorageBackend,
        derive,
        root: str,
        expected: str,
    ) -> None:
        assert derive(configured_backend) == str(
            getattr(storage_backend.settings, f"{root}_dir") / expected
        )


class TestLocalReclaim:
    def test_reclaim_pins_the_original_inode_before_a_path_replacement(
        self,
        configured_backend: LocalStorageBackend,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        destination = tmp_path / "files" / "reclaim-race.bin"
        destination.write_bytes(b"owned")
        real_rename = storage_backend.os.rename

        def replace_after_pin(source, target, **kwargs):
            replacement = destination.with_name("replacement.bin")
            replacement.write_bytes(b"other")
            storage_backend.os.replace(replacement, destination)
            return real_rename(source, target, **kwargs)

        monkeypatch.setattr(storage_backend.os, "rename", replace_after_pin)

        assert (
            configured_backend.reclaim_unverified(
                str(destination), expected_size=5, expected_etag=None
            )
            is False
        )
        assert destination.read_bytes() == b"other"

    def test_treats_a_missing_object_as_reclaimed(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = str(tmp_path / "files" / "missing.bin")

        assert (
            configured_backend.reclaim_unverified(
                key, expected_size=10, expected_etag=None
            )
            is True
        )

    def test_reclaims_an_object_matching_size_with_digest_proof(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        payload = b"owned payload"
        key = tmp_path / "files" / "reclaim.bin"
        key.write_bytes(payload)

        assert (
            configured_backend.reclaim_unverified(
                str(key),
                expected_size=len(payload),
                expected_etag=configured_backend.object_info(str(key)).etag,  # type: ignore[union-attr]
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            is True
        )
        assert not key.exists()

    def test_preserves_an_object_with_a_size_mismatch(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "reclaim-size.bin"
        key.write_bytes(b"payload")

        assert (
            configured_backend.reclaim_unverified(
                str(key), expected_size=999, expected_etag=None
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_preserves_an_object_with_a_digest_mismatch(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "reclaim-digest.bin"
        key.write_bytes(b"payload")

        assert (
            configured_backend.reclaim_unverified(
                str(key), expected_size=7, expected_etag=None, expected_sha256="0" * 64
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_preserves_an_object_with_an_etag_mismatch(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "reclaim-etag.bin"
        key.write_bytes(b"payload")

        assert (
            configured_backend.reclaim_unverified(
                str(key), expected_size=7, expected_etag='"different-etag"'
            )
            is False
        )
        assert key.read_bytes() == b"payload"

    def test_rejects_reclaim_outside_managed_roots(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            configured_backend.reclaim_unverified(
                str(tmp_path / "external.bin"), expected_size=0, expected_etag=None
            )


class TestLocalAdoption:
    def test_adopts_a_matching_legacy_object(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        payload = b"legacy artifact"
        key = tmp_path / "files" / "legacy.stl"
        key.write_bytes(payload)

        receipt = configured_backend.adopt_existing(
            str(key),
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )

        assert receipt.token == hashlib.sha256(payload).hexdigest()
        assert configured_backend.creation_matches(receipt)

    def test_rejects_a_legacy_object_with_wrong_digest(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "legacy.stl"
        key.write_bytes(b"legacy artifact")

        with pytest.raises(StorageCollisionError, match="content_mismatch"):
            configured_backend.adopt_existing(
                str(key), expected_size=15, expected_sha256="0" * 64
            )

    def test_rejects_a_legacy_directory(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "files" / "legacy-directory"
        key.mkdir()

        with pytest.raises(StorageCollisionError, match="content_mismatch"):
            configured_backend.adopt_existing(
                str(key), expected_size=0, expected_sha256="0" * 64
            )

    def test_rejects_a_legacy_object_outside_managed_roots(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        key = tmp_path / "external.stl"
        key.write_bytes(b"legacy artifact")

        with pytest.raises(StorageCollisionError, match="outside_managed_root"):
            configured_backend.adopt_existing(
                str(key), expected_size=15, expected_sha256="0" * 64
            )

    def test_rejects_a_missing_legacy_object(
        self, configured_backend: LocalStorageBackend, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageCollisionError, match="unavailable"):
            configured_backend.adopt_existing(
                str(tmp_path / "files" / "missing.stl"),
                expected_size=1,
                expected_sha256="0" * 64,
            )
