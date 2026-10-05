"""A thumbnail is published once, immutably, and readers switch to it only then.

Thumbnails are derivatives, so they are re-derived: after a recipe bump, an
administrator's "regenerate all", an audit that found the object missing. Each
publication writes a new content-addressed object and only then moves the
Artifact's pointer (and the Model's, when this Artifact represents it). If this
goes red, a re-derivation can overwrite bytes a browser is being served, a
colliding user file can be clobbered, or a Model's cover flips between its
Artifacts depending on which derivation happened to finish last.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session

from app.db.models import FileType
from app.modules.media.thumbnail_publication import (
    ThumbnailPublicationError,
    point_at,
    publish_thumbnail,
)
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories.protocols import MakeFile, MakeModel

WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 thumbnail-bytes"
OTHER_WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 other-thumbnail"


class TestPublishThumbnail:
    def test_writes_the_encoded_bytes_under_their_key(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        artifact = make_file(make_model(), file_type=FileType.STL)

        published = publish_thumbnail(
            db_session, get_backend(), artifact, WEBP, recipe_tag="thumbnail:1"
        )

        assert get_backend().read_bytes(published.key) == WEBP

    def test_gives_a_new_recipe_its_own_object(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        artifact = make_file(make_model(), file_type=FileType.STL)
        first = publish_thumbnail(
            db_session, get_backend(), artifact, WEBP, recipe_tag="thumbnail:1"
        )

        db_session.commit()
        second = publish_thumbnail(
            db_session, get_backend(), artifact, WEBP, recipe_tag="thumbnail:2"
        )

        assert second.key != first.key

    def test_reuses_the_object_when_the_same_bytes_are_published_again(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        artifact = make_file(make_model(), file_type=FileType.STL)
        first = publish_thumbnail(
            db_session, get_backend(), artifact, WEBP, recipe_tag="thumbnail:1"
        )

        db_session.commit()
        again = publish_thumbnail(
            db_session, get_backend(), artifact, WEBP, recipe_tag="thumbnail:1"
        )

        assert again.key == first.key

    def test_refuses_a_key_already_holding_different_bytes(
        self,
        db_session: Session,
        make_model: MakeModel,
        make_file: MakeFile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact = make_file(make_model(), file_type=FileType.STL)
        backend = get_backend()
        first = publish_thumbnail(
            db_session, backend, artifact, WEBP, recipe_tag="thumbnail:1"
        )
        db_session.commit()
        monkeypatch.setattr(backend, "thumbnail_variant_key", lambda *_args: first.key)

        with pytest.raises(ThumbnailPublicationError, match="collision"):
            publish_thumbnail(
                db_session, backend, artifact, OTHER_WEBP, recipe_tag="thumbnail:1"
            )

    def test_leaves_the_occupying_bytes_untouched_on_a_collision(
        self,
        db_session: Session,
        make_model: MakeModel,
        make_file: MakeFile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact = make_file(make_model(), file_type=FileType.STL)
        backend = get_backend()
        first = publish_thumbnail(
            db_session, backend, artifact, WEBP, recipe_tag="thumbnail:1"
        )
        db_session.commit()
        monkeypatch.setattr(backend, "thumbnail_variant_key", lambda *_args: first.key)

        with pytest.raises(ThumbnailPublicationError):
            publish_thumbnail(
                db_session, backend, artifact, OTHER_WEBP, recipe_tag="thumbnail:1"
            )

        assert Path(first.key).read_bytes() == WEBP


class TestPointAt:
    def test_points_the_artifact_at_its_thumbnail(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        artifact = make_file(make_model(), file_type=FileType.STL)

        point_at(db_session, artifact, "thumbs/new.webp")
        db_session.commit()

        db_session.refresh(artifact)
        assert artifact.thumbnail_path == "thumbs/new.webp"

    def test_makes_a_first_thumbnail_the_models(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        artifact = make_file(model, file_type=FileType.GCODE)

        point_at(db_session, artifact, "thumbs/first.webp")
        db_session.commit()

        db_session.refresh(model)
        assert (model.thumbnail_file_id, model.thumbnail_path) == (
            artifact.id,
            "thumbs/first.webp",
        )

    def test_refreshes_the_model_when_its_representative_is_re_derived(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        artifact = make_file(model, file_type=FileType.STL)
        point_at(db_session, artifact, "thumbs/v1.webp")

        point_at(db_session, artifact, "thumbs/v2.webp")
        db_session.commit()

        db_session.refresh(model)
        assert model.thumbnail_path == "thumbs/v2.webp"

    def test_lets_a_newer_mesh_represent_the_model(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        older = make_file(model, file_type=FileType.STL)
        newer = make_file(model, file_type=FileType.STL)
        point_at(db_session, older, "thumbs/older.webp")

        point_at(db_session, newer, "thumbs/newer.webp")
        db_session.commit()

        db_session.refresh(model)
        assert model.thumbnail_file_id == newer.id

    def test_keeps_the_newer_mesh_when_an_older_one_finishes_last(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        older = make_file(model, file_type=FileType.STL)
        newer = make_file(model, file_type=FileType.STL)
        point_at(db_session, newer, "thumbs/newer.webp")

        point_at(db_session, older, "thumbs/older.webp")
        db_session.commit()

        db_session.refresh(model)
        assert model.thumbnail_file_id == newer.id

    def test_never_lets_gcode_replace_a_mesh_representative(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        mesh = make_file(model, file_type=FileType.STL)
        gcode = make_file(model, file_type=FileType.GCODE)
        point_at(db_session, mesh, "thumbs/mesh.webp")

        point_at(db_session, gcode, "thumbs/gcode.webp")
        db_session.commit()

        db_session.refresh(model)
        assert model.thumbnail_file_id == mesh.id

    def test_lets_a_mesh_replace_a_gcode_representative(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        gcode = make_file(model, file_type=FileType.GCODE)
        mesh = make_file(model, file_type=FileType.STL)
        point_at(db_session, gcode, "thumbs/gcode.webp")

        point_at(db_session, mesh, "thumbs/mesh.webp")
        db_session.commit()

        db_session.refresh(model)
        assert model.thumbnail_file_id == mesh.id

    def test_replaces_a_trashed_representative(
        self, db_session: Session, make_model: MakeModel, make_file: MakeFile
    ) -> None:
        model = make_model()
        trashed = make_file(model, file_type=FileType.STL, trashed=True)
        gcode = make_file(model, file_type=FileType.GCODE)
        model.thumbnail_file_id = trashed.id
        db_session.add(model)
        db_session.commit()

        point_at(db_session, gcode, "thumbs/gcode.webp")
        db_session.commit()

        db_session.refresh(model)
        assert model.thumbnail_file_id == gcode.id
