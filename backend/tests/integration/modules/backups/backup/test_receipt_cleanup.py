"""Backup cleanup cannot reclaim a replacement sharing an old ETag."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.modules.backups import backup_destination
from app.modules.backups.backup import receipt_cleanup
from app.modules.backups.backup_destination import RemoteBackupDestination
from app.modules.storage.remote_io import RemoteIO
from app.modules.storage.storage_backend.contracts import CreationReceipt
from app.modules.storage.storage_publication import ReceiptReclaimResult


class TestReclaimReceipt:
    def test_defers_unversioned_receipt_when_recreation_can_share_its_etag(
        self, monkeypatch
    ):
        # The preflight observed old nonce/ETag. A new PUT can have the same
        # content/ETag but a different nonce before conditional DELETE arrives.
        class Store:
            token = "old"
            payload = b"same"
            deletes = 0

            def delete_object(self, **kwargs):
                self.token = "new"
                assert kwargs["IfMatch"] == "same-etag"
                self.payload = None
                self.deletes += 1

        store = Store()
        monkeypatch.setattr(
            receipt_cleanup.targets,
            "_get_backup_s3_target",
            lambda: SimpleNamespace(
                client=store, bucket="bucket", provider_ref="profile"
            ),
        )
        receipt = CreationReceipt(
            key="printstash-backups/race.zip",
            size=4,
            token="old",
            backend="backup-s3",
            namespace="bucket/printstash-backups",
            etag="same-etag",
            provider_ref="profile",
        )
        result = receipt_cleanup.reclaim_receipt(receipt)
        assert result is ReceiptReclaimResult.UNSUPPORTED
        assert store.deletes == 0
        assert store.payload == b"same"

    def test_deletes_only_captured_version_when_current_generation_changes(
        self, monkeypatch
    ):
        class Store:
            current = "new-version"
            versions = {"old-version": b"same", "new-version": b"same"}

            def delete_object(self, **kwargs):
                assert kwargs == {
                    "Bucket": "bucket",
                    "Key": "printstash-backups/race.zip",
                    "VersionId": "old-version",
                }
                del self.versions[kwargs["VersionId"]]

        store = Store()
        monkeypatch.setattr(
            receipt_cleanup.targets,
            "_get_backup_s3_target",
            lambda: SimpleNamespace(
                client=store, bucket="bucket", provider_ref="profile"
            ),
        )
        receipt = CreationReceipt(
            key="printstash-backups/race.zip",
            size=4,
            token="old",
            backend="backup-s3",
            namespace="bucket/printstash-backups",
            etag="same-etag",
            version_id="old-version",
            provider_ref="profile",
        )
        assert receipt_cleanup.reclaim_receipt(receipt) is ReceiptReclaimResult.REMOVED
        assert store.versions == {"new-version": b"same"}


@pytest.fixture
def versioned_receipt():
    return CreationReceipt(
        key="printstash-backups/exact.zip",
        size=4,
        token="captured",
        backend="backup-s3",
        namespace="bucket/printstash-backups",
        version_id="captured-version",
        provider_ref="saved-profile",
    )


@pytest.fixture
def backup_s3_stub(monkeypatch):
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
    )
    target = receipt_cleanup.targets._BackupS3Target(
        client=client,
        bucket="bucket",
        signature="test",
        provider_ref="saved-profile",
    )
    monkeypatch.setattr(
        receipt_cleanup.targets, "_get_backup_s3_target", lambda: target
    )
    with Stubber(client) as stub:
        yield stub
        stub.assert_no_pending_responses()
    client.close()


class TestReclaimReceiptProviderSafety:
    def test_preserves_receipt_when_s3_provider_is_unavailable(
        self, monkeypatch, versioned_receipt
    ):
        monkeypatch.setattr(
            receipt_cleanup.targets, "_get_backup_s3_target", lambda: None
        )

        with pytest.raises(RuntimeError, match="backup_delete_provider_unavailable"):
            receipt_cleanup.reclaim_receipt(versioned_receipt)
        assert versioned_receipt.version_id == "captured-version"

    @pytest.mark.parametrize(
        "changed",
        [
            pytest.param({"namespace": "other/printstash-backups"}, id="bucket"),
            pytest.param({"provider_ref": "different-profile"}, id="provider"),
        ],
    )
    def test_refuses_changed_s3_binding(
        self, backup_s3_stub, versioned_receipt, changed
    ):
        receipt = replace(versioned_receipt, **changed)

        assert (
            receipt_cleanup.reclaim_receipt(receipt)
            is ReceiptReclaimResult.PROVIDER_MISMATCH
        )
        # Stubber has no queued operation: any provider I/O would fail this test.

    def test_reconstructs_an_empty_target_bucket_from_saved_receipt(
        self, monkeypatch, versioned_receipt
    ):
        class Store:
            versions = {"captured-version": b"old!", "replacement": b"new!"}

            def delete_object(self, **kwargs):
                assert kwargs == {
                    "Bucket": "bucket",
                    "Key": versioned_receipt.key,
                    "VersionId": "captured-version",
                }
                del self.versions[kwargs["VersionId"]]

        store = Store()
        target = receipt_cleanup.targets._BackupS3Target(
            client=store,
            bucket="",
            signature="test",
            provider_ref="saved-profile",
        )
        monkeypatch.setattr(
            receipt_cleanup.targets, "_get_backup_s3_target", lambda: target
        )

        assert (
            receipt_cleanup.reclaim_receipt(versioned_receipt)
            is ReceiptReclaimResult.REMOVED
        )
        assert store.versions == {"replacement": b"new!"}

    @pytest.mark.parametrize(
        "code",
        ["NoSuchKey", "NoSuchVersion", "404", "NotFound"],
        ids=["missing-key", "missing-version", "404", "not-found"],
    )
    def test_accepts_an_already_absent_s3_generation(
        self, backup_s3_stub, versioned_receipt, code
    ):
        backup_s3_stub.add_client_error(
            "delete_object",
            service_error_code=code,
            http_status_code=404,
            expected_params={
                "Bucket": "bucket",
                "Key": versioned_receipt.key,
                "VersionId": versioned_receipt.version_id,
            },
        )

        assert (
            receipt_cleanup.reclaim_receipt(versioned_receipt)
            is ReceiptReclaimResult.ABSENT
        )

    @pytest.mark.parametrize(
        "code", ["PreconditionFailed", "412"], ids=["precondition", "412"]
    )
    def test_reports_s3_generation_mismatch(
        self, backup_s3_stub, versioned_receipt, code
    ):
        backup_s3_stub.add_client_error(
            "delete_object",
            service_error_code=code,
            http_status_code=412,
            expected_params={
                "Bucket": "bucket",
                "Key": versioned_receipt.key,
                "VersionId": versioned_receipt.version_id,
            },
        )

        assert (
            receipt_cleanup.reclaim_receipt(versioned_receipt)
            is ReceiptReclaimResult.MISMATCH
        )

    def test_propagates_unknown_s3_failure(self, backup_s3_stub, versioned_receipt):
        backup_s3_stub.add_client_error(
            "delete_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={
                "Bucket": "bucket",
                "Key": versioned_receipt.key,
                "VersionId": versioned_receipt.version_id,
            },
        )

        with pytest.raises(ClientError) as error:
            receipt_cleanup.reclaim_receipt(versioned_receipt)
        assert error.value.response["Error"]["Code"] == "AccessDenied"

    def test_refuses_null_s3_version(self, backup_s3_stub, versioned_receipt):
        receipt = replace(versioned_receipt, version_id="null")

        assert (
            receipt_cleanup.reclaim_receipt(receipt) is ReceiptReclaimResult.UNSUPPORTED
        )

    def test_rejects_an_unknown_backend(self, versioned_receipt):
        receipt = replace(versioned_receipt, backend="unknown")

        with pytest.raises(ValueError, match="backup_delete_backend_unsupported"):
            receipt_cleanup.reclaim_receipt(receipt)


@pytest.fixture
def opendal_receipt(versioned_receipt):
    return replace(versioned_receipt, backend="backup-opendal-s3")


@pytest.fixture
def opendal_destination(monkeypatch):
    class Versions:
        objects = {"captured-version": b"old!", "replacement": b"new!"}

        def delete_versioned(self, key, version_id):
            assert key == "printstash-backups/exact.zip"
            self.objects.pop(version_id, None)

    versions = Versions()
    backend = SimpleNamespace(
        backend_name="backup-opendal-s3",
        source_namespace="bucket/printstash-backups",
        exact_deletion=versions,
    )
    destination = RemoteBackupDestination(
        connection_id=1,
        name="Saved backup",
        provider="s3",
        backend=cast(RemoteIO, backend),
        provider_ref="saved-profile",
    )
    monkeypatch.setattr(
        backup_destination, "configured_destinations", lambda: [destination]
    )
    return destination, versions


class TestReclaimReceiptOpenDAL:
    def test_deletes_only_the_saved_physical_version(
        self, opendal_receipt, opendal_destination
    ):
        _, versions = opendal_destination

        assert (
            receipt_cleanup.reclaim_receipt(opendal_receipt)
            is ReceiptReclaimResult.REMOVED
        )
        assert versions.objects == {"replacement": b"new!"}

    def test_accepts_an_already_absent_exact_version(
        self, opendal_receipt, opendal_destination
    ):
        _, versions = opendal_destination
        versions.objects.pop("captured-version")

        assert (
            receipt_cleanup.reclaim_receipt(opendal_receipt)
            is ReceiptReclaimResult.REMOVED
        )
        assert versions.objects == {"replacement": b"new!"}

    @pytest.mark.parametrize(
        "version", [None, "null"], ids=["unversioned", "null-version"]
    )
    def test_refuses_a_receipt_without_immutable_version(
        self, opendal_receipt, opendal_destination, version
    ):
        _, versions = opendal_destination
        receipt = replace(opendal_receipt, version_id=version)

        assert (
            receipt_cleanup.reclaim_receipt(receipt) is ReceiptReclaimResult.UNSUPPORTED
        )
        assert versions.objects == {"captured-version": b"old!", "replacement": b"new!"}

    def test_refuses_a_transport_without_exact_deletion(
        self, monkeypatch, opendal_receipt, opendal_destination
    ):
        destination, versions = opendal_destination
        monkeypatch.setattr(destination.backend, "exact_deletion", None)

        assert (
            receipt_cleanup.reclaim_receipt(opendal_receipt)
            is ReceiptReclaimResult.UNSUPPORTED
        )
        assert versions.objects == {"captured-version": b"old!", "replacement": b"new!"}

    def test_preserves_versions_when_exact_delete_is_refused(
        self, monkeypatch, opendal_receipt, opendal_destination
    ):
        from app.modules.storage.storage_backend.contracts import (
            StorageConfigurationError,
        )

        _, versions = opendal_destination

        def refused(*_args):
            raise StorageConfigurationError("versioned_delete_unavailable")

        monkeypatch.setattr(versions, "delete_versioned", refused)

        assert (
            receipt_cleanup.reclaim_receipt(opendal_receipt)
            is ReceiptReclaimResult.UNSUPPORTED
        )
        assert versions.objects == {"captured-version": b"old!", "replacement": b"new!"}

    @pytest.mark.parametrize(
        "changed",
        [
            pytest.param({"provider_ref": "other-profile"}, id="provider"),
            pytest.param({"namespace": "other/prefix"}, id="namespace"),
        ],
    )
    def test_preserves_versions_when_saved_destination_is_missing(
        self, opendal_receipt, opendal_destination, changed
    ):
        _, versions = opendal_destination
        receipt = replace(opendal_receipt, **changed)

        with pytest.raises(RuntimeError, match="backup_delete_provider_unavailable"):
            receipt_cleanup.reclaim_receipt(receipt)
        assert versions.objects == {"captured-version": b"old!", "replacement": b"new!"}
