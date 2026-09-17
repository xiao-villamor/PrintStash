"""Human caption changes retain a writable SQLite snapshot beside other writers."""

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import Model, User
from app.db.session import _set_sqlite_pragmas
from app.modules.search import captions
from app.schemas.captions import CaptionPatch
from tests.factories import build_model, build_user


class TestPatch:
    def test_dismissal_survives_an_unrelated_writer_after_caption_lookup(
        self, tmp_path
    ):
        engine = create_engine(f"sqlite:///{tmp_path / 'caption-race.sqlite'}")
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as setup:
                actor = build_user(setup, superuser=True)
                model = build_model(setup, "Captioned", description="Human description")
                other = build_model(setup, "Other writer")
                actor_id, model_id, other_id = actor.id, model.id, other.id
                subject = SearchSubject(SubjectType.MODEL, model_id)
                edited = captions.patch(
                    setup,
                    actor,
                    subject,
                    CaptionPatch(action="edit", text="Searchable caption"),
                )
                setup.commit()

            def write_other_model():
                with engine.begin() as writer:
                    writer.exec_driver_sql("PRAGMA busy_timeout=1")
                    writer.exec_driver_sql(
                        "UPDATE models SET description=? WHERE id=?",
                        ("Other write committed", other_id),
                    )

            checkpoint_reached = False
            writer_waited = False

            def after_lookup(
                _connection, _cursor, statement, _parameters, _context, _many
            ):
                # Coordinate two real connections at a read boundary. No DB result
                # or product function is replaced: SQLite decides which writer wins.
                nonlocal checkpoint_reached, writer_waited
                if checkpoint_reached or "FROM subject_captions" not in statement:
                    return
                checkpoint_reached = True
                try:
                    write_other_model()
                except OperationalError as error:
                    if "database is locked" not in str(error):
                        raise
                    writer_waited = True

            with Session(engine) as session:
                actor = session.get(User, actor_id)
                event.listen(session.connection(), "after_cursor_execute", after_lookup)
                result = captions.patch(
                    session,
                    actor,
                    subject,
                    CaptionPatch(action="dismiss", version_token=edited.version_token),
                )
                session.commit()
                assert result.state == "dismissed"
                assert result.text == ""
                assert result.version_token != edited.version_token

            assert checkpoint_reached
            if writer_waited:
                write_other_model()
            with Session(engine) as observer:
                assert captions.lookup(observer, subject).state == "dismissed"
                assert observer.get(Model, model_id).description == "Human description"
                assert (
                    observer.get(Model, other_id).description == "Other write committed"
                )
        finally:
            engine.dispose()
