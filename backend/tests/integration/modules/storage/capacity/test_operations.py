"""Heavy workflows deny before allocating when headroom cannot be retained."""

import pytest

from app.core.config import _overlay
from app.core.errors import OperationError
from app.db.models import FileType
from app.modules.ingestion.library_transfer import create_archive, import_archive


class TestOperationAdmission:
    def test_denies_archive_export_before_allocation(
        self, db_session, make_user, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            create_archive(db_session, make_user(superuser=True))

    def test_denies_archive_import_before_allocation(
        self, db_session, make_user, monkeypatch
    ):
        user = make_user(superuser=True)
        archive = create_archive(db_session, user)
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        try:
            with pytest.raises(OperationError, match="storage_capacity_exceeded"):
                import_archive(db_session, archive, user)
        finally:
            archive.unlink()

    def test_denies_mesh_rendering_before_materialization(
        self, db_session, make_model, make_file, monkeypatch
    ):
        from app.modules.derivatives import producers
        from app.modules.media.source_preparation import SourceBatch

        artifact = make_file(make_model(), file_type=FileType.STL)
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        monkeypatch.setattr(
            SourceBatch,
            "materialize",
            lambda *_args, **_kwargs: pytest.fail(
                "artifact materialized without capacity"
            ),
        )
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            producers._derive_mesh(artifact.id)

    def test_denies_backup_before_archive_allocation(self, backup_env, monkeypatch):
        from app.modules.backups.backup.creation import create_backup

        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            create_backup()

    def test_denies_restore_before_download(self, backup_env, monkeypatch):
        from app.modules.backups.backup.creation import create_backup
        from app.modules.backups.backup.restore import restore_backup

        meta = create_backup()
        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            restore_backup(meta.id)

    def test_denies_backup_upload_before_staging(self, backup_env, monkeypatch):
        from io import BytesIO

        from app.modules.backups.backup import adoption

        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        monkeypatch.setattr(
            adoption.tempfile,
            "mkstemp",
            lambda *args, **kwargs: pytest.fail("backup staging was allocated"),
        )

        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            adoption.upload_backup_archive(
                "printstash-backup-capacity.tar.gz",
                BytesIO(b"archive"),
                expected_size=7,
            )

    def test_denies_browser_upload_before_staging(
        self, db_session, make_user, monkeypatch
    ):
        from io import BytesIO

        from app.modules.ingestion.inbox import create_browser_upload

        monkeypatch.setitem(_overlay, "storage_min_free_bytes", 10**18)
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            create_browser_upload(
                db_session,
                make_user(superuser=True),
                source_url="https://makerworld.com/en/models/42",
                title=None,
                capture_source=None,
                filename="model.stl",
                stream=BytesIO(b"solid"),
            )

    def test_reserves_archive_extraction_on_the_staging_volume(self, db_session):
        # Entries are extracted under staging, on the library's mount, so each
        # is published by hard link; the space must be checked where it lands.
        from pathlib import Path

        from app.core.config import settings
        from app.modules.storage import capacity_estimates

        extraction = next(
            resource
            for resource in capacity_estimates.archive_import(83)
            if resource.role == "archive extraction"
        )

        assert extraction.path == str(Path(settings.staging_dir).absolute())

    def test_reserves_remote_backup_source_volume(
        self, db_session, monkeypatch, tmp_path
    ):
        import tempfile
        from pathlib import Path

        from app.modules.storage import capacity_estimates
        from app.modules.storage.capacity import CapacityResource
        from app.modules.storage.storage_backend import runtime
        from app.modules.storage.storage_backend.s3 import S3StorageBackend

        monkeypatch.setitem(_overlay, "s3_bucket", "capacity-test")
        monkeypatch.setitem(_overlay, "s3_access_key", "test-access")
        monkeypatch.setitem(_overlay, "s3_secret_key", "test-secret")
        monkeypatch.setattr(runtime, "_backend", S3StorageBackend(check_bucket=False))
        resources = capacity_estimates.backup_create(83, tmp_path)
        scratch = next(r for r in resources if r.role == "backup remote source")
        expected = CapacityResource.for_path(
            Path(tempfile.gettempdir()), 83, role="test"
        )
        assert scratch.path == expected.path
        assert scratch.domain_id == expected.domain_id
        assert scratch.required_bytes == 83
