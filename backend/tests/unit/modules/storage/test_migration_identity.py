"""Namespace identity never includes credentials or permits overlapping Vault roots."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.modules.storage.migration_identity import namespace_ref, validate_isolation
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_identity import s3_target


@dataclass
class RemoteNamespace:
    root: str
    bucket: str = "migration-tests"

    @property
    def storage_target(self):
        return s3_target(endpoint="https://objects.invalid", bucket=self.bucket)

    def blob_key(self, model, revision, name):
        return f"{self.root}/files/{model}/v{revision}/{name}"

    def thumbnail_key(self, identity):
        return f"{self.root}/thumbnails/{identity}.webp"


class TestNamespaceRef:
    def test_requires_physical_target_identity(self):
        with pytest.raises(ValueError, match="migration_destination_identity_required"):
            namespace_ref(SimpleNamespace(storage_target=None))

    def test_distinguishes_prefixes_on_one_target(self):
        assert namespace_ref(RemoteNamespace("one")) != namespace_ref(
            RemoteNamespace("two")
        )


class TestValidateIsolation:
    def test_refuses_an_identical_namespace(self):
        with pytest.raises(ValueError, match="migration_destination_matches_source"):
            validate_isolation(RemoteNamespace("vault"), RemoteNamespace("vault"))

    @pytest.mark.parametrize(
        ("old", "new"),
        [("vault", "vault/files/nested"), ("vault/files/nested", "vault")],
    )
    def test_refuses_overlapping_primary_prefixes(self, old, new):
        with pytest.raises(ValueError, match="migration_destination_roots_overlap"):
            validate_isolation(RemoteNamespace(old), RemoteNamespace(new))

    def test_allows_disjoint_prefixes_on_one_target(self):
        assert (
            validate_isolation(
                RemoteNamespace("source"), RemoteNamespace("destination")
            )
            is None
        )

    def test_allows_equal_prefixes_on_distinct_targets(self):
        assert (
            validate_isolation(
                RemoteNamespace("vault", "source"),
                RemoteNamespace("vault", "destination"),
            )
            is None
        )

    def test_allows_remote_to_disjoint_local_roots(self, tmp_path):
        destination = LocalStorageBackend(
            data_dir=tmp_path / "files", thumb_dir=tmp_path / "thumbs"
        )
        assert validate_isolation(RemoteNamespace("vault"), destination) is None
