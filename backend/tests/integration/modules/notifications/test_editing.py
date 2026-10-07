"""Notification edit claims arbitrate independent sessions and retired authority."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, create_engine

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.migrate import run_migrations
from app.db.models import NotificationChannel, User
from app.db.session import _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.modules.notifications import editing
from app.schemas.editing import EditPrecondition
from tests.containers import fresh_postgres_database
from tests.factories import build_notification_channel, build_system_config, build_user


@pytest.fixture(scope="module")
def pg_notification_engine():
    url = fresh_postgres_database("notification_edit_claim")
    run_migrations(url)
    engine = create_engine(normalize_database_url(url))
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def sqlite_notification_engine(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('notification-claims') / 'notifications.sqlite'}"
    run_migrations(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_sqlite_pragmas)
    yield engine
    engine.dispose()


@pytest.fixture(
    params=[
        ("sqlite_notification_engine", "channel"),
        ("sqlite_notification_engine", "settings"),
        pytest.param(("pg_notification_engine", "channel"), marks=pytest.mark.postgres),
        pytest.param(
            ("pg_notification_engine", "settings"), marks=pytest.mark.postgres
        ),
    ],
    ids=["sqlite-channel", "sqlite-settings", "postgres-channel", "postgres-settings"],
)
def editing_db(request):
    engine_name, kind = request.param
    engine = request.getfixturevalue(engine_name)
    with Session(engine) as session:
        actor = build_user(session, superuser=True)
        if kind == "channel":
            row = build_notification_channel(session, events=["print_completed"])
            base = editing.channel_base(row)
            aggregate = f"notification-channel-{row.id}"
            claim = editing.claim_channel
        else:
            row = build_system_config(session)
            base, _ = editing.read_settings(session)
            aggregate = "notification-settings"
            claim = editing.claim_settings
        context = (
            engine,
            actor.id,
            type(row),
            row.id,
            claim,
            EditPrecondition(
                contract="conditional-v1",
                if_match=f'"{aggregate}-e{base.edit_epoch}-v{base.edit_version}"',
            ),
        )
    yield context
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM notification_channels"))
        connection.execute(text("DELETE FROM system_config"))
        connection.execute(text("DELETE FROM users"))


@pytest.fixture(params=["disabled", "demoted", "deleted", "session", "trashed"])
def retired_actor(request, editing_db):
    engine, actor_id, table, row_id, claim, base = editing_db
    with Session(engine) as session:
        actor = session.get(User, actor_id)
        row = session.get(table, row_id)
        revocation = request.param
        with Session(engine) as other:
            current = other.get(User, actor_id)
            if revocation == "disabled":
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
        yield session, actor, row, claim, base


def _stored_version(session, table, row_id):
    row = session.get(table, row_id)
    return (
        editing.channel_base(row).edit_version
        if table is NotificationChannel
        else editing.read_settings(session)[0].edit_version
    )


class TestNotificationClaims:
    def test_arbitrates_independent_notification_writers(self, editing_db):
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

    def test_refuses_retired_administrator_authority(self, editing_db, retired_actor):
        engine, _, table, row_id, _, _ = editing_db
        session, actor, row, claim, base = retired_actor
        with pytest.raises(
            OperationError, match="notification_permission_denied"
        ) as caught:
            claim(session, actor, row, base)
        assert caught.value.kind is ErrorKind.FORBIDDEN
        session.rollback()
        with Session(engine) as verification:
            assert _stored_version(verification, table, row_id) == 1
