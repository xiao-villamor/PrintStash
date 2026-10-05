"""Storage inventory distinguishes references, owned objects and unknown evidence."""

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import (
    FileType,
    User,
    VaultAuditFinding,
    VaultAuditMode,
    VaultAuditRun,
    VaultAuditRunState,
    VaultAuditSeverity,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_inventory import (
    capacity_activity,
    history,
    inventory,
    logical_drilldown,
    record_sample,
)
from tests.factories import build_storage_delete_intent


class TestInventory:
    @pytest.mark.parametrize("kind", list(FileType), ids=lambda kind: kind.value)
    def test_classifies_artifact_types(self, db_session, make_model, make_file, kind):
        make_file(make_model(), file_type=kind, size_bytes=73)
        current = inventory(db_session)
        assert (
            next(
                bucket for bucket in current.buckets if bucket.category == kind.value
            ).logical_bytes
            == 73
        )

    def test_separates_trashed_artifacts(self, db_session, make_model, make_file):
        make_file(make_model(trashed=True), size_bytes=53)
        assert (
            next(
                bucket
                for bucket in inventory(db_session).buckets
                if bucket.category == "gcode"
            ).lifecycle
            == "trash"
        )

    def test_excludes_external_bytes_from_owned_total(
        self, db_session, make_model, make_file, make_external_library, tmp_path
    ):
        library = make_external_library(tmp_path / "external")
        make_file(
            make_model(), external=True, external_library_id=library.id, size_bytes=700
        )
        current = inventory(db_session)
        assert current.external_referenced_bytes == 700
        assert current.unique_owned_bytes == 0

    def test_counts_shared_key_once(self, db_session, make_model, make_file):
        backend = get_backend()
        key = backend.blob_key("same", 1, "model.stl")
        make_file(make_model(), path=key, size_bytes=23)
        make_file(make_model(), path=key, size_bytes=23)
        current = inventory(db_session)
        assert current.unique_owned_bytes == 23
        assert current.logical_bytes == 46

    def test_counts_distinct_copies(
        self, db_session, make_model, make_owned_storage_object
    ):
        make_owned_storage_object(
            key="copy-a", namespace="vault", size_bytes=23, sha256="a" * 64
        )
        make_owned_storage_object(
            key="copy-b", namespace="vault", size_bytes=23, sha256="a" * 64
        )
        assert inventory(db_session).unique_owned_bytes == 46

    def test_keeps_pending_delete_bytes_in_owned_total(self, db_session):
        backend = get_backend()
        receipt = backend.create_bytes(b"queued", backend.stl_cache_key("f" * 64))
        build_storage_delete_intent(db_session, backend, receipt, status="pending")

        assert inventory(db_session).unique_owned_bytes == 6

    def test_never_walks_provider_on_read(self, db_session, monkeypatch):
        backend = get_backend()

        def forbid(*args, **kwargs):
            pytest.fail("interactive inventory must not enumerate provider")

        monkeypatch.setattr(backend, "walk_keys", forbid)
        monkeypatch.setattr(backend, "usage", forbid)
        assert inventory(db_session).measured_provider_bytes is None

    def test_refresh_records_typed_local_capacity(self, db_session):
        current = inventory(db_session, refresh_provider=True)

        assert current.schema_version == 1
        assert current.provider_capacity.status == "known"
        assert current.provider_capacity.method == "filesystem_statvfs"
        assert current.provider_capacity.reliability == "exact"
        assert current.provider_capacity.available_bytes is not None

    def test_failed_refresh_retains_last_measurement_as_degraded(
        self, db_session, monkeypatch
    ):
        current = inventory(db_session, refresh_provider=True)
        record_sample(db_session, current)
        backend = get_backend()

        def fail_capacity():
            raise OSError("provider unavailable")

        monkeypatch.setattr(backend, "capacity", fail_capacity)
        degraded = inventory(db_session, refresh_provider=True).provider_capacity

        assert degraded.status == "degraded"
        assert degraded.available_bytes == current.provider_capacity.available_bytes
        assert degraded.measured_at == current.provider_capacity.measured_at
        assert degraded.error == "OSError"

    def test_reports_latest_audit_unclaimed_count_without_object_names(
        self, db_session, make_user
    ):
        user: User = make_user(superuser=True)
        assert user.id is not None
        run = VaultAuditRun(
            requested_by=user.id,
            mode=VaultAuditMode.QUICK,
            state=VaultAuditRunState.COMPLETED,
            finished_at=utcnow(),
            unclaimed_bytes=4096,
            unclaimed_unknown_size_count=1,
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)
        assert run.id is not None
        db_session.add(
            VaultAuditFinding(
                run_id=run.id,
                code="unowned_blob_detected",
                severity=VaultAuditSeverity.INFO,
                resource_type="storage_object",
                resource_identifier="secret-object-name.stl",
            )
        )
        db_session.commit()

        observation = inventory(db_session).latest_audit

        assert observation is not None
        assert observation.run_id == run.id
        assert observation.unclaimed_object_count == 1
        assert observation.unclaimed_bytes == 4096
        assert observation.unknown_size_count == 1
        assert "secret-object-name" not in observation.model_dump_json()

    def test_counts_documents(self, db_session, make_document):
        make_document(filename="guide.pdf", size_bytes=43)
        current = inventory(db_session)
        assert (
            next(
                bucket for bucket in current.buckets if bucket.category == "document"
            ).logical_bytes
            == 43
        )

    def test_collapses_roles_on_shared_volume(self, db_session):
        volumes = inventory(db_session).volumes
        assert len({volume.domain_id for volume in volumes}) == len(volumes)
        assert sum("staging" in volume.roles for volume in volumes) == 1

    def test_replaces_daily_sample(self, db_session):
        current = inventory(db_session)
        record_sample(db_session, current)
        record_sample(db_session, current)
        assert len(history(db_session, current.target_ref)) == 1

    def test_bounds_retention(self, db_session, make_storage_inventory_sample):
        current = inventory(db_session)
        make_storage_inventory_sample(
            target_ref=current.target_ref, sampled_at=utcnow() - timedelta(days=400)
        )
        record_sample(db_session, current)
        assert len(history(db_session, current.target_ref)) == 1

    def test_downsamples_old_hourly_rows_to_one_per_day(
        self, db_session, make_storage_inventory_sample
    ):
        current = inventory(db_session)
        old = utcnow() - timedelta(days=30)
        make_storage_inventory_sample(
            target_ref=current.target_ref, sampled_at=old.replace(hour=8)
        )
        make_storage_inventory_sample(
            target_ref=current.target_ref, sampled_at=old.replace(hour=16)
        )

        record_sample(db_session, current)

        assert len(history(db_session, current.target_ref)) == 2

    def test_history_separates_growth_categories(
        self, db_session, make_model, make_file, make_owned_storage_object
    ):
        make_file(make_model(), file_type=FileType.STL, size_bytes=10)
        make_file(make_model(trashed=True), file_type=FileType.GCODE, size_bytes=20)
        make_owned_storage_object(
            key="backup-one", object_kind="backup_archive", size_bytes=30
        )
        current = inventory(db_session)

        record_sample(db_session, current)

        categories = history(db_session, current.target_ref)[0]["categories"]
        assert categories == {
            "live_originals": 10,
            "trash": 20,
            "derived_cache": 0,
            "backups": 30,
        }

    def test_hides_inaccessible_model_drilldowns(
        self, db_session, make_user, make_model, make_file
    ):
        user = make_user(superuser=False)
        make_file(make_model(), size_bytes=400)
        assert logical_drilldown(db_session, user) == []

    def test_cleanup_reclaims_verified_expired_staging(
        self, db_session, make_user, make_inbox_item, tmp_path
    ):
        import hashlib

        from app.modules.ingestion.staging_leases import create_review_lease
        from app.modules.storage.storage_inventory import cleanup_expired_staging

        user = make_user(superuser=True)
        item = make_inbox_item(user)
        staged = tmp_path / "expired.stl"
        staged.write_bytes(b"old staging")
        create_review_lease(
            db_session,
            inbox_item_id=item.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=11,
            sha256=hashlib.sha256(b"old staging").hexdigest(),
            now=utcnow() - timedelta(days=400),
        )
        db_session.commit()
        assert inventory(db_session).temporary_bytes == 11
        result = cleanup_expired_staging(db_session, user)
        assert result["files_removed"] == 1
        assert result["inventory"].temporary_bytes == 0
        assert not staged.exists()

    def test_aggregates_document_buckets(self, db_session, make_document):
        make_document(filename="a.pdf", size_bytes=11)
        make_document(filename="b.pdf", size_bytes=13)
        documents = [
            b for b in inventory(db_session).buckets if b.category == "document"
        ]
        assert len(documents) == 1
        assert documents[0].count == 2
        assert documents[0].logical_bytes == 24

    def test_counts_backup_receipts(self, db_session, make_owned_storage_object):
        make_owned_storage_object(
            key="backup-one", object_kind="backup_archive", size_bytes=37
        )
        current = inventory(db_session)
        assert current.backup_bytes == 37
        assert next(b for b in current.buckets if b.category == "backups").count == 1

    def test_reports_probe_failure(self, db_session, monkeypatch):
        def unavailable(path):
            raise OSError("unavailable mount")

        monkeypatch.setattr(
            "app.modules.storage.storage_inventory.os.statvfs", unavailable
        )
        assert all(
            v.status == "unavailable" and v.free_bytes is None
            for v in inventory(db_session).volumes
        )

    def test_s3_capacity_is_unknown_without_remote_calls(self, db_session, monkeypatch):
        from app.core.config import _overlay
        from app.modules.storage.storage_backend import runtime
        from app.modules.storage.storage_backend.s3 import S3StorageBackend

        monkeypatch.setitem(_overlay, "s3_bucket", "inventory-test")
        monkeypatch.setitem(_overlay, "s3_access_key", "test-access")
        monkeypatch.setitem(_overlay, "s3_secret_key", "test-secret")
        backend = S3StorageBackend(check_bucket=False)
        monkeypatch.setattr(runtime, "_backend", backend)
        current = inventory(db_session)
        remote = next(v for v in current.volumes if v.domain_id == "remote")
        assert remote.free_bytes is None
        assert remote.status == "unknown"

    def test_uses_receipt_size_for_derivative_bucket(
        self, db_session, make_model, make_file, make_owned_storage_object
    ):
        from app.modules.storage.storage_ownership import provider_ref_for_backend

        file = make_file(make_model())
        backend = get_backend()
        key = backend.thumbnail_key(file.id)
        namespace = backend.namespace_for(key)
        make_owned_storage_object(
            key=key,
            namespace=namespace,
            provider_ref=provider_ref_for_backend(backend, namespace=namespace),
            object_kind="thumbnail",
            size_bytes=29,
        )
        thumbnail = next(
            b for b in inventory(db_session).buckets if b.category == "thumbnail"
        )
        assert thumbnail.logical_bytes == 29

    def test_capacity_activity_omits_operation_identifiers(
        self, db_session, make_capacity_reservation
    ):
        make_capacity_reservation(operation_id="artifact-upload:private-session-id")

        payload = capacity_activity(db_session).model_dump()

        assert payload["active_reservations"][0]["operation_kind"] == "artifact-upload"
        assert "private-session-id" not in str(payload)


class TestRetiredPublicationInventory:
    def test_excludes_retired_history_from_current_byte_totals(
        self, db_session, make_owned_storage_object
    ):
        from app.db.models import StorageObjectState

        make_owned_storage_object(
            state=StorageObjectState.RETIRING, size_bytes=23, object_kind="thumbnail"
        )
        assert inventory(db_session).unique_owned_bytes == 0

    def test_excludes_retired_history_from_legacy_usage(
        self, db_session, make_owned_storage_object
    ):
        from app.db.models import StorageObjectState
        from app.modules.storage.storage_inventory import legacy_usage

        make_owned_storage_object(state=StorageObjectState.RETIRING, size_bytes=23)
        assert legacy_usage(db_session)["object_count"] == 0
        assert legacy_usage(db_session)["total_size_bytes"] == 0

    def test_excludes_retired_backups_from_retention_candidates(
        self, db_session, make_owned_storage_object
    ):
        from datetime import UTC, datetime

        from app.db.models import StorageObjectState
        from app.modules.storage.storage_inventory import cleanup_opportunities

        make_owned_storage_object(
            state=StorageObjectState.RETIRING,
            size_bytes=23,
            object_kind="backup",
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        preview = next(
            row
            for row in cleanup_opportunities(db_session)
            if row["owner"] == "backups"
        )
        assert preview["candidate_count"] == 0
        assert preview["candidate_bytes"] == 0
