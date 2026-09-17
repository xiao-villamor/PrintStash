"""Reserving a SQLite writer does not replace a caller-owned transaction."""

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import Model
from app.db.session import _set_sqlite_pragmas
from app.db.transactions import begin_write
from tests.factories import build_model


class TestBeginWrite:
    def test_writer_reservation_preserves_an_existing_transaction(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'caption-rollback.sqlite'}")
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        try:
            with Session(engine) as setup:
                model = build_model(setup, "Original")
                model_id = model.id
                setup.commit()
            with Session(engine) as session:
                model = session.get(Model, model_id)
                model.name = "Uncommitted"
                session.add(model)
                session.flush()
                begin_write(session, immediate=True)
                session.rollback()
            with Session(engine) as observer:
                assert observer.get(Model, model_id).name == "Original"
        finally:
            engine.dispose()
