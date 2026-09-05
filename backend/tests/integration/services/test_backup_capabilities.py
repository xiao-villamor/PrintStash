"""Backup actions require ownership and an identity-bound deletion operation."""

from types import SimpleNamespace

import pytest

from app.db.models import StorageObjectState
from app.services import backup_capabilities
from app.services.backup import BackupMeta
from tests.factories import build_owned_storage_object


def _meta(location="opendal:s3"):
    return BackupMeta(
        id="archive",
        created_at="2026-09-01",
        size_bytes=3,
        storage_backend="local",
        file_count=1,
        app_version="0.13.0",
        path="backups/archive.tar.gz",
        location=location,
        archive_sha256="d" * 64,
        provider_ref="provider",
        source_ref="source",
        namespace="backups",
    )


class TestBackupOperations:
    @pytest.mark.parametrize(
        "version,supported,allowed",
        [
            (None, True, False),
            ("null", True, False),
            ("v1", False, False),
            ("v1", True, True),
        ],
    )
    def test_remote_deletion_requires_immutable_identity_support(
        self, db_session, monkeypatch, version, supported, allowed
    ):
        build_owned_storage_object(
            db_session,
            namespace="backups",
            key="backups/archive.tar.gz",
            object_kind="backup",
            provider_ref="provider",
            sha256="d" * 64,
            version_id=version,
        )
        monkeypatch.setattr(
            backup_capabilities,
            "destination_for_ownership",
            lambda _: SimpleNamespace(
                backend=SimpleNamespace(exact_deletion=object() if supported else None)
            ),
        )

        result = backup_capabilities.backup_operations(_meta())

        assert result["physical_delete"]["allowed"] is allowed
        assert result["automatic_retention"]["allowed"] is allowed
        assert not result["catalog_purge"]["allowed"]
        assert result["gc_witness"]["reason"] == "storage_independent_backup_required"

    @pytest.mark.parametrize(
        "missing", ["row", "token", "digest", "destination", "committed"]
    )
    def test_incomplete_ownership_cannot_enable_remote_deletion(
        self, db_session, monkeypatch, missing
    ):
        if missing != "row":
            build_owned_storage_object(
                db_session,
                namespace="backups",
                key="backups/archive.tar.gz",
                object_kind="backup",
                provider_ref="provider",
                sha256=None if missing == "digest" else "d" * 64,
                version_id="v1",
                token=None if missing == "token" else "token",
                state=StorageObjectState.PENDING
                if missing == "committed"
                else StorageObjectState.COMMITTED,
            )
        monkeypatch.setattr(
            backup_capabilities, "destination_for_ownership", lambda _: None
        )

        result = backup_capabilities.backup_operations(_meta())

        assert result["physical_delete"] == {
            "allowed": False,
            "reason": "storage_exact_delete_unavailable",
            "confirmation_required": False,
        }

    @pytest.mark.parametrize(
        "location,identity,allowed",
        [
            ("local", {}, False),
            ("local", {"device": 1, "inode": 2}, True),
            ("s3", {}, False),
            ("s3", {"etag": "tag"}, True),
            ("s3", {"version_id": "v1"}, True),
        ],
    )
    def test_native_sources_keep_their_exact_deletion_contract(
        self, db_session, monkeypatch, location, identity, allowed
    ):
        build_owned_storage_object(
            db_session,
            namespace="backups",
            key="backups/archive.tar.gz",
            object_kind="backup",
            provider_ref="provider",
            sha256="d" * 64,
            **identity,
        )
        monkeypatch.setattr(
            "app.services.gc_planner._source_identity_evidence", lambda _: None
        )

        result = backup_capabilities.backup_operations(_meta(location))

        assert result["physical_delete"]["allowed"] is allowed

    @pytest.mark.parametrize("location", ["s3", "opendal:s3"])
    def test_independent_backup_still_requires_gc_reverification(
        self, db_session, monkeypatch, location
    ):
        monkeypatch.setattr(
            "app.services.gc_planner._source_identity_evidence", lambda _: ({}, {})
        )
        result = backup_capabilities.backup_operations(_meta(location))
        assert result["gc_witness"] == {
            "allowed": False,
            "reason": "storage_gc_verification_required",
            "confirmation_required": False,
        }
