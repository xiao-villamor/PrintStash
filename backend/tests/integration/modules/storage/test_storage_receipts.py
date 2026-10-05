"""Public exact receipt contracts shared by publication and retirement."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.db.models import OwnedStorageObject, StorageObjectState
from app.modules.storage.storage_receipts import (
    UnsafeStorageDeleteError,
    owned_receipt,
    provider_ref_for_backend,
)


class TestOwnedReceipt:
    def test_preserves_the_committed_physical_generation(self, db_session):
        row = OwnedStorageObject(
            backend="local",
            namespace="vault",
            key="models/source.stl",
            object_kind="artifact",
            state=StorageObjectState.COMMITTED,
            token="creation-token",
            size_bytes=9,
            provider_ref="saved-provider",
            etag="saved-etag",
            version_id="saved-version",
            device=7,
            inode=11,
            ctime_ns=13,
        )
        db_session.add(row)
        db_session.commit()
        receipt = owned_receipt(db_session.get(OwnedStorageObject, row.id))
        assert receipt.key == "models/source.stl"
        assert receipt.backend == "local"
        assert receipt.namespace == "vault"
        assert receipt.provider_ref == "saved-provider"
        assert receipt.token == "creation-token"
        assert receipt.size == 9
        assert receipt.etag == "saved-etag"
        assert receipt.version_id == "saved-version"
        assert (receipt.device, receipt.inode, receipt.ctime_ns) == (7, 11, 13)
        row.token = "replacement-token"
        assert receipt.token == "creation-token"
        assert replace(receipt, token="replacement-token") != receipt

    @pytest.mark.parametrize("missing", ["token", "size_bytes"])
    def test_refuses_incomplete_persisted_authority(self, db_session, missing):
        row = OwnedStorageObject(
            backend="local",
            namespace="vault",
            key="models/incomplete.stl",
            object_kind="artifact",
            state=StorageObjectState.COMMITTED,
            token="creation-token",
            size_bytes=9,
        )
        setattr(row, missing, None)
        db_session.add(row)
        db_session.commit()
        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_ownership_incomplete"
        ):
            owned_receipt(db_session.get(OwnedStorageObject, row.id))


class TestSharedReceiptIdentity:
    def test_provider_binding_is_shared_with_existing_ownership_consumers(
        self, tmp_path
    ):
        from app.modules.storage.storage_backend.local import LocalStorageBackend
        from app.modules.storage.storage_ownership import (
            UnsafeStorageDeleteError as ConsumerError,
        )
        from app.modules.storage.storage_ownership import (
            provider_ref_for_backend as consumer_provider_ref,
        )

        backend = LocalStorageBackend(
            data_dir=tmp_path / "data",
            thumb_dir=tmp_path / "thumbnails",
            backup_dir=tmp_path / "backups",
        )
        assert ConsumerError is UnsafeStorageDeleteError
        assert consumer_provider_ref(
            backend, namespace="vault"
        ) == provider_ref_for_backend(backend, namespace="vault")
        assert provider_ref_for_backend(
            backend, namespace="vault"
        ) != provider_ref_for_backend(backend, namespace="another-vault")


class TestProviderReceiptBinding:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://[not-an-ipv6-address]/",
            "store.example.test/bucket",
            "https:///bucket",
            "https://store.example.test:invalid/",
            "https://store.example.test:70000/",
        ],
    )
    def test_refuses_endpoints_without_a_stable_destination(self, endpoint):
        backend = SimpleNamespace(
            backend_name="s3",
            transport="s3",
            endpoint_url=endpoint,
            region="test-region",
        )
        with pytest.raises(ValueError, match="storage_provider_endpoint_invalid"):
            provider_ref_for_backend(backend, namespace="bucket/models")

    def test_backup_settings_define_credential_independent_binding(
        self, monkeypatch
    ):
        from app.core.config import _overlay

        monkeypatch.setitem(
            _overlay, "backup_s3_endpoint_url", "https://Store.Example.test:443/"
        )
        monkeypatch.setitem(_overlay, "backup_s3_region", "EU-West-1")
        configured = SimpleNamespace(backend_name="backup-s3")
        explicit = SimpleNamespace(
            backend_name="backup-s3",
            endpoint_url="https://store.example.test",
            region="eu-west-1",
        )
        saved = provider_ref_for_backend(configured, namespace="backup-bucket")
        assert saved == provider_ref_for_backend(explicit, namespace="backup-bucket")
        monkeypatch.setitem(_overlay, "backup_s3_access_key", "rotated-access")
        monkeypatch.setitem(_overlay, "backup_s3_secret_key", "rotated-secret")
        assert provider_ref_for_backend(configured, namespace="backup-bucket") == saved
        monkeypatch.setitem(
            _overlay, "backup_s3_endpoint_url", "https://other.example.test"
        )
        assert provider_ref_for_backend(configured, namespace="backup-bucket") != saved

    @pytest.mark.parametrize("transport", ["s3", "webdav"])
    def test_transport_options_bind_destination_but_not_credentials(self, transport):
        options = {"endpoint_url": "https://Store.Example.test:443/"}
        if transport == "s3":
            options.update(region="EU-West-1", addressing_style="virtual")
        else:
            options["root"] = "models"

        def backend(**changes):
            return SimpleNamespace(
                backend_name=f"backup-opendal-{transport}",
                transport=transport,
                _spec=SimpleNamespace(options={**options, **changes}),
            )

        saved = provider_ref_for_backend(backend(), namespace="models")
        assert saved == provider_ref_for_backend(
            backend(
                endpoint_url="https://store.example.test",
                access_key="rotated",
                secret_key="secret",
                password="password",
            ),
            namespace="models",
        )
        assert saved != provider_ref_for_backend(
            backend(endpoint_url="https://other.example.test"), namespace="models"
        )
        if transport == "s3":
            assert saved != provider_ref_for_backend(
                backend(region="another-region"), namespace="models"
            )
            assert saved != provider_ref_for_backend(
                backend(addressing_style="path"), namespace="models"
            )
        else:
            assert saved != provider_ref_for_backend(
                backend(root="other-models"), namespace="models"
            )
