"""Migration receipts and key translation against real versioned S3 storage."""

import uuid

import boto3
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import _overlay, settings
from app.db.models import File
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.modules.administration.runtime_config import update_storage_provider
from app.modules.backups.backup.creation import create_backup
from app.modules.storage import vault_migration
from app.modules.storage.storage_backend.factory import build_configured_backend
from app.modules.storage.storage_backend.runtime import bind_backend, get_backend
from app.modules.storage.storage_providers import parse_provider_config
from app.runtime.maintenance import end_restore_maintenance
from tests._env import use_local_storage
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint
from tests.factories import build_model, build_stored_file, build_user

pytestmark = pytest.mark.s3


@pytest.fixture
def migration_database(tmp_path):
    previous = get_session_factory()
    use_local_storage(tmp_path)
    _overlay["backup_dir"] = tmp_path / "backups"
    _overlay["backup_dir"].mkdir()
    _overlay["secrets_key"] = "migration-contract-only-key"
    url = f"sqlite:///{tmp_path / 'migration.sqlite'}"
    _overlay["db_url"] = url
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    factory = SQLiteSessionFactory(engine)
    override_session_factory(factory)
    try:
        with Session(engine) as session:
            yield session
    finally:
        for run_id in list(vault_migration._retentions):
            vault_migration._release(run_id)
        end_restore_maintenance()
        override_session_factory(previous)
        engine.dispose()


def remote_config():
    bucket = "migration-" + uuid.uuid4().hex[:16]
    client = boto3.client(
        "s3",
        endpoint_url=s3_endpoint(),
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    # The container fixture owns these unique disposable buckets and its teardown.
    return {
        "provider": "s3",
        "bucket": bucket,
        "endpoint_url": s3_endpoint(),
        "region": "us-east-1",
        "access_key": S3_ACCESS_KEY,
        "secret_key": S3_SECRET_KEY,
        "root": "migration-vault",
    }


class TestVaultProviderMigration:
    @pytest.mark.parametrize(
        "source_kind,destination_kind", [("local", "s3"), ("s3", "local"), ("s3", "s3")]
    )
    def test_provider_pair_preserves_large_unicode_artifact_ranges(
        self, migration_database, tmp_path, source_kind, destination_kind
    ):
        session = migration_database
        config = (
            remote_config()
            if source_kind == "s3"
            else {
                "provider": "local",
                "data_dir": str(settings.data_dir),
                "thumb_dir": str(settings.thumb_dir),
            }
        )
        update_storage_provider(session, provider=config["provider"], raw_config=config)
        source = build_configured_backend(parse_provider_config(config))
        source.ensure_setup()
        bind_backend(source)
        actor = build_user(session, superuser=True)
        payload = b"large-artifact\x00" * (512 * 1024)
        artifact = build_stored_file(
            session,
            source,
            build_model(session),
            data=payload,
            filename="pieza-ñ-機械.stl",
        )
        artifact_id, original_key = artifact.id, artifact.path
        backup = create_backup()
        if destination_kind == "s3":
            destination = remote_config()
        else:
            data, thumbs = tmp_path / "destination", tmp_path / "destination-thumbs"
            data.mkdir()
            thumbs.mkdir()
            destination = {
                "provider": "local",
                "data_dir": str(data),
                "thumb_dir": str(thumbs),
            }
        owner = vault_migration.VaultMigrations(get_session_factory())
        plan = owner.preflight(
            destination,
            actor_id=actor.id,
            backup_id=backup.id,
            backup_source_ref=backup.source_ref,
        )
        owner.start(plan["id"], plan["plan_digest"])
        assert owner.advance(plan["id"])["state"] == "ready"
        assert get_backend() is source
        assert source.read_bytes(original_key) == payload
        assert owner.cutover(plan["id"])["state"] == "active"
        session.expire_all()
        key = session.get(File, artifact_id).path
        active = get_backend()
        assert active.read_bytes(key) == payload
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.factories import bearer

        response = TestClient(app).get(
            f"/api/v1/files/{artifact_id}/download",
            headers={**bearer(actor), "Range": "bytes=1024-2047"},
            follow_redirects=False,
        )
        assert response.status_code == 206, response.text
        assert response.content == payload[1024:2048]
        assert source.read_bytes(original_key) == payload
        assert owner.full_audit(plan["id"])["full_audit"]["critical_count"] == 0
