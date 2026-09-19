"""SQLite cutover owns a real writer transaction before validating its snapshot."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import event, update
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.time import utcnow
from app.db.models import IndexGeneration, User
from app.db.session import _set_sqlite_pragmas
from app.modules.search import configuration, generations
from app.modules.search.text_inputs import TextRecipe
from app.schemas.inference import SearchSettings
from tests.factories import build_embedding_space, build_index_generation, build_user


@pytest.fixture
def cutover_database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cutover.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


class TestCutoverConcurrency:
    def test_drains_competing_writes_before_verifying_activation(
        self, cutover_database, monkeypatch
    ):
        session = cutover_database
        actor = build_user(session, superuser=True)
        space = build_embedding_space(
            session,
            modality="text",
            profile="semantic_text",
            recipe_json=TextRecipe().encode(),
        )
        old = build_index_generation(session, space)
        replacement = build_index_generation(
            session,
            space,
            active=False,
            state="building",
            phase="ready",
            verified_at=utcnow(),
            version_token="f" * 32,
            building_profile_key="text/semantic_text",
            replaces_generation_id=old.id,
        )
        configuration.update(session, SearchSettings(enabled=True))
        old_id, replacement_id, actor_id = old.id, replacement.id, actor.id
        session.commit()
        engine = session.get_bind()
        started, finished = Event(), Event()
        futures = []

        def observe(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("UPDATE users SET auth_version"):
                started.set()

        def competing_write():
            with Session(engine) as other:
                other.exec(
                    update(User).where(User.id == actor_id).values(auth_version=2)
                )
                other.commit()
            finished.set()

        original = generations.verify_counts
        with ThreadPoolExecutor(max_workers=1) as executor:

            def verify(current, generation):
                result = original(current, generation)
                futures.append(executor.submit(competing_write))
                assert started.wait(3), "competing writer did not reach SQLite"
                assert not finished.wait(0.1), (
                    "writer committed inside the cutover snapshot"
                )
                return result

            monkeypatch.setattr(generations, "verify_counts", verify)
            event.listen(engine, "before_cursor_execute", observe)
            try:
                result = generations.activate(
                    session, replacement_id, replacement.version_token
                )
                assert result.state == "active"
                session.commit()
            finally:
                session.rollback()
                event.remove(engine, "before_cursor_execute", observe)
            for future in futures:
                future.result(timeout=3)
        assert finished.is_set()
        assert session.exec(
            select(IndexGeneration.id).where(IndexGeneration.state == "active")
        ).all() == [replacement_id]
        assert session.get(IndexGeneration, old_id).state == "retired"
