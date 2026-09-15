"""Actual PostgreSQL vector type, HNSW lifecycle and permission-aware fallback."""

import json
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from printstash_core.inference import EmbeddingSpace as SpaceContract
from printstash_core.inference.transforms import IndexTransform
from sqlalchemy import create_engine, make_url, text
from sqlmodel import Session, SQLModel, select

from app.core.config import _overlay
from app.db.derived_objects import managed_names
from app.db.models import PassageVector
from app.db.url import normalize_database_url
from app.modules.search import vector_index, vector_store
from tests.containers import pgvector_url
from tests.factories import (
    build_embedding_space,
    build_file,
    build_index_generation,
    build_model,
    build_passage_vector,
)


@pytest.fixture
def native_database(monkeypatch):
    monkeypatch.setitem(_overlay, "search_native_vectors_enabled", True)
    root = make_url(normalize_database_url(pgvector_url()))
    base = create_engine(root)
    schema = "vectors_" + uuid4().hex
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(
        root.update_query_dict({"options": f"-csearch_path={schema},public"})
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            space = build_embedding_space(session)
            generation = build_index_generation(
                session, space, index_backend="pgvector"
            )
            first = build_passage_vector(
                session, generation, build_file(session, build_model(session))
            )
            second = build_passage_vector(
                session, generation, build_file(session, build_model(session))
            )
            contract = SpaceContract(**json.loads(space.config_json))
            yield session, generation, contract, first, second, schema
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestPostgresVectorIndex:
    @pytest.mark.parametrize(
        ("quantization", "expected_backend"),
        [("int8", "numpy"), ("binary", "pgvector")],
        ids=["portable-int8", "native-binary"],
    )
    def test_queries_compressed_generations(
        self, native_database, quantization, expected_backend
    ):
        session, generation, contract, first, second, _ = native_database
        generation.quantization = quantization
        generation.transform_json = IndexTransform.approved(
            3, 3, quantization
        ).metadata()
        session.add(generation)
        assert vector_index.prepare(session, generation)
        assert vector_index.rebuild_partition(session, generation) == 2
        session.commit()

        result = vector_store.query(
            session,
            generation_id=generation.id,
            space=contract,
            vector=[1, 0, 0],
            allowed_ids=select(PassageVector.id).where(PassageVector.id == second.id),
        )

        assert result.backend == expected_backend
        assert [(item.unit_id, item.score) for item in result.items] == [(second.id, 1)]
        assert len(first.vector_blob) == len(second.vector_blob) == 12
        assert generation.vector_table_name in managed_names(session.connection())

    @pytest.mark.parametrize("native_enabled", [False, True])
    @pytest.mark.parametrize("quantization", ["float32", "int8", "binary"])
    def test_restores_without_pgvector(
        self, native_database, tmp_path, monkeypatch, native_enabled, quantization
    ):
        from app.modules.administration.database_transfer import (
            snapshot_postgres,
            transfer,
        )

        session, generation, contract, first, second, schema = native_database
        generation.quantization = quantization
        generation.transform_json = IndexTransform(3, 3, quantization).metadata()
        session.add(generation)
        assert vector_index.prepare(session, generation)
        vector_index.rebuild_partition(session, generation)
        session.commit()
        engine = session.get_bind()
        # Stamp the application schema, as the composition root does on install.
        from alembic import command
        from app.db.migrate import _alembic_config

        command.stamp(
            _alembic_config(engine.url.render_as_string(hide_password=False)), "head"
        )
        portable = create_engine(f"sqlite:///{tmp_path / 'portable.sqlite'}")
        target_database = "restored_" + uuid4().hex
        # Release ORM reads before taking a repeatable-read snapshot.
        generation_id = generation.id
        expected_ids = {first.id, second.id}
        session.rollback()
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{target_database}"')
        target = create_engine(
            engine.url.difference_update_query(["options"]).set(
                database=target_database
            )
        )
        try:
            snapshot_postgres(engine, portable)
            monkeypatch.setitem(
                _overlay, "search_native_vectors_enabled", native_enabled
            )
            report = transfer(portable, target, dry_run=False)
            assert report.derived_indexes == int(
                native_enabled or quantization == "int8"
            )
            with Session(target) as restored:
                result = vector_store.query(
                    restored,
                    generation_id=generation_id,
                    space=contract,
                    vector=[1, 0, 0],
                    allowed_ids=select(PassageVector.id),
                )
                assert result.backend == (
                    "pgvector" if native_enabled and quantization != "int8" else "numpy"
                )
                assert {row.unit_id for row in result.items} == expected_ids
                monkeypatch.setitem(_overlay, "search_native_vectors_enabled", False)
                fallback = vector_store.query(
                    restored,
                    generation_id=generation_id,
                    space=contract,
                    vector=[1, 0, 0],
                    allowed_ids=select(PassageVector.id),
                )
                assert fallback.backend == "numpy"
                assert [(row.unit_id, row.score) for row in fallback.items] == [
                    (row.unit_id, row.score) for row in result.items
                ]
        finally:
            portable.dispose()
            target.dispose()
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE "{target_database}" WITH (FORCE)'
                )

    def test_queries_authorized_native_vectors(self, native_database):
        session, generation, contract, first, second, _ = native_database
        assert vector_index.prepare(session, generation)
        assert vector_index.rebuild_partition(session, generation) == 2
        session.commit()
        result = vector_store.query(
            session,
            generation_id=generation.id,
            space=contract,
            vector=[1, 0, 0],
            allowed_ids=select(PassageVector.id).where(PassageVector.id == second.id),
        )
        assert result.backend == "pgvector"
        assert [row.unit_id for row in result.items] == [second.id]
        indexes = (
            session.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND tablename=:name"
                ),
                {"name": generation.vector_table_name},
            )
            .scalars()
            .all()
        )
        assert generation.vector_table_name + "_hnsw" in indexes
        names = managed_names(session.connection())
        assert generation.vector_table_name in names
        context = MigrationContext.configure(
            session.connection(),
            opts={
                "include_name": lambda name, kind, parents: (
                    not (kind == "table" and name in names)
                ),
                "compare_server_default": True,
            },
        )
        assert compare_metadata(context, SQLModel.metadata) == []

    def test_degrades_when_pg_extension_cannot_be_created(self, native_database):
        session, generation, contract, *_rest, schema = native_database
        role = "vector_reader_" + uuid4().hex
        session.execute(text(f'CREATE ROLE "{role}"'))
        session.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        session.execute(
            text(f'GRANT SELECT, UPDATE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        )
        session.commit()
        try:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert not vector_index.prepare(session, generation)
            assert generation.index_state == "unavailable"
            result = vector_store.query(
                session,
                generation_id=generation.id,
                space=contract,
                vector=[1, 0, 0],
                allowed_ids=select(PassageVector.id),
            )
            assert result.backend == "numpy"
            assert len(result.items) == 2
            session.rollback()
        finally:
            session.execute(text(f'DROP OWNED BY "{role}"'))
            session.execute(text(f'DROP ROLE "{role}"'))
            session.commit()

    def test_publishes_nullable_owner_columns_from_a_consumer(self, native_database):
        from sqlalchemy import literal

        from app.db.models import File

        session, generation, contract, first, _, _ = native_database
        file = session.get(File, first.file_id)
        source = select(
            literal("model").label("subject_type"),
            File.model_id.label("subject_id"),
            File.model_id,
            File.id.label("file_id"),
            literal(None).label("passage_id"),
        ).where(File.id == file.id, File.sha256 == file.sha256)
        assert vector_store.publish(
            session,
            generation_id=generation.id,
            space=contract,
            unit_kind="consumer_mesh",
            unit_key=f"consumer:{file.id}",
            input_hash=file.sha256,
            vector=[1, 0, 0],
            source=source,
        )
        session.commit()
        stored = session.exec(
            select(PassageVector).where(PassageVector.unit_kind == "consumer_mesh")
        ).one()
        assert stored.model_id == file.model_id
        assert stored.passage_id is None
        assert len(stored.vector_blob) == 12
