"""Contracts that must run against a real PostgreSQL server.

The ordinary suite remains SQLite-first. CI supplies
``PRINTSTASH_TEST_POSTGRES_URL`` for this focused dialect and concurrency gate.
"""

from __future__ import annotations

import os
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
    Collection,
    CollectionPermission,
    CollectionRole,
    File,
    FileType,
    Model,
    ModelProvenanceSource,
    Printer,
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
from tests.paths import BACKEND_ROOT

_POSTGRES_URL = os.getenv("PRINTSTASH_TEST_POSTGRES_URL")


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator:
    if not _POSTGRES_URL:
        pytest.skip("PRINTSTASH_TEST_POSTGRES_URL is not configured")
    run_migrations(_POSTGRES_URL)
    engine = create_engine(normalize_database_url(_POSTGRES_URL), pool_pre_ping=True)
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


def test_fresh_bootstrap_is_at_head_with_partial_default_index(postgres_engine) -> None:
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
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


def test_postgres_crud_enums_rbac_and_default_uniqueness(postgres_engine) -> None:
    with Session(postgres_engine) as session:
        user = User(username="pg-user", hashed_password="not-used")
        collection = Collection(name="Parts", slug="parts", path="parts")
        printer = Printer(name="PG printer", is_default=True)
        session.add_all([user, collection, printer])
        session.commit()
        session.refresh(user)
        session.refresh(collection)
        session.refresh(printer)

        session.add(
            CollectionPermission(
                user_id=user.id,
                collection_id=collection.id,
                role=CollectionRole.EDIT,
            )
        )
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
        assert effective_printer_role(session, user, printer.id) == PrinterRole.PRINT

        session.add(Printer(name="Conflicting default", is_default=True))
        with pytest.raises(IntegrityError):
            session.commit()


def test_refresh_token_is_consumed_exactly_once_concurrently(postgres_engine) -> None:
    with Session(postgres_engine) as session:
        user = User(username="pg-refresh", hashed_password="not-used")
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


def test_concurrent_identical_provenance_capture_converges_at_savepoints(
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
        model = Model(name="PG provenance", slug="pg-provenance", hash="p" * 64)
        session.add(model)
        session.flush()
        assert model.id is not None
        artifact = File(
            model_id=model.id,
            path="external/pg-provenance.stl",
            original_filename="part.stl",
            file_type=FileType.STL,
            size_bytes=1,
            sha256="a" * 64,
            is_external=True,
        )
        session.add(artifact)
        session.commit()
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
            session.add(
                User(username=f"pg-provenance-outer-{index}", hashed_password="x")
            )
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
            len(session.exec(select(File).where(File.model_id == model_id)).all()) == 1
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


@pytest.mark.asyncio
async def test_psycopg_async_engine_executes_against_real_postgres() -> None:
    if not _POSTGRES_URL:
        pytest.skip("PRINTSTASH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine_for_db(_POSTGRES_URL)
    try:
        async with AsyncSession(engine) as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
