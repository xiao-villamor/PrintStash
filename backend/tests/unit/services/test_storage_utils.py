"""The typed ownership snapshot exposes one deduplicated vault-owned key set.

External paths are deliberately excluded from this destructive ownership view.
"""

from __future__ import annotations

from app.services.storage_utils import OwnedBlob, StorageOwnershipSnapshot


class TestStorageOwnershipSnapshotClaimedKeys:
    def test_deduplicates_primary_derived_and_embedded_keys(self) -> None:
        duplicate = OwnedBlob(
            key="vault/shared.bin", resource_type="file", resource_id=1
        )
        snapshot = StorageOwnershipSnapshot(
            primary=[duplicate],
            external=[
                OwnedBlob(
                    key="/mnt/user-owned.bin",
                    resource_type="file",
                    resource_id=2,
                )
            ],
            derived=[duplicate],
            embedded=[
                OwnedBlob(
                    key="vault/document-image.png",
                    resource_type="document_image",
                    resource_id=3,
                )
            ],
        )

        assert snapshot.claimed_keys == {
            "vault/shared.bin",
            "vault/document-image.png",
        }
