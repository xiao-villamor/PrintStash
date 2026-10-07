"""Source edit claims arbitrate independent sessions and retired authority."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, create_engine

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import ExternalLibrary, User
from app.db.session import _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.modules.administration import runtime_config
from app.modules.sources import edits as editing
from app.schemas.editing import EditPrecondition
from tests.containers import fresh_postgres_database
from tests.factories import build_external_library, build_user


@pytest.fixture(scope="module")
def pg_source_engine():
    url = fresh_postgres_database("source_edit_claim")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def sqlite_source_engine(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('source-claims') / 'connections.sqlite'}"
    run_migrations(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    yield engine
    engine.dispose()


@pytest.fixture(
    params=[
        "sqlite_source_engine",
        pytest.param("pg_source_engine", marks=pytest.mark.postgres),
    ],
    ids=["sqlite", "postgres"],
)
def editing_db(request, tmp_path):
    engine = request.getfixturevalue(request.param)
    with Session(engine) as session:
        actor = build_user(session, superuser=True)
        runtime_config.set_external_libraries_enabled(session, True)
        row = build_external_library(session, tmp_path / "source")
        base = editing.source_base(row)
        context = (
            engine,
            actor.id,
            ExternalLibrary,
            row.id,
            editing.claim_source,
            EditPrecondition(
                contract="conditional-v1",
                if_match=f'"library-source-{row.id}-e{base.edit_epoch}-v{base.edit_version}"',
            ),
        )
    yield context
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM external_libraries"))
        connection.execute(text("DELETE FROM users"))


@pytest.fixture(
    params=["disabled", "demoted", "deleted", "session", "trashed", "feature"]
)
def retired_actor(request, editing_db):
    engine, actor_id, table, row_id, claim, base = editing_db
    with Session(engine) as session:
        actor = session.get(User, actor_id)
        row = session.get(table, row_id)
        revocation = request.param
        with Session(engine) as other:
            current = other.get(User, actor_id)
            if revocation == "feature":
                runtime_config.set_external_libraries_enabled(other, False)
            elif revocation == "disabled":
                current.is_active = False
            elif revocation == "demoted":
                current.is_superuser = False
            elif revocation == "session":
                current.auth_version += 1
            elif revocation == "trashed":
                current.deleted_at = utcnow()
            else:
                other.delete(current)
            other.commit()
        yield session, actor, row, claim, base, revocation


def _stored_version(session, table, row_id):
    row = session.get(table, row_id)
    return editing.source_base(row).edit_version


class TestSourceClaims:
    def test_arbitrates_independent_source_writers(self, editing_db):
        engine, actor_id, table, row_id, claim, base = editing_db
        start = Barrier(2)

        def save():
            with Session(engine) as session:
                actor = session.get(User, actor_id)
                row = session.get(table, row_id)
                start.wait(timeout=10)
                try:
                    claim(session, actor, row, base)
                    session.commit()
                    return "saved"
                except OperationError as error:
                    session.rollback()
                    assert error.kind is ErrorKind.PRECONDITION_FAILED
                    return error.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(save)
            second = executor.submit(save)
            assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
                "edit_conflict",
                "saved",
            ]

    def test_refuses_retired_source_authority(self, editing_db, retired_actor):
        engine, _, table, row_id, _, _ = editing_db
        session, actor, row, claim, base, revocation = retired_actor
        with pytest.raises(
            OperationError,
            match="feature_disabled"
            if revocation == "feature"
            else "library_source_permission_denied",
        ) as caught:
            claim(session, actor, row, base)
        assert caught.value.kind is (
            ErrorKind.NOT_FOUND if revocation == "feature" else ErrorKind.FORBIDDEN
        )
        session.rollback()
        with Session(engine) as verification:
            assert _stored_version(verification, table, row_id) == 1
