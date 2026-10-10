"""Saving personal search preferences must survive concurrent background writes."""

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import Model, User, UserSearchPreferences
from app.db.session import _set_sqlite_pragmas
from app.modules.search import preferences
from app.schemas.search_parsing import SearchPreferencesPatch
from tests.factories import build_model, build_user, build_user_search_preferences


@pytest.fixture(params=[False, True], ids=["first-save", "existing-preferences"])
def preferences_database(tmp_path, request):
    engine = create_engine(f"sqlite:///{tmp_path / 'preferences-race.sqlite'}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as setup:
        actor = build_user(setup, superuser=True)
        other = build_model(setup, "Concurrent writer")
        if request.param:
            build_user_search_preferences(setup, actor, nl_filters_enabled=False)
        ids = actor.id, other.id
    try:
        yield engine, *ids
    finally:
        engine.dispose()


class TestUpdate:
    def test_saves_preferences_beside_another_sqlite_writer(self, preferences_database):
        engine, actor_id, other_id = preferences_database
        checkpoint_reached = False

        def write_other_model():
            with engine.begin() as writer:
                writer.exec_driver_sql("PRAGMA busy_timeout=1")
                writer.exec_driver_sql(
                    "UPDATE models SET description=? WHERE id=?",
                    ("Concurrent update", other_id),
                )

        def after_lookup(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal checkpoint_reached
            if checkpoint_reached or "FROM user_search_preferences" not in statement:
                return
            checkpoint_reached = True
            try:
                write_other_model()
            except OperationalError as error:
                if "database is locked" not in str(error):
                    raise

        with Session(engine) as session:
            actor = session.get(User, actor_id)
            event.listen(session.connection(), "after_cursor_execute", after_lookup)

            result = preferences.update(
                session,
                actor,
                SearchPreferencesPatch(
                    nl_filters_enabled=True, timezone="Europe/Madrid"
                ),
            )

        assert checkpoint_reached
        assert result.nl_filters_enabled is True
        assert result.effective_timezone == "Europe/Madrid"
        write_other_model()
        with Session(engine) as observer:
            stored = observer.get(UserSearchPreferences, actor_id)
            assert stored.nl_filters_enabled is True
            assert stored.timezone == "Europe/Madrid"
            assert observer.get(Model, other_id).description == "Concurrent update"
