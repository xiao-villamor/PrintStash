"""Contracts that must run against a real PostgreSQL server.

The ordinary suite remains SQLite-first, because SQLite is free and covers the
application logic. What it cannot cover is the dialect: a partial index, an enum
column, a concurrent-write conflict and a migration's rendered SQL all behave
differently on PostgreSQL, and every one of those differences is invisible until
somebody self-hosts on it.

The server is a container started for the run — the only path, so this file runs
everywhere or the session stops saying why. See ``tests/containers.py``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Iterator

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from printstash_core.imports import CaptureManifestV2
from printstash_core.mesh.measurements import VolumeLegacyUnassessed
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, create_engine, select

from alembic import command
from app.db import migrate as migrate_mod
from app.db.migrate import run_migrations
from app.db.models import (
    ArtifactProvenanceLink,
    CollectionRole,
    File,
    FileType,
    ModelProvenanceSource,
    PrinterPermission,
    PrinterRole,
    ProvenanceCapture,
    User,
)
from app.db.session import create_async_engine_for_db
from app.db.url import normalize_database_url
from app.modules.identity.auth import create_refresh_token, rotate_refresh_token
from app.modules.identity.printer_rbac import effective_printer_role
from app.modules.identity.rbac import effective_collection_role
from app.modules.library import provenance
from tests.containers import postgres_url
from tests.factories import (
    build_collection,
    build_file,
    build_metadata,
    build_model,
    build_printer,
    build_user,
    grant_collection_role,
    printer_config,
)
from tests.factories.migration_rows import (
    RELEASED_V0121_REVISION,
    create_released_v0121_postgres_schema,
    seed_released_v0121_rows,
)
from tests.paths import ALEMBIC_INI


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator:
    url = postgres_url()
    run_migrations(url)
    engine = create_engine(normalize_database_url(url), pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_postgres(postgres_engine) -> None:
    table_names = [
        table
        for table in inspect(postgres_engine).get_table_names()
        if table != "alembic_version"
    ]
    if not table_names:
        return
    quoted = ", ".join(f'"{table}"' for table in table_names)
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")


def _migration_config(postgres_engine) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option(
        "sqlalchemy.url",
        postgres_engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    return config


def _reset_postgres_schema(postgres_engine) -> None:
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")


@pytest.fixture
def released_postgres(
    postgres_engine, fresh_postgres_schema_issues, fresh_mesh_measurement_checks
) -> Iterator:
    """A v0.12.1 create-all PostgreSQL database, restored to head afterwards."""
    _reset_postgres_schema(postgres_engine)
    with postgres_engine.begin() as connection:
        create_released_v0121_postgres_schema(connection)
        seed_released_v0121_rows(connection)
    command.stamp(_migration_config(postgres_engine), RELEASED_V0121_REVISION)
    try:
        yield postgres_engine
    finally:
        _reset_postgres_schema(postgres_engine)
        run_migrations(postgres_engine.url.render_as_string(hide_password=False))


@pytest.fixture
def fresh_postgres_schema_issues(postgres_engine) -> tuple[str, ...]:
    """Dialect-normalized comparator fingerprint of a fresh current install."""
    return tuple(migrate_mod._orphan_schema_issues(postgres_engine))  # noqa: SLF001


class TestBootstrap:
    def test_fresh_bootstrap_is_at_head_with_partial_default_index(
        self, postgres_engine
    ) -> None:
        # `ALEMBIC_INI` rather than a path built here: alembic.ini lives at the
        # backend root, and a wrong anchor yields a Config with no
        # `script_location`, which fails as "No 'script_location' key found" —
        # a long way from the actual mistake.
        alembic_config = Config(str(ALEMBIC_INI))
        expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
        with postgres_engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == expected_head
            )

        index = next(
            item
            for item in inspect(postgres_engine).get_indexes("printers")
            if item["name"] == "uq_printers_live_default"
        )
        assert index["unique"] is True
        predicate = str(index["dialect_options"]["postgresql_where"])
        assert "deleted_at IS NULL" in predicate

    @pytest.mark.postgres
    def test_released_rows_survive_the_upgrade(self, released_postgres) -> None:
        command.upgrade(_migration_config(released_postgres), "head")

        with released_postgres.connect() as connection:
            stored = connection.execute(
                text(
                    "SELECT f.file_type, f.revision_status, f.revision_label, "
                    "m.slicer_name, m.slicer_version, m.layer_height_mm, "
                    "m.estimated_time_s, m.filament_weight_g, o.key, d.sha256 "
                    "FROM files AS f JOIN metadata AS m ON m.file_id = f.id "
                    "JOIN owned_storage_objects AS o ON o.id = 1 "
                    "JOIN storage_delete_intents AS d ON d.id = 1 "
                    "WHERE f.id = 2"
                )
            ).one()

        assert stored == (
            "GCODE",
            "KNOWN_GOOD",
            "Release slice",
            "PrusaSlicer",
            "2.8.1",
            0.2,
            3600,
            12.5,
            "models/released-model.gcode",
            None,
        )

    @pytest.mark.postgres
    def test_released_upgrade_records_the_current_head(self, released_postgres) -> None:
        command.upgrade(_migration_config(released_postgres), "head")

        with released_postgres.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()

        assert (
            revision
            == ScriptDirectory.from_config(
                _migration_config(released_postgres)
            ).get_current_head()
        )

    @pytest.mark.postgres
    def test_released_upgrade_matches_the_fresh_schema(
        self, fresh_postgres_schema_issues, released_postgres
    ) -> None:
        command.upgrade(_migration_config(released_postgres), "head")

        upgraded_issues = tuple(
            migrate_mod._orphan_schema_issues(released_postgres)  # noqa: SLF001
        )

        assert upgraded_issues == fresh_postgres_schema_issues

    @pytest.mark.postgres
    def test_convergence_downgrade_keeps_released_enum_types(
        self, released_postgres
    ) -> None:
        config = _migration_config(released_postgres)
        command.upgrade(config, "6acea2a5e555")

        command.downgrade(config, "eb8435c9400e")

        with released_postgres.connect() as connection:
            enum_names = set(
                connection.execute(
                    text(
                        "SELECT typname FROM pg_type WHERE typname IN "
                        "('documentkind', 'filerevisionstatus', 'printerprovider')"
                    )
                ).scalars()
            )
        assert enum_names == {
            "documentkind",
            "filerevisionstatus",
            "printerprovider",
        }


class TestCrud:
    def test_the_core_contracts_hold_on_postgres(self, postgres_engine) -> None:
        with Session(postgres_engine) as session:
            user = build_user(session, "pg-user")
            collection = build_collection(
                session, name="Parts", slug="parts", path="parts"
            )
            printer = build_printer(session, name="PG printer", is_default=True)
            session.add_all([user, collection, printer])
            session.commit()
            session.refresh(user)
            session.refresh(collection)
            session.refresh(printer)

            grant_collection_role(session, user, collection, CollectionRole.EDIT)
            session.add(
                PrinterPermission(
                    user_id=user.id,
                    printer_id=printer.id,
                    role=PrinterRole.PRINT,
                )
            )
            session.commit()

            assert (
                effective_collection_role(session, user, collection.id)
                == CollectionRole.EDIT
            )
            assert (
                effective_printer_role(session, user, printer.id) == PrinterRole.PRINT
            )

            session.add(printer_config("Conflicting default", is_default=True))
            with pytest.raises(IntegrityError):
                session.commit()


class TestAsyncEngine:
    @pytest.mark.asyncio
    async def test_psycopg_async_engine_executes_against_real_postgres(self) -> None:
        engine = create_async_engine_for_db(postgres_url())
        try:
            async with AsyncSession(engine) as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
        finally:
            await engine.dispose()


class TestProvenance:
    def test_concurrent_identical_provenance_capture_converges_at_savepoints(
        self,
        postgres_engine,
    ) -> None:
        """A uniqueness race must not poison either caller's outer transaction."""
        manifest = CaptureManifestV2.from_dict(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": {
                    "provider": "printables",
                    "canonical_url": "https://printables.com/model/42?utm_source=pg",
                    "source_item_id": "42",
                    "source_revision": None,
                    "adapter_version": "printables-v1",
                    "fields": {"title": {"value": "Bracket", "origin": "confirmed"}},
                },
                "files": [
                    {
                        "id": "42:file-a",
                        "name": "part.stl",
                        "file_type": "stl",
                        "size": None,
                    }
                ],
                "selected_ids": ["42:file-a"],
            }
        )
        with Session(postgres_engine) as session:
            model = build_model(
                session, name="PG provenance", slug="pg-provenance", hash="p" * 64
            )
            assert model.id is not None
            artifact = build_file(
                session,
                model,
                path="external/pg-provenance.stl",
                filename="part.stl",
                file_type=FileType.STL,
                size_bytes=1,
                sha256="a" * 64,
                external=True,
            )
            assert artifact.id is not None and model.id is not None
            file_id: int = artifact.id
            model_id: int = model.id

        barrier = Barrier(2)

        def attach(index: int) -> int:
            with Session(postgres_engine) as session:
                artifact = session.get(File, file_id)
                assert artifact is not None
                barrier.wait(timeout=10)
                link = provenance.attach_ingested_artifact(
                    session,
                    artifact,
                    provenance.ProvenanceContext(
                        manifest=manifest,
                        source_file_id="42:file-a",
                        source_filename="part.stl",
                        blob_sha256="a" * 64,
                    ),
                )
                # This write occurs *after* the raced savepoints.  It proves that
                # retrying a unique insert did not abort the outer transaction.
                build_user(session, f"pg-provenance-outer-{index}")
                provenance.finish_storage_retirements(session, link.storage_retirements)
                link = link.link
                session.commit()
                assert link.id is not None
                return link.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            link_ids = list(executor.map(attach, range(2)))

        assert link_ids[0] == link_ids[1]
        with Session(postgres_engine) as session:
            assert (
                len(
                    session.exec(
                        select(ModelProvenanceSource).where(
                            ModelProvenanceSource.model_id == model_id
                        )
                    ).all()
                )
                == 1
            )
            assert len(session.exec(select(ProvenanceCapture)).all()) == 1
            assert len(session.exec(select(ArtifactProvenanceLink)).all()) == 1
            assert (
                len(session.exec(select(File).where(File.model_id == model_id)).all())
                == 1
            )
            assert (
                len(
                    [
                        user
                        for user in session.exec(select(User)).all()
                        if user.username.startswith("pg-provenance-outer-")
                    ]
                )
                == 2
            )


class TestRefresh:
    def test_refresh_token_is_consumed_exactly_once_concurrently(
        self, postgres_engine
    ) -> None:
        with Session(postgres_engine) as session:
            user = build_user(session, "pg-refresh")
            session.add(user)
            session.commit()
            session.refresh(user)
            raw_token = create_refresh_token(session, user.id)
            user_id = user.id

        barrier = Barrier(2)

        def consume() -> int | None:
            with Session(postgres_engine) as session:
                barrier.wait(timeout=5)
                return rotate_refresh_token(session, raw_token)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: consume(), range(2)))

        assert results.count(user_id) == 1
        assert results.count(None) == 1


class TestFirstOwnerConcurrency:
    def test_two_api_processes_create_exactly_one_first_owner(
        self, postgres_engine, tmp_path
    ):
        from tests.fakes.setup_process import race_setup_workers

        results = race_setup_workers(
            postgres_engine.url.render_as_string(hide_password=False), tmp_path
        )
        assert sorted(code for code, _ in results) == [201, 409], results
        with Session(postgres_engine) as session:
            assert len(session.exec(select(User).where(User.is_superuser)).all()) == 1


class TestManufacturingPostgres:
    def test_existing_parts_receive_quantity_one_after_upgrade(self, released_postgres):
        config = _migration_config(released_postgres)
        command.upgrade(config, "046685afd7ea")
        with released_postgres.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO multipart_models (id, name, slug, created_at, updated_at) VALUES (1, 'Existing chair', 'existing-chair', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO multipart_parts (id, multipart_model_id, name, name_key, sort_order, created_at, updated_at) VALUES (1, 1, 'Leg', 'leg', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(config, "head")
        with released_postgres.connect() as connection:
            assert connection.execute(
                text("SELECT name, quantity FROM multipart_parts WHERE id=1")
            ).one() == ("Leg", 1)
        assert {
            "multipart_builds",
            "multipart_build_parts",
            "multipart_build_attempts",
            "multipart_build_confirmations",
        }.issubset(inspect(released_postgres).get_table_names())

    def test_conflicting_results_have_one_winner(self, postgres_engine):
        from tests.fakes.manufacturing import race_confirmations, seed_confirmation_race

        ids = seed_confirmation_race(postgres_engine)
        outcomes = race_confirmations(postgres_engine, *ids, False)
        assert sorted(code for code, _ in outcomes) == ["conflict", "success"]
        assert ("success", 1) in outcomes

    def test_duplicate_results_count_once(self, postgres_engine):
        from tests.fakes.manufacturing import race_confirmations, seed_confirmation_race

        ids = seed_confirmation_race(postgres_engine)
        assert race_confirmations(postgres_engine, *ids, True) == [
            ("success", 1),
            ("success", 1),
        ]


class TestManufacturingQueuePostgres:
    def test_parallel_queue_requests_reserve_each_unit_once(self, postgres_engine):
        from tests.fakes.manufacturing import race_queues

        outcomes, reserved = race_queues(postgres_engine)
        assert outcomes == [("conflict", 0), ("success", 1)]
        assert reserved == 4


@pytest.fixture
def fresh_mesh_measurement_checks(postgres_engine):
    """Capture PostgreSQL's exact rendering before the released-schema reset."""
    return {
        item["name"]: item["sqltext"]
        for item in inspect(postgres_engine).get_check_constraints("metadata")
    }


class TestMeshMeasurementContract:
    @pytest.mark.postgres
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_upgrade_retires_unrepresentable_measurement_facts(
        self,
        fresh_postgres_schema_issues,
        fresh_mesh_measurement_checks,
        released_postgres,
        value,
    ):
        config = _migration_config(released_postgres)
        command.upgrade(config, "67494831ae72")
        with released_postgres.begin() as connection:
            connection.execute(
                text(
                    "UPDATE metadata SET volume_mm3=:value,bbox_x_mm=:value,bbox_y_mm=-1,bbox_z_mm=1e-9 WHERE id=1"
                ),
                {"value": value},
            )
        command.upgrade(config, "head")
        with released_postgres.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT volume_mm3,volume_state,volume_method,volume_unavailable_cause,volume_not_calculated_cause,bbox_x_mm,bbox_y_mm,bbox_z_mm,slicer_name,estimated_time_s FROM metadata WHERE id=1"
                )
            ).one()
        assert row == (
            None,
            "legacy_unassessed",
            None,
            None,
            None,
            None,
            None,
            1e-9,
            "PrusaSlicer",
            3600,
        )
        upgraded_issues = tuple(migrate_mod._orphan_schema_issues(released_postgres))
        checks = {
            item["name"]: item["sqltext"]
            for item in inspect(released_postgres).get_check_constraints("metadata")
        }
        assert upgraded_issues == fresh_postgres_schema_issues
        assert set(checks) == {
            "ck_metadata_bbox_x_mm_physical",
            "ck_metadata_bbox_y_mm_physical",
            "ck_metadata_bbox_z_mm_physical",
            "ck_metadata_volume_evidence",
            "ck_metadata_volume_method_values",
            "ck_metadata_volume_not_calculated_cause_values",
            "ck_metadata_volume_state_values",
            "ck_metadata_volume_unavailable_cause_values",
        }
        assert checks == fresh_mesh_measurement_checks

    @pytest.mark.postgres
    @pytest.mark.parametrize(
        ("state", "value", "cause"),
        [("measured", 1.0, None), ("unavailable", None, "not_watertight")],
    )
    def test_rejects_integral_evidence_without_method(
        self, postgres_engine, state, value, cause
    ):
        with Session(postgres_engine) as session:
            model = build_model(session, slug="volume-evidence", hash="v" * 64)
            artifact = build_file(
                session,
                model,
                path="cube.stl",
                filename="cube.stl",
                file_type=FileType.STL,
                size_bytes=1,
                sha256="a" * 64,
            )
            metadata = build_metadata(session, artifact)
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "UPDATE metadata SET volume_state=:state,volume_mm3=:value,volume_method=NULL,volume_unavailable_cause=:cause,volume_not_calculated_cause=NULL WHERE id=:id"
                    ),
                    {"state": state, "value": value, "cause": cause, "id": metadata.id},
                )
            session.rollback()

    @pytest.mark.postgres
    @pytest.mark.parametrize(
        "field", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm", "volume_mm3"]
    )
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_rejects_nonfinite_measurement_facts(self, postgres_engine, field, value):
        with Session(postgres_engine) as session:
            model = build_model(session, slug="finite-evidence", hash="q" * 64)
            artifact = build_file(
                session,
                model,
                path="cube.stl",
                filename="cube.stl",
                file_type=FileType.STL,
                size_bytes=1,
                sha256="a" * 64,
            )
            metadata = build_metadata(
                session, artifact, volume=VolumeLegacyUnassessed(1.0)
            )
            with pytest.raises(IntegrityError):
                session.execute(
                    text(f"UPDATE metadata SET {field}=:value WHERE id=:id"),
                    {"value": value, "id": metadata.id},
                )
            session.rollback()
