"""Thumbnail repair rebuilds one safe derivative without weakening ownership.

Maintenance may invoke this service against stale rows and colliding storage
keys. A regression could overwrite unowned bytes or republish a trashed Model.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from app.db.models import File, FileType, Model, OwnedStorageObject
from app.services import thumbnail_repair
from app.services.storage_backend import get_backend
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    record_creation,
)

TRASHED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TETRAHEDRON = b"""solid tetrahedron
facet normal 0 0 -1
 outer loop
  vertex 0 0 0
  vertex 0 1 0
  vertex 1 0 0
 endloop
endfacet
facet normal 0 -1 0
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 0 1
 endloop
endfacet
facet normal 1 1 1
 outer loop
  vertex 1 0 0
  vertex 0 1 0
  vertex 0 0 1
 endloop
endfacet
facet normal -1 0 0
 outer loop
  vertex 0 1 0
  vertex 0 0 0
  vertex 0 0 1
 endloop
endfacet
endsolid tetrahedron
"""


def _make_model(
    session: Session,
    slug: str,
    *,
    trashed: bool = False,
) -> Model:
    model = Model(
        name=slug,
        slug=slug,
        hash=hashlib.sha256(slug.encode()).hexdigest(),
        deleted_at=TRASHED_AT if trashed else None,
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


def _add_artifact(
    session: Session,
    model: Model,
    *,
    version: int = 1,
    file_type: FileType = FileType.STL,
    content: bytes | None = _TETRAHEDRON,
    trashed: bool = False,
) -> File:
    assert model.id is not None
    backend = get_backend()
    extension = "stl" if file_type == FileType.STL else file_type.value
    filename = f"version-{version}.{extension}"
    key = backend.blob_key(model.slug, version, filename)
    if content is not None:
        backend.create_bytes(content, key)
    artifact = File(
        model_id=model.id,
        path=key,
        original_filename=filename,
        file_type=file_type,
        version=version,
        size_bytes=len(content or b""),
        sha256=hashlib.sha256(content or b"").hexdigest(),
        deleted_at=TRASHED_AT if trashed else None,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


class TestRegenerateModelThumbnail:
    def test_regenerates_a_missing_thumbnail_from_the_newest_readable_mesh(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-create")
        artifact = _add_artifact(db_session, model)
        assert model.id is not None
        assert artifact.id is not None

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        db_session.refresh(model)
        key = get_backend().thumbnail_key(artifact.id)
        ownership = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).one()
        assert repaired is True
        assert model.thumbnail_file_id == artifact.id
        assert model.thumbnail_path == key
        assert get_backend().read_bytes(key)[:4] == b"RIFF"
        assert ownership.object_kind == "thumbnail"

    def test_replaces_an_already_owned_thumbnail_idempotently(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-replace")
        artifact = _add_artifact(db_session, model)
        assert model.id is not None
        assert artifact.id is not None
        backend = get_backend()
        key = backend.thumbnail_key(artifact.id)
        receipt = backend.create_bytes(b"old-thumbnail", key)
        record_creation(db_session, receipt, object_kind="thumbnail")
        db_session.commit()

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        ownership = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).all()
        assert repaired is True
        assert backend.read_bytes(key)[:4] == b"RIFF"
        assert len(ownership) == 1

    def test_chooses_the_newest_live_supported_mesh_deterministically(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-newest")
        older = _add_artifact(db_session, model, version=1)
        newer = _add_artifact(db_session, model, version=2)
        assert model.id is not None
        assert older.id is not None
        assert newer.id is not None

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        db_session.refresh(model)
        assert repaired is True
        assert model.thumbnail_file_id == newer.id
        assert not get_backend().exists(get_backend().thumbnail_key(older.id))

    @pytest.mark.parametrize(
        ("trash_model", "trash_artifact"),
        [
            pytest.param(True, False, id="trashed-model"),
            pytest.param(False, True, id="trashed-artifact"),
        ],
    )
    def test_ignores_trashed_models_and_mesh_artifacts(
        self,
        db_session: Session,
        trash_model: bool,
        trash_artifact: bool,
    ) -> None:
        model = _make_model(
            db_session, f"repair-trashed-{trash_model}", trashed=trash_model
        )
        artifact = _add_artifact(db_session, model, trashed=trash_artifact)
        assert model.id is not None
        assert artifact.id is not None

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert repaired is False
        assert not get_backend().exists(get_backend().thumbnail_key(artifact.id))

    def test_returns_false_when_the_model_does_not_exist(
        self, db_session: Session
    ) -> None:
        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, 999_999)

        assert repaired is False

    def test_returns_false_when_the_model_has_no_supported_mesh(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-no-mesh")
        _add_artifact(db_session, model, file_type=FileType.GCODE, content=b"G28\n")
        assert model.id is not None

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert repaired is False

    def test_returns_false_when_the_primary_mesh_blob_is_unavailable(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-missing-blob")
        artifact = _add_artifact(db_session, model, content=None)
        assert model.id is not None
        assert artifact.id is not None

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert repaired is False
        assert not get_backend().exists(get_backend().thumbnail_key(artifact.id))

    def test_returns_false_when_the_mesh_cannot_produce_a_preview(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-no-preview")
        artifact = _add_artifact(db_session, model, content=b"")
        assert model.id is not None
        assert artifact.id is not None

        repaired = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert repaired is False
        assert not get_backend().exists(get_backend().thumbnail_key(artifact.id))

    def test_refuses_to_overwrite_an_unowned_thumbnail_collision(
        self, db_session: Session
    ) -> None:
        model = _make_model(db_session, "repair-collision")
        artifact = _add_artifact(db_session, model)
        assert model.id is not None
        assert artifact.id is not None
        backend = get_backend()
        key = backend.thumbnail_key(artifact.id)
        backend.create_bytes(b"unowned-collision", key)

        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_ownership_unverified"
        ):
            thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert backend.read_bytes(key) == b"unowned-collision"

    def test_leaves_model_and_ownership_unchanged_when_storage_creation_fails(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = _make_model(db_session, "repair-write-failure")
        artifact = _add_artifact(db_session, model)
        assert model.id is not None
        assert artifact.id is not None
        backend = get_backend()
        key = backend.thumbnail_key(artifact.id)

        def _fail_create(_data: bytes, _key: str):
            raise OSError("storage unavailable")

        monkeypatch.setattr(backend, "create_bytes", _fail_create)

        with pytest.raises(OSError, match="storage unavailable"):
            thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        db_session.refresh(model)
        ownership = db_session.exec(
            select(OwnedStorageObject).where(OwnedStorageObject.key == key)
        ).all()
        assert model.thumbnail_file_id is None
        assert model.thumbnail_path is None
        assert ownership == []
