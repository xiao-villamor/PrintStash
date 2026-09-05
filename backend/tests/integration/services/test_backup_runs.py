"""Selected destinations remain visible even when construction fails."""

import pytest
from sqlmodel import select

from app.db.models import StorageConnectionPurpose
from app.services import backup
from tests.factories import build_storage_connection


class TestDurableBackupRuns:
    def test_invalid_selected_profile_is_a_durable_partial_result(self, backup_env):
        from app.db.models import BackupDestinationResult, BackupRun

        with backup_env.new_session() as session:
            profile = build_storage_connection(
                session, purpose=StorageConnectionPurpose.BACKUP
            )
            profile.config_json = '{"invalid": true}'
            profile.manual_backup_enabled = True
            session.add(profile)
            session.commit()
            profile_id = profile.id
        meta = backup.create_backup()
        with backup_env.new_session() as session:
            run = session.get(BackupRun, meta.run_id)
            results = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == run.id
                )
            ).all()
            assert run.outcome == "partial"
            assert len(results) == 2
            failed = next(row for row in results if row.connection_id == profile_id)
            assert failed.outcome == "failed"
            assert failed.error_code == "storage_connection_invalid"
            success = next(row for row in results if row.kind == "local")
            assert success.outcome == "completed"
            assert success.ownership_id is not None
            assert success.verified_at is None

    def test_all_failed_response_retains_the_durable_run(self, backup_env, monkeypatch):
        from app.db.models import BackupRun, SystemConfig

        with backup_env.new_session() as session:
            session.add(SystemConfig(id=1, manual_local_backup_enabled=False))
            profile = build_storage_connection(
                session, purpose=StorageConnectionPurpose.BACKUP
            )
            profile.config_json = "{}"
            profile.manual_backup_enabled = True
            session.add(profile)
            session.commit()
        with pytest.raises(
            RuntimeError, match="backup_all_destinations_failed"
        ) as error:
            backup.create_backup()
        with backup_env.new_session() as session:
            run = session.get(BackupRun, error.value.run_id)
            assert run.outcome == "failed"
            assert run.finished_at is not None

    def test_retry_requires_a_verified_surviving_archive(self, backup_env):
        from app.db.models import BackupDestinationResult
        from app.services import backup_runs

        with backup_env.new_session() as session:
            profile = build_storage_connection(
                session, purpose=StorageConnectionPurpose.BACKUP
            )
            profile.config_json = "{}"
            session.add(profile)
            session.commit()
        meta = backup.create_backup()
        with backup_env.new_session() as session:
            failed = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == meta.run_id,
                    BackupDestinationResult.outcome == "failed",
                )
            ).one()
            result_id = failed.id
        from pathlib import Path

        Path(meta.path).unlink()
        with pytest.raises(RuntimeError, match="backup_retry_new_backup_required"):
            backup_runs.retry_destination(result_id)
        assert list(backup_env.backup_dir.glob("*.tar.gz")) == []


@pytest.mark.s3
class TestExactReplicaRetry:
    def test_retry_publishes_the_same_surviving_archive_digest(
        self, backup_env, monkeypatch
    ):
        import hashlib
        import json
        from uuid import uuid4

        import boto3
        from botocore.config import Config

        from app.db.models import BackupDestinationResult, LibrarySourceKind
        from app.services import backup_runs
        from app.services.remote_io_adapters import OpenDALRemoteIO
        from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint

        endpoint = s3_endpoint()
        bucket = f"retry-{uuid4().hex[:12]}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            config=Config(s3={"addressing_style": "path"}),
        )
        client.create_bucket(Bucket=bucket)
        with backup_env.new_session() as session:
            profile = build_storage_connection(
                session, purpose=StorageConnectionPurpose.BACKUP
            )
            profile.kind = LibrarySourceKind.S3
            profile.config_json = json.dumps(
                {
                    "provider": "s3_self_hosted",
                    "endpoint_url": endpoint,
                    "bucket": bucket,
                    "region": "us-east-1",
                    "root": "replicas",
                    "addressing_style": "path",
                }
            )
            profile.secret_json = json.dumps(
                {"access_key": S3_ACCESS_KEY, "secret_key": S3_SECRET_KEY}
            )
            session.add(profile)
            session.commit()
        original = OpenDALRemoteIO.publish_replica

        def offline(*_args, **_kwargs):
            raise OSError("offline secret-must-not-escape")

        monkeypatch.setattr(OpenDALRemoteIO, "publish_replica", offline)
        meta = backup.create_backup()
        assert meta.outcome == "partial"
        with backup_env.new_session() as session:
            result = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == meta.run_id,
                    BackupDestinationResult.kind == "connection",
                )
            ).one()
            result_id, key = result.id, result.key
        monkeypatch.setattr(OpenDALRemoteIO, "publish_replica", original)
        retried = backup_runs.retry_destination(result_id)
        assert retried["outcome"] == "completed"
        body = client.get_object(Bucket=bucket, Key=key.removeprefix(f"s3/{bucket}/"))["Body"]
        try:
            assert hashlib.sha256(body.read()).hexdigest() == meta.archive_sha256
        finally:
            body.close()
        detail = backup_runs.run_detail(meta.run_id)
        assert detail["outcome"] == "completed"
        assert (
            next(row for row in detail["destinations"] if row["kind"] == "local")[
                "verified_at"
            ]
            is not None
        )
        assert "secret-must-not-escape" not in json.dumps(detail)
