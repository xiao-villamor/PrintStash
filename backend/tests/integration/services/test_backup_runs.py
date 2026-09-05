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

    def test_configured_path_spelling_remains_listable(self, backup_env, monkeypatch):
        from app.core.config import _overlay

        configured = backup_env.backup_dir / ".." / backup_env.backup_dir.name
        monkeypatch.setitem(_overlay, "backup_dir", configured)
        meta = backup.create_backup()
        assert meta.source_ref in {
            row.source_ref for row in backup.list_backup_sources()
        }
        assert meta.path.startswith(str(configured))
        assert backup.verify_backup(meta.id, source_ref=meta.source_ref).valid

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


def _retry_target(backup_env):
    import json
    from uuid import uuid4

    import boto3
    from botocore.config import Config

    from app.db.models import LibrarySourceKind
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
    return client, bucket


@pytest.mark.s3
class TestExactReplicaRetry:
    def test_retry_publishes_the_same_surviving_archive_digest(
        self, backup_env, monkeypatch
    ):
        import hashlib
        import json

        from app.db.models import BackupDestinationResult
        from app.services import backup_runs
        from app.services.remote_io_adapters import OpenDALRemoteIO

        client, bucket = _retry_target(backup_env)
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
        body = client.get_object(Bucket=bucket, Key=key.removeprefix(f"s3/{bucket}/"))[
            "Body"
        ]
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

    def test_retry_reconciles_a_write_committed_before_receipt_recording(
        self, backup_env, monkeypatch
    ):
        from app.db.models import BackupDestinationResult
        from app.services import backup_runs
        from app.services.remote_io_adapters import OpenDALRemoteIO

        _retry_target(backup_env)
        original = OpenDALRemoteIO.publish_replica
        writes = []

        def interrupted(backend, source, key):
            original(backend, source, key)
            writes.append(key)
            raise OSError("lost publication response")

        monkeypatch.setattr(OpenDALRemoteIO, "publish_replica", interrupted)
        meta = backup.create_backup()
        with backup_env.new_session() as session:
            failed = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == meta.run_id,
                    BackupDestinationResult.kind == "connection",
                )
            ).one()
            result_id = failed.id
        retried = backup_runs.retry_destination(result_id)
        assert retried["outcome"] == "completed"
        assert retried["ownership_id"] is not None
        assert len(writes) == 1
        assert retried["retry_attempts"][0]["source_result_id"] == result_id
        assert retried["retry_attempts"][0]["outcome"] == "completed"
        assert backup_runs.run_detail(meta.run_id)["outcome"] == "completed"


class TestRunVerification:
    def test_successful_verification_updates_only_the_verified_destination(
        self, backup_env
    ):
        from app.services import backup_runs

        meta = backup.create_backup()
        assert (
            backup_runs.run_detail(meta.run_id)["destinations"][0]["verified_at"]
            is None
        )
        result = backup.verify_backup(meta.id, source_ref=meta.source_ref)
        assert result.valid is True
        assert (
            backup_runs.run_detail(meta.run_id)["destinations"][0]["verified_at"]
            is not None
        )

    def test_interrupted_run_remains_visible_as_a_failed_execution(self, backup_env):
        from app.core.time import utcnow
        from app.services import backup_runs
        from app.services.backup_destination import BackupTrigger

        selection = backup_runs.begin_run(
            backup_id="interrupted",
            archive_name="interrupted.tar.gz",
            trigger=BackupTrigger.MANUAL,
            created_at=utcnow(),
        )
        listing = backup_runs.list_runs()
        run = next(row for row in listing if row["id"] == selection.run_id)
        assert run["outcome"] == "failed"
        assert run["destinations"][0]["error_code"] == "backup_publication_interrupted"


def _failed_s3_run(backup_env, monkeypatch):
    from app.db.models import BackupDestinationResult
    from app.services.remote_io_adapters import OpenDALRemoteIO

    client, bucket = _retry_target(backup_env)
    original = OpenDALRemoteIO.publish_replica

    def offline(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(OpenDALRemoteIO, "publish_replica", offline)
    meta = backup.create_backup()
    monkeypatch.setattr(OpenDALRemoteIO, "publish_replica", original)
    with backup_env.new_session() as session:
        result = session.exec(
            select(BackupDestinationResult).where(
                BackupDestinationResult.run_id == meta.run_id,
                BackupDestinationResult.kind == "connection",
            )
        ).one()
        return meta, result.id, result.key, client, bucket


@pytest.mark.s3
class TestRetryIntegrity:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("root", "edited-root"),
            ("bucket", "edited-bucket"),
            ("endpoint_url", "http://changed.invalid"),
        ],
    )
    def test_target_edits_cannot_redirect_a_retry(
        self, backup_env, monkeypatch, field, value
    ):
        import json

        from app.db.models import StorageConnection
        from app.services import backup_runs

        meta, result_id, key, client, bucket = _failed_s3_run(backup_env, monkeypatch)
        with backup_env.new_session() as session:
            profile = session.exec(select(StorageConnection)).one()
            configuration = json.loads(profile.config_json)
            configuration[field] = value
            profile.config_json = json.dumps(configuration)
            session.add(profile)
            session.commit()
        with pytest.raises(RuntimeError, match="backup_retry_target_changed"):
            backup_runs.retry_destination(result_id)
        assert client.list_objects_v2(Bucket=bucket).get("KeyCount", 0) == 0
        assert backup_runs.run_detail(meta.run_id)["outcome"] == "partial"

    def test_corrupted_surviving_bytes_cannot_seed_a_retry(
        self, backup_env, monkeypatch
    ):
        from pathlib import Path

        from app.services import backup_runs

        meta, result_id, key, client, bucket = _failed_s3_run(backup_env, monkeypatch)
        archive = Path(meta.path)
        replacement = b"x" * archive.stat().st_size
        archive.write_bytes(replacement)
        with pytest.raises(RuntimeError, match="backup_retry_new_backup_required"):
            backup_runs.retry_destination(result_id)
        assert archive.read_bytes() == replacement
        assert client.list_objects_v2(Bucket=bucket).get("KeyCount", 0) == 0

    def test_existing_replacement_bytes_survive_a_retry(self, backup_env, monkeypatch):
        from app.services import backup_runs

        meta, result_id, key, client, bucket = _failed_s3_run(backup_env, monkeypatch)
        object_key = key.removeprefix(f"s3/{bucket}/")
        client.put_object(Bucket=bucket, Key=object_key, Body=b"replacement")
        with pytest.raises(RuntimeError, match="backup_retry_publication_conflict"):
            backup_runs.retry_destination(result_id)
        body = client.get_object(Bucket=bucket, Key=object_key)["Body"]
        try:
            assert body.read() == b"replacement"
        finally:
            body.close()

    def test_competing_retries_publish_one_replica(self, backup_env, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context
        from threading import Event

        from app.services import backup_runs
        from app.services.remote_io_adapters import OpenDALRemoteIO

        meta, result_id, key, client, bucket = _failed_s3_run(backup_env, monkeypatch)
        entered, release = Event(), Event()
        writes = []
        original = OpenDALRemoteIO.publish_replica

        def paused(backend, source, target_key):
            writes.append(target_key)
            entered.set()
            assert release.wait(10)
            return original(backend, source, target_key)

        monkeypatch.setattr(OpenDALRemoteIO, "publish_replica", paused)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                copy_context().run, backup_runs.retry_destination, result_id
            )
            assert entered.wait(10)
            second = pool.submit(
                copy_context().run, backup_runs.retry_destination, result_id
            )
            release.set()
            assert first.result(timeout=20)["outcome"] == "completed"
            with pytest.raises(RuntimeError, match="backup_retry_not_failed"):
                second.result(timeout=20)
        assert writes == [key]


@pytest.mark.s3
class TestRetryOtherDestinations:
    @pytest.mark.parametrize(
        "failed_kind", ["local", "native", "native_without_reservation"]
    )
    def test_remote_survivor_repairs_the_original_destination(
        self, backup_env, monkeypatch, failed_kind
    ):
        import hashlib
        from pathlib import Path

        from app.core.config import _overlay
        from app.db.models import BackupDestinationResult, SystemConfig
        from app.services import backup_replication, backup_runs
        from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint

        client, bucket = _retry_target(backup_env)
        if failed_kind == "local":
            original = backup_replication.publish_file

            def unavailable(*_args, **_kwargs):
                raise OSError("local unavailable")

            monkeypatch.setattr(backup_replication, "publish_file", unavailable)
        else:
            with backup_env.new_session() as session:
                session.add(SystemConfig(id=1, manual_local_backup_enabled=False))
                session.commit()
            for name, value in {
                "backup_s3_bucket": bucket,
                "backup_s3_endpoint_url": s3_endpoint(),
                "backup_s3_region": "us-east-1",
                "backup_s3_access_key": S3_ACCESS_KEY,
                "backup_s3_secret_key": S3_SECRET_KEY,
            }.items():
                monkeypatch.setitem(_overlay, name, value)
            monkeypatch.setattr(backup, "_get_backup_s3", lambda: client)
            original = client.put_object

            def unavailable(**_kwargs):
                raise OSError("native S3 unavailable")

            monkeypatch.setattr(client, "put_object", unavailable)
        meta = backup.create_backup()
        assert meta.location == "opendal:s3"
        assert meta.outcome == "partial"
        with backup_env.new_session() as session:
            result = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == meta.run_id,
                    BackupDestinationResult.kind
                    == ("local" if failed_kind == "local" else "s3"),
                )
            ).one()
            result_id, key = result.id, result.key
            if failed_kind == "native_without_reservation":
                from app.db.models import OwnedStorageObject

                row = session.exec(
                    select(OwnedStorageObject).where(OwnedStorageObject.key == key)
                ).one()
                session.delete(row)
                session.commit()
        if failed_kind == "local":
            monkeypatch.setattr(backup_replication, "publish_file", original)
        else:
            monkeypatch.setattr(client, "put_object", original)
        assert backup_runs.retry_destination(result_id)["outcome"] == "completed"
        if failed_kind == "local":
            payload = Path(key).read_bytes()
        else:
            body = client.get_object(Bucket=bucket, Key=key)["Body"]
            try:
                payload = body.read()
            finally:
                body.close()
        assert hashlib.sha256(payload).hexdigest() == meta.archive_sha256
        assert backup_runs.run_detail(meta.run_id)["outcome"] == "completed"

    def test_cancelled_retry_releases_private_materialization(
        self, backup_env, monkeypatch
    ):
        import asyncio

        from app.db.models import BackupRetryAttempt
        from app.services import backup_replica_retry, backup_runs

        meta, result_id, key, client, bucket = _failed_s3_run(backup_env, monkeypatch)
        temporary_directories = []

        def cancelled(_reader, path, _run):
            temporary_directories.append(path.parent)
            raise asyncio.CancelledError()

        monkeypatch.setattr(backup_replica_retry, "_copy_exact", cancelled)
        with pytest.raises(asyncio.CancelledError):
            backup_runs.retry_destination(result_id)
        assert temporary_directories
        assert all(not directory.exists() for directory in temporary_directories)
        with backup_env.new_session() as session:
            attempt = session.exec(select(BackupRetryAttempt)).one()
            assert attempt.outcome == "failed"
            assert attempt.finished_at is not None
        assert client.list_objects_v2(Bucket=bucket).get("KeyCount", 0) == 0


class TestRetryTargetEvidence:
    def test_drive_without_account_identity_cannot_be_rebound(self, backup_env):
        from app.db.models import BackupDestinationResult
        from app.services.backup_replica_retry import RetryRefused, binding_for
        from app.services.storage_identity import StorageTargetIdentity

        result = BackupDestinationResult(
            id="drive-result",
            run_id="drive-run",
            kind="connection",
            name="Drive",
            key="gdrive/archive",
            namespace="gdrive",
            provider_ref="saved-profile",
            target_identity_json=StorageTargetIdentity(
                transport="gdrive", endpoint="https://www.googleapis.com"
            ).model_dump_json(),
        )
        with pytest.raises(RetryRefused, match="backup_retry_target_unverified"):
            binding_for(result)


@pytest.mark.s3
class TestNativeSurvivor:
    def test_native_survivor_repairs_a_failed_local_copy(self, backup_env, monkeypatch):
        from pathlib import Path

        from app.core.config import _overlay
        from app.db.models import StorageConnection
        from app.services import backup_replication, backup_runs
        from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint

        client, bucket = _retry_target(backup_env)
        with backup_env.new_session() as session:
            for profile in session.exec(select(StorageConnection)).all():
                profile.enabled = False
                session.add(profile)
            session.commit()
        for key, value in {
            "backup_s3_bucket": bucket,
            "backup_s3_endpoint_url": s3_endpoint(),
            "backup_s3_region": "us-east-1",
            "backup_s3_access_key": S3_ACCESS_KEY,
            "backup_s3_secret_key": S3_SECRET_KEY,
        }.items():
            monkeypatch.setitem(_overlay, key, value)
        original = backup_replication.publish_file

        def unavailable(*args, **kwargs):
            raise OSError("local unavailable")

        monkeypatch.setattr(backup_replication, "publish_file", unavailable)
        meta = backup.create_backup()
        assert meta.location == "s3"
        assert meta.outcome == "partial"
        local = next(row for row in meta.destination_results if row["kind"] == "local")
        monkeypatch.setattr(backup_replication, "publish_file", original)
        result = backup_runs.retry_destination(local["id"])
        assert result["outcome"] == "completed"
        assert Path(result["key"]).is_file()
        assert backup.verify_backup(meta.id, source_ref=result["source_ref"]).valid


class TestLiveSurvivorBoundaries:
    @pytest.fixture
    def survivor(self, backup_env):
        from app.db.models import BackupDestinationResult, BackupRun

        meta = backup.create_backup()
        with backup_env.new_session() as session:
            run = session.get(BackupRun, meta.run_id)
            result = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == run.id
                )
            ).one()
            return BackupRun.model_validate(
                run.model_dump()
            ), BackupDestinationResult.model_validate(result.model_dump())

    @pytest.mark.parametrize(
        "field,value",
        [
            ("namespace", "wrong"),
            ("provider_ref", "wrong"),
            ("key", "other"),
            ("sha256", "b" * 64),
            ("size_bytes", 0),
        ],
    )
    def test_mismatched_ownership_cannot_supply_retry_bytes(
        self, backup_env, survivor, field, value
    ):
        from app.db.models import OwnedStorageObject
        from app.services.backup_replica_retry import RetryRefused, owned_for

        run, result = survivor
        with backup_env.new_session() as session:
            owned = session.get(OwnedStorageObject, result.ownership_id)
            setattr(owned, field, value)
            session.add(owned)
            session.commit()
        with pytest.raises(RetryRefused, match="backup_retry_source_unverified"):
            owned_for(result, run)

    @pytest.mark.parametrize(
        "change", ["replaced_inode", "during_read", "incompatible"]
    )
    def test_changed_live_local_copy_is_rejected(
        self, backup_env, survivor, monkeypatch, change
    ):
        import os
        from pathlib import Path
        from types import SimpleNamespace

        from app.services import backup_replica_retry

        run, result = survivor
        path = Path(result.key)
        if change == "replaced_inode":
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        elif change == "during_read":
            original = backup_replica_retry._copy_exact

            def changing(reader, destination, selected_run):
                original(reader, destination, selected_run)
                stat = path.stat()
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))

            monkeypatch.setattr(backup_replica_retry, "_copy_exact", changing)
        else:
            monkeypatch.setattr(
                backup,
                "verify_backup",
                lambda *args, **kwargs: SimpleNamespace(
                    valid=True, app_compatible=False
                ),
            )
        with pytest.raises(backup_replica_retry.RetryRefused):
            with backup_replica_retry.verified_survivor(result, run):
                pytest.fail("changed source was yielded")
        from app.services import backup_runs

        assert backup_runs.run_detail(run.id)["destinations"][0]["verified_at"] is None


class TestUnavailableRetryBindings:
    @pytest.mark.parametrize(
        "change,reason",
        [
            ("missing_evidence", "backup_retry_target_unverified"),
            ("local_directory", "backup_retry_target_changed"),
            ("native_removed", "backup_retry_target_unavailable"),
            ("profile_deleted", "backup_retry_target_unavailable"),
            ("profile_disabled", "backup_retry_target_unavailable"),
            ("profile_invalid", "backup_retry_target_unavailable"),
        ],
    )
    def test_saved_target_cannot_be_resolved_unsafely(
        self, backup_env, monkeypatch, change, reason
    ):
        from app.core.config import _overlay
        from app.db.models import BackupDestinationResult
        from app.services.backup_replica_retry import RetryRefused, binding_for

        meta = backup.create_backup()
        with backup_env.new_session() as session:
            row = session.exec(
                select(BackupDestinationResult).where(
                    BackupDestinationResult.run_id == meta.run_id
                )
            ).one()
            result = BackupDestinationResult.model_validate(row.model_dump())
            if change.startswith("profile_"):
                profile = build_storage_connection(session)
                result.kind, result.connection_id = "connection", profile.id
                if change == "profile_deleted":
                    session.delete(profile)
                else:
                    if change == "profile_disabled":
                        profile.enabled = False
                    else:
                        profile.config_json = "{}"
                    session.add(profile)
                session.commit()
        if change == "missing_evidence":
            result.target_identity_json = None
        elif change == "local_directory":
            monkeypatch.setitem(
                _overlay, "backup_dir", str(backup_env.backup_dir / "other")
            )
        elif change == "native_removed":
            result.kind = "s3"
        with pytest.raises(RetryRefused, match=reason):
            binding_for(result)
