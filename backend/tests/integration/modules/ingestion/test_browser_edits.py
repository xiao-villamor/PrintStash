"""Browser edits arbitrate at the database, including separate process sessions."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier

import pytest
from sqlalchemy import Engine, event, text
from sqlmodel import Session, create_engine

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import BrowserDevice, User
from app.db.session import _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.modules.ingestion import browser_edits, provider_connections
from app.schemas.editing import EditPrecondition
from tests.containers import fresh_postgres_database
from tests.factories import build_user


@dataclass(frozen=True)
class EditingDatabase:
    engine: Engine
    actor_id: int
    device_id: int
    base: EditPrecondition


@pytest.fixture(scope="module")
def pg_edit_engine() -> Iterator[Engine]:
    url = fresh_postgres_database("browser_edit_claim")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def sqlite_edit_engine(tmp_path_factory) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path_factory.mktemp('browser-claims') / 'browser.sqlite'}"
    run_migrations(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)]
)
def editing_db(request) -> Iterator[EditingDatabase]:
    engine = request.getfixturevalue(
        "pg_edit_engine" if request.param == "postgresql" else "sqlite_edit_engine"
    )
    with Session(engine) as session:
        actor = build_user(session)
        assert actor.id is not None
        code, _ = provider_connections.create_pairing_code(session, actor.id)
        session.commit()
        claimed = provider_connections.claim_pairing_code(session, code, "Browser")
        assert claimed is not None
        _, device = claimed
        session.commit()
        assert device.id is not None
        base = browser_edits.editing_base(device)
        environment = EditingDatabase(
            engine,
            actor.id,
            device.id,
            EditPrecondition(
                contract="conditional-v1",
                if_match=f'"browser-device-{device.id}-e{base.edit_epoch}-v{base.edit_version}"',
            ),
        )
    try:
        yield environment
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users"))


class TestRename:
    def test_arbitrates_simultaneous_device_edits(self, editing_db):
        start = Barrier(2)

        def edit(name):
            with Session(editing_db.engine) as session:
                actor = session.get(User, editing_db.actor_id)
                assert actor is not None
                start.wait(timeout=10)
                try:
                    browser_edits.rename(
                        session, actor, editing_db.device_id, name, editing_db.base
                    )
                    session.commit()
                    return "saved"
                except OperationError as error:
                    session.rollback()
                    assert error.kind is ErrorKind.PRECONDITION_FAILED
                    return error.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(edit, "Office")
            second = executor.submit(edit, "Workshop")
            assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
                "edit_conflict",
                "saved",
            ]
        with Session(editing_db.engine) as session:
            device = session.get(BrowserDevice, editing_db.device_id)
            assert device is not None
            assert device.name in {"Office", "Workshop"}
            assert device.edit_version == 2

    @pytest.mark.parametrize(
        "revocation", ["disabled", "deleted", "session", "trashed"]
    )
    def test_refuses_retired_account_authority(self, editing_db, revocation):
        with Session(editing_db.engine) as session:
            actor = session.get(User, editing_db.actor_id)
            assert actor is not None
            with Session(editing_db.engine) as other:
                current = other.get(User, editing_db.actor_id)
                assert current is not None
                if revocation == "disabled":
                    current.is_active = False
                elif revocation == "trashed":
                    current.deleted_at = utcnow()
                elif revocation == "session":
                    current.auth_version += 1
                else:
                    other.delete(current)
                other.commit()
            with pytest.raises(
                OperationError, match="browser_device_permission_denied"
            ) as caught:
                browser_edits.rename(
                    session, actor, editing_db.device_id, "Lost", editing_db.base
                )
            assert caught.value.kind is ErrorKind.FORBIDDEN
            session.rollback()
        with Session(editing_db.engine) as session:
            device = session.get(BrowserDevice, editing_db.device_id)
            if revocation == "deleted":
                assert device is None
            else:
                assert device is not None
                assert device.name == "Browser"
                assert device.edit_version == 1

    def test_rolls_back_an_uncommitted_browser_rename(self, editing_db):
        with Session(editing_db.engine) as session:
            actor = session.get(User, editing_db.actor_id)
            assert actor is not None
            browser_edits.rename(
                session, actor, editing_db.device_id, "Lost", editing_db.base
            )
            session.rollback()
        with Session(editing_db.engine) as session:
            device = session.get(BrowserDevice, editing_db.device_id)
            assert device is not None
            assert device.name == "Browser"
            assert device.edit_version == 1
