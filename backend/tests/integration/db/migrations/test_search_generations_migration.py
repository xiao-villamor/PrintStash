"""Lifecycle upgrades retain pre-existing serving indexes without adopting their work."""

import struct

from sqlalchemy import create_engine, text

from alembic import command
from app.db.migrate import _alembic_config
from tests.factories.migration_rows import seed_schema_row


class TestSearchGenerationsMigration:
    def test_preserves_legacy_serving_indexes_across_roundtrip(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'generations.sqlite'}"
        config = _alembic_config(url)
        command.upgrade(config, "bf6dfc561eca")
        engine = create_engine(url)
        blob = struct.pack("<3f", 0.6, 0.8, 0)
        try:
            with engine.begin() as connection:
                seed_schema_row(connection, "models", id=12, hash="a" * 64)
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
                    active_profile_key="text_image/mesh_view",
                    index_backend="numpy",
                    quantization="float32",
                )
                seed_schema_row(
                    connection,
                    "passage_vectors",
                    id=31,
                    generation_id=9,
                    subject_type="model",
                    subject_id=12,
                    unit_kind="mesh_artifact",
                    unit_key="fixture:legacy",
                    model_id=12,
                    input_hash="b" * 64,
                    native_dimension=3,
                    vector_blob=blob,
                )

            command.upgrade(config, "head")

            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT id, state, active_profile_key, version_token, lease_token, cancel_requested, auto_activate, processed FROM index_generations"
                    )
                ).one() == (
                    9,
                    "active",
                    "text_image/mesh_view",
                    None,
                    None,
                    False,
                    False,
                    0,
                )
                assert connection.execute(
                    text(
                        "SELECT id, native_dimension, vector_blob, truncated FROM passage_vectors"
                    )
                ).one() == (31, 3, blob, False)
                assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            command.downgrade(config, "bf6dfc561eca")
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT id, native_dimension, vector_blob FROM passage_vectors"
                    )
                ).one() == (31, 3, blob)
        finally:
            engine.dispose()
