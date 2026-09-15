"""Existing native vectors upgrade with no re-embedding or identity changes."""

import struct

from sqlalchemy import create_engine, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestSearchVectorsMigration:
    def test_preserves_legacy_vectors_on_upgrade(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'vectors.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "68d8257ee80a")
        engine = create_engine(url)
        blob = struct.pack("<3f", 0.6, 0.8, 0)
        unit_key = "mesh:21:0:" + "b" * 64 + ":" + "d" * 16
        try:
            with engine.begin() as connection:
                seed_schema_row(connection, "models", id=12, hash="a" * 64)
                seed_schema_row(
                    connection,
                    "files",
                    id=21,
                    model_id=12,
                    file_type="STL",
                    sha256="b" * 64,
                    version=1,
                )
                seed_schema_row(
                    connection,
                    "embedding_spaces",
                    id=8,
                    config_hash="c" * 64,
                    native_dimension=3,
                )
                seed_schema_row(
                    connection,
                    "index_generations",
                    id=9,
                    space_id=8,
                    index_dimension=3,
                    state="active",
                    index_backend="numpy",
                    quantization="float32",
                )
                seed_schema_row(
                    connection,
                    "passage_vectors",
                    id=31,
                    generation_id=9,
                    unit_kind="mesh_artifact",
                    unit_key=unit_key,
                    model_id=12,
                    file_id=21,
                    input_hash="b" * 64,
                    native_dimension=3,
                    vector_blob=blob,
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT id, subject_type, subject_id, model_id, file_id, unit_kind, unit_key, input_hash, native_dimension, vector_blob FROM passage_vectors"
                    )
                ).one()
                assert row == (
                    31,
                    "model",
                    12,
                    12,
                    21,
                    "mesh_artifact",
                    unit_key,
                    "b" * 64,
                    3,
                    blob,
                )
                assert (
                    connection.execute(
                        text("SELECT config_hash FROM embedding_spaces")
                    ).scalar_one()
                    == "c" * 64
                )
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            command.downgrade(config, "68d8257ee80a")
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT id, model_id, file_id, vector_blob FROM passage_vectors"
                    )
                ).one() == (31, 12, 21, blob)
        finally:
            engine.dispose()
