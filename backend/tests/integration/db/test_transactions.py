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


class TestWriterWaitTelemetry:
    """Each writer reservation is timed and slow ones name who waited (F5)."""

    @staticmethod
    def _engine(path):
        engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        return engine

    @staticmethod
    def _count(kind: str) -> float:
        from app.core.metrics import registry

        return (
            registry.get_sample_value(
                "printstash_sqlite_writer_wait_seconds_count", {"kind": kind}
            )
            or 0.0
        )

    def test_a_job_writer_wait_is_counted_under_its_definition(self, tmp_path):
        from app.core.execution_scope import ExecutionIdentity, execution_scope

        engine = self._engine(tmp_path / "wait.sqlite")
        before = self._count("derivatives.mesh")
        try:
            with (
                execution_scope(ExecutionIdentity("j", "j:e:1", "derivatives.mesh")),
                Session(engine) as session,
            ):
                begin_write(session, immediate=True)
                session.rollback()
        finally:
            engine.dispose()

        assert self._count("derivatives.mesh") == before + 1

    def test_a_writer_wait_outside_jobs_is_counted_as_none(self, tmp_path):
        engine = self._engine(tmp_path / "wait.sqlite")
        before = self._count("none")
        try:
            with Session(engine) as session:
                begin_write(session, immediate=True)
                session.rollback()
        finally:
            engine.dispose()

        assert self._count("none") == before + 1

    def test_a_deferred_begin_is_not_timed(self, tmp_path):
        engine = self._engine(tmp_path / "wait.sqlite")
        before = self._count("none")
        try:
            with Session(engine) as session:
                begin_write(session)
                session.rollback()
        finally:
            engine.dispose()

        assert self._count("none") == before

    def test_a_slow_wait_logs_the_job_that_waited(self, tmp_path, caplog):
        import logging
        import threading

        from app.core.execution_scope import ExecutionIdentity, execution_scope
        from app.db import transactions

        caplog.set_level(logging.INFO, logger=transactions.logger.name)
        engine = self._engine(tmp_path / "slow.sqlite")
        holder = engine.connect()
        holder.exec_driver_sql("BEGIN IMMEDIATE")
        release = threading.Timer(0.4, holder.rollback)
        release.start()
        try:
            with (
                execution_scope(
                    ExecutionIdentity("job-9", "job-9:e:1", "search.project")
                ),
                Session(engine) as session,
            ):
                begin_write(session, immediate=True)
                session.rollback()
        finally:
            release.join()
            holder.close()
            engine.dispose()

        line = next(
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("sqlite_writer_wait ")
        )
        assert "job_id=job-9" in line and "definition=search.project" in line
