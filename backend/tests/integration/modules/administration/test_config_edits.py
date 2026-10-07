"""Configuration edit claims serialize competing writers on both supported databases."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, event, text
from sqlmodel import Session, create_engine, select

from app.core.errors import ErrorKind, OperationError
from app.db.migrate import run_migrations
from app.db.models import LibraryRevision, SystemConfig, User
from app.db.session import _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.schemas.editing import EditingBase
from tests.containers import fresh_postgres_database
from tests.factories import build_system_config, build_user


@dataclass(frozen=True)
class EditingDatabase:
    engine: Engine
    actor_id: int
    base: EditingBase


@pytest.fixture(scope="module")
def pg_edit_engine() -> Iterator[Engine]:
    url = fresh_postgres_database("config_edit_claim")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def sqlite_edit_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    url = (
        f"sqlite:///{tmp_path_factory.mktemp('config-claims') / 'configuration.sqlite'}"
    )
    run_migrations(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", id="postgres", marks=pytest.mark.postgres),
    ]
)
def editing_db(request: pytest.FixtureRequest) -> Iterator[EditingDatabase]:
    engine = request.getfixturevalue(
        "pg_edit_engine" if request.param == "postgresql" else "sqlite_edit_engine"
    )
    with Session(engine) as session:
        row = build_system_config(session, currency="USD")
        actor = build_user(session, superuser=True)
        assert actor.id is not None
        base = EditingBase(
            edit_epoch=session.exec(
                select(LibraryRevision.epoch).where(LibraryRevision.id == 1)
            ).one(),
            edit_version=row.vault_edit_version,
        )
        environment = EditingDatabase(engine, actor.id, base)
    try:
        yield environment
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM system_config"))
            connection.execute(text("DELETE FROM users"))


class TestClaim:
    def test_only_one_editor_can_commit_from_a_shared_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.administration.config_edits import claim

        start = Barrier(2)

        def editor(currency: str) -> str:
            with Session(editing_db.engine) as session:
                actor = session.get(User, editing_db.actor_id)
                row = session.get(SystemConfig, 1)
                assert actor is not None and row is not None
                start.wait(timeout=10)
                try:
                    claim(session, actor, row, editing_db.base)
                    row.currency = currency
                    session.add(row)
                    session.commit()
                    return "saved"
                except OperationError as error:
                    session.rollback()
                    assert error.kind is ErrorKind.PRECONDITION_FAILED
                    return error.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(editor, "EUR")
            second = executor.submit(editor, "GBP")
            outcomes = [first.result(timeout=20), second.result(timeout=20)]
        assert sorted(outcomes) == ["edit_conflict", "saved"]
        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            assert row is not None
            assert row.currency in {"EUR", "GBP"}
            assert row.vault_edit_version > editing_db.base.edit_version

    def test_rollback_preserves_an_unused_editing_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.administration.config_edits import claim

        with Session(editing_db.engine) as session:
            actor = session.get(User, editing_db.actor_id)
            row = session.get(SystemConfig, 1)
            assert actor is not None and row is not None
            claim(session, actor, row, editing_db.base)
            row.currency = "EUR"
            session.add(row)
            session.flush()
            session.rollback()
        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            assert row.currency == "USD"
            assert row.vault_edit_version == editing_db.base.edit_version
            claim(session, actor, row, editing_db.base)
            row.currency = "GBP"
            session.add(row)
            session.commit()

    def test_a_restored_database_rejects_a_prior_incarnation(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.administration.config_edits import claim

        with editing_db.engine.begin() as connection:
            connection.execute(
                text("UPDATE library_revision SET epoch=:epoch WHERE id=1"),
                {"epoch": uuid4().hex},
            )
        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            with pytest.raises(OperationError, match="edit_conflict"):
                claim(session, actor, row, editing_db.base)
            session.rollback()
            assert row.currency == "USD"
            assert row.vault_edit_version == editing_db.base.edit_version

    def test_a_legacy_writer_invalidates_an_earlier_conditional_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.administration.config_edits import claim

        with editing_db.engine.begin() as connection:
            connection.execute(
                text("UPDATE system_config SET currency='EUR' WHERE id=1")
            )
        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            before = row.vault_edit_version
            with pytest.raises(OperationError, match="edit_conflict"):
                claim(session, actor, row, editing_db.base)
            session.rollback()
            assert row.currency == "EUR"
            assert row.vault_edit_version == before

    @pytest.mark.parametrize(
        "revocation", ["disabled", "demoted", "deleted", "session"], ids=str
    )
    def test_revoked_administrator_authority_rejects_the_edit(
        self, editing_db: EditingDatabase, revocation: str
    ) -> None:
        from app.modules.administration.config_edits import claim

        with Session(editing_db.engine) as session:
            actor = session.get(User, editing_db.actor_id)
            row = session.get(SystemConfig, 1)
            assert actor is not None and row is not None
            with Session(editing_db.engine) as other:
                revoked = other.get(User, editing_db.actor_id)
                assert revoked is not None
                if revocation == "deleted":
                    other.delete(revoked)
                else:
                    if revocation == "disabled":
                        revoked.is_active = False
                    elif revocation == "demoted":
                        revoked.is_superuser = False
                    else:
                        revoked.auth_version += 1
                    other.add(revoked)
                other.commit()
            with pytest.raises(
                OperationError, match="config_permission_denied"
            ) as caught:
                claim(session, actor, row, editing_db.base)
            assert caught.value.kind is ErrorKind.FORBIDDEN
            session.rollback()
            assert row.currency == "USD"
            assert row.vault_edit_version == editing_db.base.edit_version

    def test_a_current_administrator_can_commit_an_explicit_edit(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.administration.config_edits import claim

        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            claim(session, actor, row, editing_db.base)
            row.currency = "EUR"
            session.add(row)
            session.commit()
        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            assert row is not None
            assert row.currency == "EUR"
            assert row.vault_edit_version > editing_db.base.edit_version

    def test_explicit_legacy_commands_can_save_without_a_base(
        self, editing_db: EditingDatabase
    ) -> None:
        from app.modules.administration.config_edits import claim

        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            actor = session.get(User, editing_db.actor_id)
            assert row is not None and actor is not None
            claim(session, actor, row, None)
            row.currency = "EUR"
            session.add(row)
            session.commit()
        with Session(editing_db.engine) as session:
            row = session.get(SystemConfig, 1)
            assert row is not None
            assert row.currency == "EUR"
            assert row.vault_edit_version > editing_db.base.edit_version

    def test_rejects_a_non_singleton_target(self, editing_db: EditingDatabase) -> None:
        from app.modules.administration.config_edits import claim

        with Session(editing_db.engine) as session:
            target = build_system_config(session, id=2, currency="EUR")
            actor = session.get(User, editing_db.actor_id)
            assert actor is not None
            with pytest.raises(
                ValueError, match="config_edit_requires_persisted_singleton"
            ):
                claim(session, actor, target, editing_db.base)
            session.rollback()
            row = session.get(SystemConfig, 1)
            assert row is not None
            assert row.currency == "USD"
            assert row.vault_edit_version == editing_db.base.edit_version
