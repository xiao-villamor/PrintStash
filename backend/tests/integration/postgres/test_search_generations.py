"""Concurrent administrator actions preserve one serving generation on PostgreSQL."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import make_url, text
from sqlmodel import SQLModel, create_engine, select

from alembic import command
from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.migrate import _alembic_config
from app.db.models import IndexGeneration
from app.db.session import (
    SQLiteSessionFactory,
    get_session_factory,
    override_session_factory,
)
from app.db.url import normalize_database_url
from app.modules.search import configuration, generations
from app.modules.search.text_inputs import TextRecipe
from app.schemas.inference import SearchSettings
from app.schemas.search_generations import GenerationProposal
from tests.containers import postgres_url
from tests.factories import (
    build_embedding_space,
    build_index_generation,
    build_inference_endpoint,
    build_user,
)


@pytest.fixture
def generation_database():
    schema = "generations_" + uuid4().hex
    root = make_url(normalize_database_url(postgres_url()))
    base = create_engine(root)
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(
        root.update_query_dict(
            {"options": f"-csearch_path={schema} -cstatement_timeout=10000"}
        )
    )
    SQLModel.metadata.create_all(engine)
    original = get_session_factory()
    factory = SQLiteSessionFactory(engine)
    override_session_factory(factory)
    try:
        with factory.scoped_session() as session:
            actor = build_user(session, superuser=True)
            endpoint = build_inference_endpoint(session)
            configuration.update(session, SearchSettings(enabled=True))
            yield SimpleNamespace(
                factory=factory,
                session=session,
                actor=actor,
                endpoint=endpoint,
                base=base,
            )
    finally:
        override_session_factory(original)
        engine.dispose()
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


@pytest.fixture
def ready_generation(generation_database):
    session = generation_database.session
    space = build_embedding_space(
        session,
        modality="text",
        profile="semantic_text",
        recipe_json=TextRecipe().encode(),
    )
    return build_index_generation(
        session,
        space,
        active=False,
        state="building",
        phase="ready",
        verified_at=utcnow(),
        version_token=uuid4().hex,
        building_profile_key="text/semantic_text",
    )


@pytest.fixture
def database_wait():
    def blocked(base, application):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with base.connect() as connection:
                waiting = connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=:app AND wait_event_type='Lock'"
                    ),
                    {"app": application},
                ).scalar_one()
                if waiting:
                    return
            time.sleep(0.01)
        raise AssertionError("concurrent operation did not reach the database lock")

    return blocked


class TestPrepare:
    def test_keeps_the_migrated_postgres_schema_in_sync(self, generation_database):
        env = generation_database
        engine = env.session.get_bind()
        config = _alembic_config(engine.url.render_as_string(hide_password=False))
        env.session.rollback()
        command.stamp(config, "head")
        command.downgrade(config, "bf6dfc561eca")

        command.upgrade(config, "head")
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_server_default": True}
            )
            assert compare_metadata(context, SQLModel.metadata) == []

        assert env.session.exec(select(IndexGeneration)).all() == []

    def test_fences_concurrent_proposals(self, generation_database):
        env = generation_database
        actor_id, endpoint_id = env.actor.id, env.endpoint.id

        def propose():
            with env.factory.scoped_session() as session:
                try:
                    return generations.prepare(
                        session,
                        session.get(type(env.actor), actor_id),
                        GenerationProposal(
                            endpoint_id=endpoint_id, index_backend="numpy"
                        ),
                    ).state
                except OperationError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(copy_context().run, propose)
            second = pool.submit(copy_context().run, propose)
            outcomes = sorted((first.result(timeout=15), second.result(timeout=15)))

        assert outcomes == ["building", "search_generation_building"]
        assert (
            len(
                env.session.exec(
                    select(IndexGeneration).where(IndexGeneration.state == "building")
                ).all()
            )
            == 1
        )


class TestActivate:
    def test_serializes_concurrent_activation(
        self, generation_database, ready_generation
    ):
        env = generation_database
        generation_id, version = ready_generation.id, ready_generation.version_token
        env.session.rollback()

        def activate():
            with env.factory.scoped_session() as session:
                try:
                    return generations.activate(session, generation_id, version).state
                except OperationError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(copy_context().run, activate)
            second = pool.submit(copy_context().run, activate)
            outcomes = sorted((first.result(timeout=15), second.result(timeout=15)))

        assert outcomes == ["active", "search_generation_not_ready"]
        assert env.session.exec(
            select(IndexGeneration.id).where(IndexGeneration.state == "active")
        ).all() == [generation_id]


class TestCancel:
    def test_refuses_cancellation_after_concurrent_activation(
        self, generation_database, ready_generation, database_wait
    ):
        env = generation_database
        generation_id, version = ready_generation.id, ready_generation.version_token
        application = "cancel_" + uuid4().hex
        env.session.exec(
            select(IndexGeneration)
            .where(IndexGeneration.id == generation_id)
            .with_for_update()
        ).one()

        def cancel():
            with env.factory.scoped_session() as session:
                session.execute(
                    text("SELECT set_config('application_name', :app, true)"),
                    {"app": application},
                )
                try:
                    return generations.cancel(session, generation_id, version).state
                except OperationError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(copy_context().run, cancel)
            database_wait(env.base, application)
            generations.activate(env.session, generation_id, version)
            outcome = future.result(timeout=15)

        env.session.expire_all()
        assert outcome == "search_generation_not_building"
        assert env.session.get(IndexGeneration, generation_id).state == "active"


class TestPointGenerations:
    def test_publishes_current_point_units_on_postgres(
        self, generation_database, tmp_path, monkeypatch
    ):
        import hashlib

        from app.core.config import _overlay
        from app.db.models import FileType, PassageVector
        from app.modules.inference import model_cache
        from app.modules.search.indexing import IndexProcessor
        from app.modules.search.retrieval import search
        from tests.factories import build_file, build_model
        from tests.factories.embeddings import (
            local_embedding_assets,
            point_embedding_assets,
        )
        from tests.factories.geometry import tetrahedron

        env = generation_database
        root = tmp_path / "models"
        clip = model_cache.inspect(local_embedding_assets(root / "clip"))
        point = model_cache.inspect(point_embedding_assets(root / "point"))
        monkeypatch.setitem(_overlay, "embedding_cache_dir", root)
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        configuration.update(
            env.session, SearchSettings(enabled=True, local_models_enabled=True)
        )
        model = build_model(env.session, "Opaque object")
        payload = tetrahedron().export(file_type="stl")
        path = tmp_path / "object.stl"
        path.write_bytes(payload)
        file = build_file(
            env.session,
            model,
            file_type=FileType.STL,
            path=str(path),
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            external=True,
        )
        env.session.commit()
        processor = IndexProcessor(env.factory)
        for profile, encoder in (("thumbnail", clip), ("point_cloud", point)):
            proposal = generations.prepare(
                env.session,
                env.actor,
                GenerationProposal(
                    local_model_id=encoder.id, profile=profile, index_backend="numpy"
                ),
            )
            for _ in range(30):
                processor.work_one()
                env.session.expire_all()
                stored = env.session.get(IndexGeneration, proposal.id)
                if stored.state == "active":
                    break
            assert stored.state == "active"
            assert generations.counts(env.session, stored) == (1, 1, 0)
        vector = env.session.exec(
            select(PassageVector).where(PassageVector.generation_id == proposal.id)
        ).one()
        assert (vector.unit_kind, vector.unit_key, vector.input_hash) == (
            "point_cloud",
            f"file:{file.id}:point",
            file.sha256,
        )
        result = search(env.session, env.actor, "gray", legs=("point_cloud",))
        assert [row.subject_id for row in result.items] == [model.id]
