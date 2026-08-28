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
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, create_engine, select

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
from app.services import provenance
from app.services.auth import create_refresh_token, rotate_refresh_token
from app.services.printer_rbac import effective_printer_role
from app.services.rbac import effective_collection_role
from tests.containers import postgres_url
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_printer,
    build_user,
    grant_collection_role,
    printer_config,
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
