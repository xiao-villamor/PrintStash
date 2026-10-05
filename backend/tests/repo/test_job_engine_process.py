"""The durable-engine observer reports one committed derivative snapshot."""

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import ArtifactDerivative, DerivativeKind, DerivativeState, File
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
    override_session_factory,
)
from tests import factories
from tests.fakes.job_engine_process import _settle


class TestSettledObservation:
    @pytest.mark.parametrize("previous_thumbnail", [None, "thumbnails/old.png"])
    def test_returns_the_published_thumbnail_when_completion_races_the_observer(
        self, tmp_path: Path, previous_thumbnail: str | None
    ) -> None:
        # Independent WAL connections reproduce the reader/writer interleaving
        # in the real subprocess harness; StaticPool would share a transaction.
        engine = create_engine(f"sqlite:///{tmp_path / 'observation.sqlite'}")
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        factory = SQLiteSessionFactory(engine)
        previous_factory = get_session_factory()
        override_session_factory(factory)
        published = False
        published_thumbnail = "thumbnails/new.png"
        try:
            with factory.scoped_session() as session:
                model = factories.build_model(session)
                file = factories.build_file(
                    session,
                    model,
                    filename="mesh.stl",
                    thumbnail_path=previous_thumbnail,
                )
                factories.build_derivative(session, file, DerivativeKind.METADATA)
                factories.build_derivative(
                    session,
                    file,
                    DerivativeKind.THUMBNAIL,
                    state=DerivativeState.RUNNING,
                )
                file_id = file.id
            assert file_id is not None

            def complete_thumbnail(_reader: Session, loaded: object) -> None:
                nonlocal published
                if published or not isinstance(loaded, File) or loaded.id != file_id:
                    return
                # Set the one-shot flag before loading File in the writer.
                published = True
                with factory.scoped_session() as writer:
                    fresh = writer.get(File, file_id)
                    assert fresh is not None
                    thumbnail = writer.exec(
                        select(ArtifactDerivative).where(
                            ArtifactDerivative.file_id == file_id,
                            ArtifactDerivative.kind == DerivativeKind.THUMBNAIL,
                        )
                    ).one()
                    fresh.thumbnail_path = published_thumbnail
                    thumbnail.state = DerivativeState.READY
                    thumbnail.attempt_token = None
                    thumbnail.storage_key = published_thumbnail
                    writer.add(fresh)
                    writer.add(thumbnail)
                    writer.commit()

            event.listen(Session, "loaded_as_persistent", complete_thumbnail)
            try:
                observed = _settle(file_id)
            finally:
                event.remove(Session, "loaded_as_persistent", complete_thumbnail)

            assert published
            assert observed["states"] == {"metadata": "ready", "thumbnail": "ready"}
            assert observed["thumbnail"] == published_thumbnail
        finally:
            override_session_factory(previous_factory)
            engine.dispose()
