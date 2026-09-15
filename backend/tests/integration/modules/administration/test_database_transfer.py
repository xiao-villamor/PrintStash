"""Database transfer copies real durable data and refuses unsafe destinations."""

import json
import struct
from dataclasses import asdict
from uuid import uuid4

import pytest
from printstash_core.inference import EmbeddingSpace as SpaceContract
from sqlalchemy import create_engine, inspect, make_url, text
from sqlmodel import Session, SQLModel

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from app.modules.administration.database_transfer import DatabaseTransferError, transfer
from tests.containers import postgres_url
from tests.factories import build_file, build_model
from tests.factories.migration_rows import seed_schema_row

pytestmark = pytest.mark.postgres


@pytest.fixture
def databases(tmp_path):
    source = create_engine(f"sqlite:///{tmp_path / 'source.sqlite'}")
    SQLModel.metadata.create_all(source)
    command.stamp(_alembic_config(str(source.url)), "head")
    with Session(source) as session:
        model = build_model(session, "A bracket")
        file = build_file(session, model)
        model.thumbnail_file_id = file.id
        session.add(model)
        session.commit()
        model_id, file_id = model.id, file.id
    contract = SpaceContract("fixture", "v1", 3, "text", "recipe-v1", profile="passage")
    blob = struct.pack("<3f", 0.6, 0.8, 0)
    with source.begin() as connection:
        seed_schema_row(
            connection,
            "embedding_spaces",
            id=71,
            config_hash=contract.config_hash,
            config_json=json.dumps(asdict(contract)),
            native_dimension=3,
        )
        seed_schema_row(
            connection,
            "index_generations",
            id=72,
            space_id=71,
            state="active",
            index_dimension=3,
            index_backend="numpy",
            quantization="float32",
            active_profile_key="text/passage",
        )
        seed_schema_row(
            connection,
            "passage_vectors",
            id=73,
            generation_id=72,
            subject_type="model",
            subject_id=model_id,
            model_id=model_id,
            file_id=file_id,
            native_dimension=3,
            vector_blob=blob,
        )
        # Raw ciphertext is copied verbatim; this deliberately never needs the
        # original key in the process doing the database transfer.
        seed_schema_row(
            connection,
            "system_config",
            id=1,
            configured_at=None,
            makerworld_token="enc:v1:opaque-fixture-ciphertext",
        )
    root_url = make_url(normalize_database_url(postgres_url()))
    schema = "transfer_" + uuid4().hex
    base = create_engine(root_url)
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    target = create_engine(
        root_url.update_query_dict({"options": f"-csearch_path={schema}"})
    )
    try:
        yield source, target, blob, model_id, file_id
    finally:
        source.dispose()
        target.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestDatabaseTransfer:
    def test_refuses_unknown_tables_in_a_transfer_destination(self, databases):
        source, target, *_ = databases
        with target.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE unknown_table (id INTEGER PRIMARY KEY)"
            )

        with pytest.raises(
            DatabaseTransferError, match="database_transfer_target_schema_unknown"
        ):
            transfer(source, target, dry_run=False)

        assert inspect(target).get_table_names() == ["unknown_table"]

    @pytest.mark.parametrize(
        "operation,batch",
        [("transfer", 0), ("transfer", 1025), ("snapshot", 0), ("snapshot", 1025)],
    )
    def test_refuses_invalid_transfer_batches(self, databases, operation, batch):
        from app.modules.administration.database_transfer import snapshot_postgres

        source, target, *_ = databases
        operation_call, left, right = (
            (transfer, source, target)
            if operation == "transfer"
            else (snapshot_postgres, target, source)
        )

        with pytest.raises(
            DatabaseTransferError, match="database_transfer_batch_invalid"
        ):
            operation_call(left, right, batch_size=batch)

        assert inspect(target).get_table_names() == []

    @pytest.mark.parametrize("operation", ["transfer", "snapshot"])
    def test_refuses_unsupported_transfer_dialects(self, databases, operation):
        from app.modules.administration.database_transfer import snapshot_postgres

        source, target, *_ = databases
        operation_call = transfer if operation == "transfer" else snapshot_postgres

        with pytest.raises(
            DatabaseTransferError, match="database_transfer_dialects_invalid"
        ):
            operation_call(source, source)

        assert inspect(target).get_table_names() == []

    @pytest.mark.parametrize(
        "change,code",
        [
            (
                "CREATE TABLE unknown_table (id INTEGER PRIMARY KEY)",
                "database_transfer_source_schema_unknown",
            ),
            (
                "UPDATE alembic_version SET version_num='0118'",
                "database_transfer_source_upgrade_required",
            ),
        ],
        ids=["unknown-table", "old-schema"],
    )
    def test_refuses_unknown_or_outdated_transfer_sources(
        self, databases, change, code
    ):
        source, target, *_ = databases
        with source.begin() as connection:
            connection.exec_driver_sql(change)

        with pytest.raises(DatabaseTransferError, match=code):
            transfer(source, target, dry_run=False)

        assert inspect(target).get_table_names() == []

    @pytest.mark.parametrize("quantization", ["int8", "binary"])
    def test_copies_compressed_generations_between_databases(
        self, databases, monkeypatch, quantization
    ):
        from printstash_core.inference.transforms import IndexTransform

        from app.core.config import _overlay
        from app.db.models import IndexGeneration
        from app.modules.search import vector_index

        source, target, blob, *_ = databases
        monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
        with Session(source) as session:
            generation = session.get(IndexGeneration, 72)
            generation.index_backend = "sqlite_vec"
            generation.quantization = quantization
            generation.transform_json = IndexTransform(3, 3, quantization).metadata()
            session.add(generation)
            assert vector_index.prepare(session, generation)
            vector_index.rebuild_partition(session, generation)
            session.commit()

        transfer(source, target, dry_run=False)

        with target.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT vector_blob FROM passage_vectors WHERE id=73")
                ).scalar_one()
                == blob
            )
            assert (
                connection.execute(
                    text("SELECT index_backend FROM index_generations WHERE id=72")
                ).scalar_one()
                == "pgvector"
            )
            if quantization == "int8":
                assert connection.execute(
                    text("SELECT embedding FROM code_gen_72 WHERE id=73")
                ).scalar_one() == IndexTransform(3, 3, "int8").encode(blob)

    def test_copies_sqlite_to_postgres_without_inference(self, databases):
        source, target, blob, model_id, file_id = databases
        report = transfer(source, target, dry_run=False, batch_size=1)
        assert report.dry_run is False
        assert (
            next(row.rows for row in report.tables if row.name == "passage_vectors")
            == 1
        )
        with target.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT vector_blob FROM passage_vectors WHERE id=73")
                ).scalar_one()
                == blob
            )
            assert (
                connection.execute(
                    text("SELECT thumbnail_file_id FROM models WHERE id=:id"),
                    {"id": model_id},
                ).scalar_one()
                == file_id
            )
            assert (
                connection.execute(
                    text("SELECT makerworld_token FROM system_config WHERE id=1")
                ).scalar_one()
                == "enc:v1:opaque-fixture-ciphertext"
            )
            assert (
                connection.execute(
                    text(
                        "SELECT nextval(pg_get_serial_sequence('passage_vectors','id'))"
                    )
                ).scalar_one()
                == 74
            )
            assert all(
                not constraint.get("options", {}).get("deferrable", False)
                for constraint in inspect(connection).get_foreign_keys("models")
            )

    def test_previews_database_migration(self, databases):
        source, target, *_ = databases
        report = transfer(source, target)
        assert report.dry_run is True
        assert next(row.rows for row in report.tables if row.name == "models") == 1
        assert inspect(target).get_table_names() == []

    def test_refuses_nonempty_migration_target(self, databases):
        source, target, *_ = databases
        SQLModel.metadata.create_all(target)
        with Session(target) as session:
            build_model(session, "Existing destination")
        with pytest.raises(DatabaseTransferError, match="target_not_empty"):
            transfer(source, target, dry_run=False)
        with target.connect() as connection:
            assert connection.execute(
                text("SELECT name FROM models")
            ).scalars().all() == ["Existing destination"]

    def test_rolls_back_a_failed_transfer(self, databases, monkeypatch):
        from app.modules.administration import database_transfer

        source, target, *_ = databases
        original = database_transfer._proof

        def fail_destination(connection, table, **kwargs):
            if connection.dialect.name == "postgresql":
                raise DatabaseTransferError("database_transfer_verification_failed")
            return original(connection, table, **kwargs)

        monkeypatch.setattr(database_transfer, "_proof", fail_destination)
        with pytest.raises(DatabaseTransferError, match="verification_failed"):
            transfer(source, target, dry_run=False)
        assert inspect(target).get_table_names() == []
