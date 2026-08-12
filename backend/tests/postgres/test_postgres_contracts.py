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
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine

from app.db.migrate import run_migrations
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    Printer,
    PrinterPermission,
    PrinterRole,
    User,
)
from app.services.auth import create_refresh_token, rotate_refresh_token
from app.services.printer_rbac import effective_printer_role
from app.services.rbac import effective_collection_role

_POSTGRES_URL = os.getenv("PRINTSTASH_TEST_POSTGRES_URL")


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator:
    if not _POSTGRES_URL:
        pytest.skip("PRINTSTASH_TEST_POSTGRES_URL is not configured")
    run_migrations(_POSTGRES_URL)
    engine = create_engine(_POSTGRES_URL, pool_pre_ping=True)
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
    with postgres_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == "d8f5b2c9a1e7"

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

        assert effective_collection_role(session, user, collection.id) == CollectionRole.EDIT
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
