"""Durable ingestion migrations and exclusive ownership on supported PostgreSQL."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import make_url, text
from sqlmodel import Session, SQLModel, create_engine

from alembic import command
from app.db.migrate import _alembic_config
from app.db.url import normalize_database_url
from app.modules.ingestion.commands import (
    claim_next,
    execution_scope,
    release,
    require_execution_claim,
)
from tests.containers import postgres_url
from tests.factories import build_background_job
from tests.factories.migration_rows import seed_schema_row


@pytest.fixture
def ingestion_database():
    schema = "ingestion_" + uuid4().hex
    base_url = make_url(normalize_database_url(postgres_url()))
    base = create_engine(base_url)
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    config = _alembic_config(url.render_as_string(hide_password=False))
    try:
        SQLModel.metadata.create_all(engine)
        command.stamp(config, "head")
        yield engine, config
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


class TestIngestionEnrichmentContract:
    def test_upgrades_existing_sources_without_rewriting_their_identity(
        self, ingestion_database
    ):
        engine, config = ingestion_database
        command.downgrade(config, "b49f72e927c9")
        with engine.begin() as connection:
            seed_schema_row(
                connection,
                "models",
                id=1,
                name="Existing",
                slug="existing",
                hash="a" * 64,
            )
            seed_schema_row(
                connection,
                "files",
                id=1,
                model_id=1,
                path="models/original.stl",
                filename="original.stl",
                sha256="b" * 64,
                file_type="STL",
                size_bytes=100,
                thumbnail_path="thumbs/existing.webp",
            )
            connection.execute(
                text(
                    "UPDATE models SET thumbnail_path='thumbs/existing.webp', thumbnail_file_id=1 WHERE id=1"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT path, sha256 FROM files WHERE id=1")
            ).one() == ("models/original.stl", "b" * 64)
            assert connection.execute(
                text(
                    "SELECT thumbnail_selection_version, thumbnail_path, thumbnail_file_id FROM models WHERE id=1"
                )
            ).one() == (0, "thumbs/existing.webp", 1)

    def test_only_one_concurrent_worker_claims_an_accepted_import(
        self, ingestion_database
    ):
        engine, _ = ingestion_database
        with Session(engine) as session:
            job = build_background_job(
                session,
                state="pending",
                replay_safe=True,
                payload_json=json.dumps(
                    {"version": 1, "command": "artifact", "arguments": {}}
                ),
            )
            job_id = job.id
        barrier = Barrier(2)

        def claim():
            with Session(engine) as session:
                barrier.wait(timeout=10)
                return claim_next(session)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claim(), range(2)))
        assert [value.job_id for value in claims if value is not None] == [job_id]

    def test_reclaimed_import_rejects_the_previous_publish_owner(
        self, ingestion_database
    ):
        engine, _ = ingestion_database
        with Session(engine) as session:
            build_background_job(
                session,
                state="pending",
                replay_safe=True,
                payload_json=json.dumps(
                    {"version": 1, "command": "artifact", "arguments": {}}
                ),
            )
            old = claim_next(session)
            release(session, old)
            current = claim_next(session)
            with (
                execution_scope(old),
                pytest.raises(RuntimeError, match="ingestion_claim_lost"),
            ):
                require_execution_claim(session)
            session.rollback()
            with execution_scope(current):
                require_execution_claim(session)
