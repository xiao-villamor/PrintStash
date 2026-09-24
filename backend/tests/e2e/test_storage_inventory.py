"""Operators can measure storage and run an explicitly audited safe cleanup."""

import hashlib
import shutil
from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import AuditLog, CollectionRole, DocumentKind, FileType, User
from app.modules.ingestion.staging_leases import create_review_lease
from app.modules.storage.capacity import CapacityResource
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import (
    bearer,
    build_collection,
    build_document,
    build_file,
    build_inbox_item,
    build_model,
    build_user,
    grant_collection_role,
    store_owned_bytes,
)
from tests.factories.content import ascii_stl


class TestStorageInventoryCleanup:
    @pytest.mark.asyncio
    async def test_reconciles_every_storage_category_without_double_counting(
        self, api, e2e_db, superuser_headers
    ):
        backend = get_backend()
        for file_type in FileType:
            build_file(
                e2e_db,
                build_model(e2e_db, f"Live {file_type.value}"),
                file_type=file_type,
                size_bytes=10,
            )
        build_file(
            e2e_db,
            build_model(e2e_db, "Trashed", trashed=True),
            file_type=FileType.STL,
            size_bytes=20,
        )
        shared_key = backend.blob_key("shared", 1, "shared.stl")
        for name in ("Shared A", "Shared B"):
            build_file(
                e2e_db,
                build_model(e2e_db, name),
                file_type=FileType.STL,
                path=shared_key,
                size_bytes=17,
                sha256="1" * 64,
            )
        build_document(e2e_db, "Storage manual", kind=DocumentKind.PDF)
        store_owned_bytes(
            e2e_db,
            backend,
            backend.thumbnail_key(777),
            b"thumbnail",
            object_kind="thumbnail",
        )
        store_owned_bytes(
            e2e_db,
            backend,
            backend.stl_cache_key("2" * 64),
            b"derived",
            object_kind="derived_stl_cache",
        )
        store_owned_bytes(
            e2e_db,
            backend,
            backend.blob_key("backups", 1, "snapshot.zip"),
            b"backup",
            object_kind="backup_archive",
        )

        response = await api.get(
            "/api/v1/storage/inventory", headers=superuser_headers
        )

        assert response.status_code == 200
        current = response.json()["inventory"]
        assert {file_type.value for file_type in FileType}.issubset(
            {bucket["category"] for bucket in current["buckets"]}
        )
        assert {
            (bucket["category"], bucket["lifecycle"])
            for bucket in current["buckets"]
        }.issuperset(
            {
                ("stl", "trash"),
                ("thumbnail", "owned"),
                ("stl_cache", "owned"),
                ("document", "owned"),
                ("backups", "replica"),
                ("staging", "temporary"),
            }
        )
        # One 10-byte Artifact per supported format, plus 20 trashed bytes,
        # two 17-byte references to a shared key, and one Document byte.
        assert current["logical_bytes"] == 10 * len(FileType) + 20 + 2 * 17 + 1
        # Only positively managed objects enter the physical total: one copy of
        # the shared 17-byte key, the 1-byte Document and 22 bytes of receipts.
        assert current["unique_owned_bytes"] == 40

    @pytest.mark.asyncio
    async def test_explicit_cleanup_refreshes_insights(
        self, api, e2e_db, superuser_headers, tmp_path, monkeypatch
    ):
        user = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
        item = build_inbox_item(e2e_db, user)
        assert item.id is not None
        assert user.id is not None
        staged = tmp_path / "expired-staging.stl"
        staged_payload = b"x" * (8 * 1024**2)
        staged.write_bytes(staged_payload)
        create_review_lease(
            e2e_db,
            inbox_item_id=item.id,
            owner_user_id=user.id,
            path=staged,
            size_bytes=len(staged_payload),
            sha256=hashlib.sha256(staged_payload).hexdigest(),
            now=utcnow() - timedelta(days=400),
        )
        e2e_db.commit()
        before = await api.get("/api/v1/storage/inventory", headers=superuser_headers)
        assert before.status_code == 200
        assert before.json()["inventory"]["temporary_bytes"] == len(staged_payload)
        baseline_free = shutil.disk_usage(staged.parent).free
        staged_device = staged.stat().st_dev
        real_measure = CapacityResource.measure

        def measure_after_cleanup(resource: CapacityResource):
            available, total = real_measure(resource)
            if resource.domain_id != f"volume:{staged_device}":
                return available, total
            # Overlay filesystems can defer free-space accounting after unlink.
            # Keep the E2E admission boundary deterministic while preserving the
            # real filesystem probe for every other capacity domain.
            delta = -1 if staged.exists() else len(staged_payload)
            return baseline_free + delta, total

        monkeypatch.setattr(CapacityResource, "measure", measure_after_cleanup)
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", baseline_free)
        upload = {
            "file": ("capacity.stl", ascii_stl(), "application/sla")
        }
        denied = await api.post(
            "/api/v1/ingest/model",
            files=upload,
            data={"model_name": "Capacity retry"},
            headers=superuser_headers,
        )
        assert denied.status_code == 507
        measured = await api.post(
            "/api/v1/storage/inventory/sample", headers=superuser_headers
        )
        assert measured.status_code == 200
        result = await api.post(
            "/api/v1/storage/inventory/cleanup-staging", headers=superuser_headers
        )
        assert result.status_code == 200
        assert result.json()["files_removed"] == 1
        assert not staged.exists()
        after = await api.get("/api/v1/storage/inventory", headers=superuser_headers)
        assert len(after.json()["history"]) == 1
        assert after.json()["inventory"]["temporary_bytes"] == 0
        assert after.json()["inventory"]["measured_at"] is not None
        accepted = await api.post(
            "/api/v1/ingest/model",
            files={"file": ("capacity.stl", ascii_stl(), "application/sla")},
            data={"model_name": "Capacity retry"},
            headers=superuser_headers,
        )
        assert accepted.status_code == 202, accepted.text
        assert (
            e2e_db.exec(
                select(AuditLog).where(
                    AuditLog.action == "storage.cleanup_expired_staging"
                )
            ).first()
            is not None
        )

    @pytest.mark.asyncio
    async def test_non_admin_cannot_infer_private_collection_or_model_storage(
        self, api, e2e_db, superuser_headers
    ):
        del superuser_headers
        reader = build_user(e2e_db, "storage-reader")
        visible = build_collection(e2e_db, "Visible collection")
        private = build_collection(e2e_db, "Private collection")
        grant_collection_role(e2e_db, reader, visible, CollectionRole.VIEW)
        build_file(
            e2e_db,
            build_model(e2e_db, "Visible model", collection=visible),
            size_bytes=20,
        )
        build_file(
            e2e_db,
            build_model(e2e_db, "Private model", collection=private),
            size_bytes=9000,
        )

        collections = await api.get(
            "/api/v1/storage/inventory/collections", headers=bearer(reader)
        )
        models = await api.get(
            f"/api/v1/storage/inventory/models?collection_id={visible.id}",
            headers=bearer(reader),
        )

        assert collections.status_code == 200
        assert collections.json() == [
            {
                "collection_id": visible.id,
                "name": "Visible collection",
                "logical_bytes": 20,
                "external_bytes": 0,
                "model_count": 1,
            }
        ]
        assert models.status_code == 200
        assert models.json()[0]["name"] == "Visible model"
        assert "Private" not in collections.text + models.text
