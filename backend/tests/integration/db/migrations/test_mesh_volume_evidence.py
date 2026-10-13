"""Historical volume scalars survive one constrained table rebuild unassessed."""

import io
import shutil

import pytest
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db import migrate
from tests.factories.migration_rows import seed_released_v0121_rows

PREVIOUS = "67494831ae72"
REVISION = "8fd749bf52a1"


@pytest.fixture(scope="module")
def pre_evidence_database(tmp_path_factory):
    path = tmp_path_factory.mktemp("pre-volume-evidence") / "vault.sqlite"
    url = f"sqlite:///{path}"
    command.upgrade(migrate._alembic_config(url), PREVIOUS)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            seed_released_v0121_rows(connection)
    finally:
        engine.dispose()
    return path


@pytest.fixture
def volume_database(pre_evidence_database, tmp_path):
    path = tmp_path / "upgraded.sqlite"
    shutil.copy2(pre_evidence_database, path)
    return f"sqlite:///{path}"


class TestHistoricalNonfiniteVolume:
    @pytest.mark.parametrize("value", [float("inf"), float("-inf")])
    def test_upgrade_repairs_unrepresentable_legacy_scalar_without_losing_other_facts(
        self, volume_database, value, caplog
    ):
        engine = create_engine(volume_database)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE metadata SET volume_mm3=:volume WHERE id=1"),
                    {"volume": value},
                )
                assert (
                    connection.execute(
                        text("SELECT volume_mm3 FROM metadata WHERE id=1")
                    ).scalar_one()
                    == value
                )
            command.upgrade(migrate._alembic_config(volume_database), REVISION)
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT id,file_id,volume_mm3,volume_state,volume_method,volume_unavailable_cause,volume_not_calculated_cause,slicer_name,estimated_time_s FROM metadata WHERE id=1"
                    )
                ).one()
            assert row == (
                1,
                2,
                None,
                "legacy_unassessed",
                None,
                None,
                None,
                "PrusaSlicer",
                3600,
            )
            assert "replaced 1 nonfinite historical volume scalar" in caplog.text
        finally:
            engine.dispose()


class TestVolumeUpgrade:
    @pytest.mark.parametrize("value", [None, 0.0, -1.0, 1e-9, 500.0])
    def test_upgrade_retains_historical_volume_without_certification(
        self, volume_database, value
    ):
        engine = create_engine(volume_database)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE metadata SET volume_mm3=:volume WHERE id=1"),
                    {"volume": value},
                )
            command.upgrade(migrate._alembic_config(volume_database), REVISION)
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT volume_mm3, volume_state, volume_method, volume_unavailable_cause, volume_not_calculated_cause, slicer_name, estimated_time_s FROM metadata WHERE id=1"
                    )
                ).one()
            assert row == (
                value,
                "legacy_unassessed",
                None,
                None,
                None,
                "PrusaSlicer",
                3600,
            )
            # Historical evidence above belongs to REVISION; live model parity
            # requires the complete current migration chain.
            command.upgrade(migrate._alembic_config(volume_database), "head")
            assert migrate._orphan_schema_issues(engine) == []
        finally:
            engine.dispose()

    def test_upgrade_switches_new_rows_to_pending_without_changing_old_rows(
        self, volume_database
    ):
        command.upgrade(migrate._alembic_config(volume_database), REVISION)
        engine = create_engine(volume_database)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO metadata (id,file_id,created_at) VALUES (2,1,'2026-10-04 10:00:00')"
                    )
                )
                rows = connection.execute(
                    text(
                        "SELECT id, volume_mm3, volume_state, volume_not_calculated_cause FROM metadata ORDER BY id"
                    )
                ).all()
            assert rows == [
                (1, None, "legacy_unassessed", None),
                (2, None, "not_calculated", "enrichment_pending"),
            ]
            inspector = inspect(engine)
            assert inspector.get_pk_constraint("metadata")["constrained_columns"] == [
                "id"
            ]
            assert (
                inspector.get_foreign_keys("metadata")[0]["referred_table"] == "files"
            )
            assert inspector.get_indexes("metadata") == [
                {
                    "name": "ix_metadata_file_id",
                    "column_names": ["file_id"],
                    "unique": 1,
                    "dialect_options": {},
                }
            ]
        finally:
            engine.dispose()

    def test_roundtrip_preserves_historical_measurements(self, volume_database):
        engine = create_engine(volume_database)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE metadata SET volume_mm3=6e-9 WHERE id=1")
                )
            command.upgrade(migrate._alembic_config(volume_database), REVISION)
            command.downgrade(migrate._alembic_config(volume_database), PREVIOUS)
            with engine.connect() as connection:
                scalar = connection.execute(
                    text(
                        "SELECT volume_mm3,slicer_name,estimated_time_s FROM metadata WHERE id=1"
                    )
                ).one()
            assert scalar == (6e-9, "PrusaSlicer", 3600)
            assert "volume_state" not in {
                column["name"] for column in inspect(engine).get_columns("metadata")
            }
            command.upgrade(migrate._alembic_config(volume_database), REVISION)
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT volume_mm3,volume_state,volume_method,volume_unavailable_cause,volume_not_calculated_cause FROM metadata WHERE id=1"
                    )
                ).one()
            assert row == (6e-9, "legacy_unassessed", None, None, None)
            # Historical evidence above belongs to REVISION; live model parity
            # requires the complete current migration chain.
            command.upgrade(migrate._alembic_config(volume_database), "head")
            assert migrate._orphan_schema_issues(engine) == []
        finally:
            engine.dispose()


class TestVolumeOfflineMigration:
    def test_offline_sql_preserves_repair_contract(self):
        output = io.StringIO()
        configuration = migrate._alembic_config("sqlite://")
        configuration.output_buffer = output
        command.upgrade(configuration, PREVIOUS + ":" + REVISION, sql=True)
        ddl = output.getvalue()
        assert ddl.count("CREATE TABLE _alembic_tmp_metadata") == 1
        assert "UPDATE metadata SET volume_mm3=NULL WHERE" in ddl
        assert "volume_mm3 > 1.7976931348623157e+308" in ddl
        assert "volume_mm3 < -1.7976931348623157e+308" in ddl
        assert "DEFAULT 'legacy_unassessed' NOT NULL" in ddl
        assert "DEFAULT 'not_calculated' NOT NULL" in ddl
        assert "volume_not_calculated_cause TEXT DEFAULT 'enrichment_pending'" in ddl
        assert "CREATE UNIQUE INDEX ix_metadata_file_id" in ddl
        assert "CONSTRAINT fk_metadata_file_id_files" in ddl
        assert "nonfinite historical volume scalar" not in ddl


class TestHistoricalDimensionRepair:
    @pytest.mark.parametrize("axis", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"])
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), -1.0])
    def test_upgrade_retires_invalid_dimension_facts(
        self, volume_database, axis, value, caplog
    ):
        engine = create_engine(volume_database)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE metadata SET {axis}=:value WHERE id=1"),
                    {"value": value},
                )
                assert (
                    connection.execute(
                        text(f"SELECT {axis} FROM metadata WHERE id=1")
                    ).scalar_one()
                    == value
                )
            command.upgrade(migrate._alembic_config(volume_database), REVISION)
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        f"SELECT {axis},id,file_id,slicer_name,estimated_time_s FROM metadata WHERE id=1"
                    )
                ).one()
            assert row == (None, 1, 2, "PrusaSlicer", 3600)
            assert f"invalid historical {axis}" in caplog.text
            # Historical evidence above belongs to REVISION; live model parity
            # requires the complete current migration chain.
            command.upgrade(migrate._alembic_config(volume_database), "head")
            assert migrate._orphan_schema_issues(engine) == []
        finally:
            engine.dispose()

    @pytest.mark.parametrize("axis", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"])
    @pytest.mark.parametrize("value", [0.0, 1e-9])
    def test_upgrade_preserves_finite_dimension_precision(
        self, volume_database, axis, value
    ):
        engine = create_engine(volume_database)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE metadata SET {axis}=:value WHERE id=1"),
                    {"value": value},
                )
            command.upgrade(migrate._alembic_config(volume_database), REVISION)
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text(f"SELECT {axis} FROM metadata WHERE id=1")
                    ).scalar_one()
                    == value
                )
        finally:
            engine.dispose()
