"""The exact v0.12.1 SQLite create_all schema can upgrade to head."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterator

import boto3
import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from alembic import command
from app.core.config import _overlay, settings
from app.db import migrate as migrate_mod
from app.db.models import File
from app.services import runtime_config
from app.services.storage_backend import S3StorageBackend
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_sqlite_schema,
    seed_released_v0121_rows,
    seed_schema_row,
)


@dataclass(frozen=True)
class _MigratedS3Installation:
    engine: Engine
    backend: S3StorageBackend
    released_key: str
    released_payload: bytes


@pytest.fixture
def released_v0121_s3(
    tmp_path: Path,
) -> Iterator[_MigratedS3Installation]:
    """A released SQLite database pointing at bytes in the real S3 test store."""
    endpoint = s3_endpoint()
    bucket = f"printstash-upgrade-{uuid.uuid4().hex[:12]}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    client.create_bucket(Bucket=bucket)
    released_key = "vault-data/files/released-model/v1/released-model.stl"
    released_payload = b"released-v0.12.1-object-bytes"
    client.put_object(Bucket=bucket, Key=released_key, Body=released_payload)

    url = f"sqlite:///{tmp_path / 'released-v0.12.1-real-s3.sqlite'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_sqlite_schema(connection)
            seed_released_v0121_rows(connection)
            seed_schema_row(
                connection,
                "system_config",
                id=1,
                storage_backend="s3",
                s3_bucket=bucket,
                s3_endpoint_url=endpoint,
                s3_region="us-east-1",
                s3_access_key=S3_ACCESS_KEY,
                s3_secret_key=S3_SECRET_KEY,
            )
            connection.execute(
                text(
                    "UPDATE files SET path = :path, size_bytes = :size_bytes, "
                    "sha256 = :sha256 WHERE id = 1"
                ),
                {
                    "path": released_key,
                    "size_bytes": len(released_payload),
                    "sha256": hashlib.sha256(released_payload).hexdigest(),
                },
            )

        command.stamp(
            migrate_mod._alembic_config(url),  # noqa: SLF001
            RELEASED_V0121_REVISION,
        )
        command.upgrade(migrate_mod._alembic_config(url), "head")  # noqa: SLF001
        with Session(engine) as session:
            runtime_config.apply_overlay(session)
        backend = S3StorageBackend()

        yield _MigratedS3Installation(
            engine=engine,
            backend=backend,
            released_key=released_key,
            released_payload=released_payload,
        )
    finally:
        _overlay.clear()
        engine.dispose()
        listed = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        for item in listed:
            client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)


def _test_exact_released_sqlite_create_all_schema_upgrades_without_data_loss(
    tmp_path: Path,
) -> None:
    """A fresh-install database from the released models is an upgrade input."""
    external_bytes = b"released external artifact\x00with stable bytes"
    external_path = tmp_path / "external" / "released-model.stl"
    external_path.parent.mkdir()
    external_path.write_bytes(external_bytes)
    external_sha256 = hashlib.sha256(external_bytes).hexdigest()
    url = f"sqlite:///{tmp_path / 'released-v0.12.1-create-all.sqlite'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            create_released_v0121_sqlite_schema(connection)
            seed_released_v0121_rows(connection)
            # A real legacy install stored only compatibility S3 fields; the
            # new root column must be introduced and pinned by the upgrade.
            seed_schema_row(
                connection,
                "system_config",
                id=1,
                storage_backend="s3",
                s3_bucket="released-bucket",
                s3_endpoint_url="https://s3.example.test",
                s3_region="us-east-1",
            )
            seed_schema_row(
                connection,
                "external_libraries",
                id=1,
                name="Released NAS",
                root_path="/mnt/printstash",
            )
            connection.execute(
                text(
                    "UPDATE files SET path = :path, "
                    "original_filename = 'released-model.stl', size_bytes = :size_bytes, "
                    "sha256 = :sha256, is_external = 1, external_library_id = 1 "
                    "WHERE id = 1"
                ),
                {
                    "path": str(external_path),
                    "size_bytes": len(external_bytes),
                    "sha256": external_sha256,
                },
            )
            external_identity_before = connection.execute(
                text(
                    "SELECT path, size_bytes, sha256, is_external, external_library_id "
                    "FROM files WHERE id = 1"
                )
            ).one()
            before = {
                table: connection.execute(
                    text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                ).scalar_one()
                for table in (
                    "collections",
                    "files",
                    "metadata",
                    "models",
                    "owned_storage_objects",
                    "storage_delete_intents",
                    "tags",
                )
            }
    finally:
        engine.dispose()

    command.stamp(migrate_mod._alembic_config(url), RELEASED_V0121_REVISION)  # noqa: SLF001
    command.upgrade(migrate_mod._alembic_config(url), "head")  # noqa: SLF001

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            after = {
                table: connection.execute(
                    text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
                ).scalar_one()
                for table in before
            }
            assert after == before
            assert (
                connection.execute(
                    text(
                        "SELECT path, size_bytes, sha256, is_external, external_library_id "
                        "FROM files WHERE id = 1"
                    )
                ).one()
                == external_identity_before
            )
            # Legacy external rows deliberately remain unbound.  The upgrade
            # must preserve their path/bytes while requiring explicit admin
            # enrollment before scans or write-back can resume.
            assert (
                connection.execute(
                    text("SELECT root_identity FROM external_libraries WHERE id = 1")
                ).scalar_one()
                is None
            )
            assert Path(external_identity_before[0]).read_bytes() == external_bytes
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == ScriptDirectory.from_config(
                    migrate_mod._alembic_config(url)  # noqa: SLF001
                ).get_current_head()
            )
            assert migrate_mod._orphan_schema_issues(engine) == []  # noqa: SLF001
            config_columns = {
                column["name"]
                for column in inspect(connection).get_columns("system_config")
            }
            assert "s3_root" in config_columns
            assert connection.execute(
                text(
                    "SELECT storage_backend, storage_provider, s3_root "
                    "FROM system_config WHERE id = 1"
                )
            ).one() == ("s3", None, "vault-data")

            for table in (
                "collections",
                "files",
                "models",
                "print_jobs",
                "printers",
                "tags",
                "users",
            ):
                foreign_keys = inspect(connection).get_foreign_keys(table)
                signatures = {
                    (
                        tuple(key["constrained_columns"]),
                        key["referred_table"],
                        tuple(key["referred_columns"]),
                    )
                    for key in foreign_keys
                }
                assert len(signatures) == len(foreign_keys)

            metadata_indexes = {
                index["name"] for index in inspect(connection).get_indexes("metadata")
            }
            assert "ix_metadata_material_type" not in metadata_indexes
            assert "ix_metadata_printer_model" not in metadata_indexes
            assert "ix_metadata_slicer_name" not in metadata_indexes
    finally:
        engine.dispose()


class TestReleasedV0121Upgrade:
    def test_exact_released_sqlite_create_all_schema_upgrades_without_data_loss(
        self, tmp_path: Path
    ) -> None:
        _test_exact_released_sqlite_create_all_schema_upgrades_without_data_loss(
            tmp_path
        )

    def test_legacy_s3_key_preserves_pinned_root_across_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A released S3 key remains readable through two fresh adapter instances."""
        url = f"sqlite:///{tmp_path / 'released-v0.12.1-s3.sqlite'}"
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                create_released_v0121_sqlite_schema(connection)
                seed_released_v0121_rows(connection)
                seed_schema_row(
                    connection,
                    "system_config",
                    id=1,
                    storage_backend="s3",
                    s3_bucket="released-bucket",
                    s3_endpoint_url="https://s3.example.test",
                    s3_region="us-east-1",
                )
            command.stamp(
                migrate_mod._alembic_config(url),
                RELEASED_V0121_REVISION,  # noqa: SLF001
            )
            command.upgrade(migrate_mod._alembic_config(url), "head")  # noqa: SLF001

            key = "vault-data/files/released-model.stl"
            payload = b"legacy object bytes"
            calls: list[tuple[str, str]] = []

            class FakeS3:
                def get_object(self, *, Bucket: str, Key: str, **_kwargs):
                    calls.append(("get", Key))
                    assert Bucket == "released-bucket"
                    assert Key == key
                    return {"Body": BytesIO(payload)}

                def get_paginator(self, name: str):
                    assert name == "list_objects_v2"

                    class Paginator:
                        def paginate(self, *, Bucket: str, Prefix: str):
                            assert Bucket == "released-bucket"
                            assert Prefix == "vault-data/"
                            calls.append(("list", Prefix))
                            return [{"Contents": [{"Key": key}]}]

                    return Paginator()

            fake = FakeS3()
            monkeypatch.setattr("boto3.client", lambda **_kwargs: fake)
            monkeypatch.setattr(
                settings._frozen,
                "s3_root",
                "operator-drift",  # noqa: SLF001
            )

            with Session(engine) as session:
                runtime_config.apply_overlay(session)
                first = S3StorageBackend(check_bucket=False)
                assert first.read_bytes(key) == payload
                assert first.list_keys() == [key]

            # A restart clears process-local overlay state and projects the DB
            # row again. The ambient root remains deliberately different.
            _overlay.clear()
            with Session(engine) as session:
                runtime_config.apply_overlay(session)
                second = S3StorageBackend(check_bucket=False)
                assert second.read_bytes(key) == payload
                assert second.list_keys() == [key]

            assert calls == [
                ("get", key),
                ("list", "vault-data/"),
                ("get", key),
                ("list", "vault-data/"),
            ]
        finally:
            engine.dispose()


@pytest.mark.s3
class TestReleasedV0121S3Upgrade:
    def test_reads_a_released_artifact_through_the_migrated_backend(
        self, released_v0121_s3: _MigratedS3Installation
    ) -> None:
        with Session(released_v0121_s3.engine) as session:
            artifact = session.get(File, 1)
            assert artifact is not None
            assert artifact.path == released_v0121_s3.released_key

        assert (
            released_v0121_s3.backend.read_bytes(artifact.path)
            == released_v0121_s3.released_payload
        )

    def test_publishes_a_post_upgrade_object_beside_the_released_one(
        self, released_v0121_s3: _MigratedS3Installation
    ) -> None:
        new_key = released_v0121_s3.backend.blob_key("post-upgrade", 1, "new-model.stl")
        released_v0121_s3.backend.create_bytes(b"post-upgrade-bytes", new_key)

        assert released_v0121_s3.backend.read_bytes(new_key) == b"post-upgrade-bytes"
        assert (
            released_v0121_s3.backend.read_bytes(released_v0121_s3.released_key)
            == released_v0121_s3.released_payload
        )
        assert {released_v0121_s3.released_key, new_key} <= set(
            released_v0121_s3.backend.list_keys()
        )
