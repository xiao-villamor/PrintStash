"""A new Model is created in its Collection, never moved there afterwards.

Moving a Model into a Collection changes who may see it, so every open library
view has to assume its access changed and start over. An upload that created
the Model at the root and then moved it did that once per file, and left the
Model briefly visible at the root.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session

from app.db.models import FileType, Model
from app.modules.ingestion import ingestion
from app.modules.library.model_views.browse import revision
from tests._env import use_local_storage
from tests.factories import library
from tests.factories.geometry import tetrahedron


def _staged(
    tmp_path: Path, name: str, collection: str | None
) -> ingestion.StagedArtifact:
    source = tmp_path / f"{name}.stl"
    mesh = tetrahedron()
    mesh.apply_scale(len(name))  # distinct bytes per name, same bytes per twin
    source.write_bytes(mesh.export(file_type="stl"))
    return ingestion.StagedArtifact(
        source, source.name, name, FileType.STL, collection=collection
    )


def _access(session: Session) -> str:
    session.commit()
    return revision(session).authorization_revision


class TestNewModelHome:
    def test_an_upload_into_a_collection_keeps_access(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        library.build_collection(db_session, "Brackets")
        before = _access(db_session)

        ingestion.commit_staged_artifact(
            _staged(tmp_path, "bracket", "Brackets"), ingestion_key="home-1"
        )

        assert _access(db_session) == before

    def test_an_upload_into_a_new_collection_keeps_access(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        before = _access(db_session)

        ingestion.commit_staged_artifact(
            _staged(tmp_path, "hinge", "Fresh/Hinges"), ingestion_key="home-2"
        )

        assert _access(db_session) == before

    def test_the_model_is_inserted_in_its_collection(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        moves: list[str] = []

        def record(_conn, _cursor, statement, *_rest) -> None:
            if statement.startswith("UPDATE models") and "collection_id" in statement:
                moves.append(statement)

        target = db_session.get_bind()
        event.listen(target, "before_cursor_execute", record)
        try:
            outcome = ingestion.commit_staged_artifact(
                _staged(tmp_path, "clip", "Clips"), ingestion_key="home-3"
            )
        finally:
            event.remove(target, "before_cursor_execute", record)

        model = db_session.get(Model, outcome.model_id)
        assert model is not None and model.collection_rel is not None
        assert (model.collection_rel.path, moves) == ("clips", [])

    def test_an_upload_without_a_collection_stays_at_the_root(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)

        outcome = ingestion.commit_staged_artifact(
            _staged(tmp_path, "loose", None), ingestion_key="home-4"
        )

        model = db_session.get(Model, outcome.model_id)
        assert model is not None and model.collection_id is None

    def test_a_duplicate_keeps_the_collection_it_already_had(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        use_local_storage(tmp_path)
        first = ingestion.commit_staged_artifact(
            _staged(tmp_path, "twin", "Original"), ingestion_key="home-5"
        )

        again = ingestion.commit_staged_artifact(
            _staged(tmp_path, "twin", "Elsewhere"), ingestion_key="home-6"
        )

        model = db_session.get(Model, again.model_id)
        assert model is not None and model.collection_rel is not None
        assert (again.model_id, model.collection_rel.path) == (
            first.model_id,
            "original",
        )
