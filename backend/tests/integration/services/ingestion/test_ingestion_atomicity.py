"""``persist_artifact`` writes one artifact, or nothing at all.

It used to commit the File row before writing the thumbnail and the Metadata
row. A failure in between (a corrupt image, a full disk) left a committed File
with no metadata — a model that renders but has no print time, filament or cost,
and no error anywhere to explain it.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import _overlay
from app.db.models import (
    File,
    FileType,
    Metadata,
    Model,
    ModelProvenanceField,
    ProvenanceCapture,
)
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas
from app.services import ingestion, provenance, thumbnail
from app.services.jobs import registry
from app.services.storage_backend import get_backend
from tests.factories import (
    build_file,
    build_model,
    build_user,
)


@pytest.fixture
def storage(tmp_path: Path):
    _overlay["storage_backend"] = "local"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    (tmp_path / "files").mkdir()
    (tmp_path / "thumbs").mkdir()
    yield get_backend()
    for key in ("storage_backend", "data_dir", "thumb_dir"):
        _overlay.pop(key, None)


@pytest.fixture
def model(db_session: Session) -> Model:
    model = build_model(db_session, name="Bracket", slug="bracket", hash="h" * 64)
    return model


def _staged(tmp_path: Path, name: str = "bracket.stl") -> Path:
    staged = tmp_path / name
    staged.write_bytes(b"solid bracket\nendsolid\n")
    return staged


def _persist(db_session: Session, model: Model, staged: Path, **kwargs):
    defaults = dict(
        model=model,
        staged_path=staged,
        original_filename=staged.name,
        file_type=FileType.STL,
        blob_hash="b" * 64,
        meta={"estimated_time_s": 120},
        thumb_bytes=None,
        overwrite_thumbnail=True,
    )
    defaults.update(kwargs)
    return ingestion.persist_artifact(db_session, **defaults)


class TestPersistArtifact:
    def test_persist_never_overwrites_an_unclaimed_destination(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        occupied = Path(storage.blob_key(model.slug, 1, "bracket.stl"))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"pre-existing user data")

        file_row = _persist(db_session, model, _staged(tmp_path))

        assert occupied.read_bytes() == b"pre-existing user data"
        assert file_row.path != str(occupied)
        assert Path(file_row.path).read_bytes() == b"solid bracket\nendsolid\n"

    def test_concurrent_same_hash_upload_dedups_instead_of_crashing(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Model.hash is UNIQUE. Two uploads of the same bytes race between the
        lookup and the insert; the loser must dedup onto the winner's model, not
        500 with an IntegrityError."""
        from app.db.session import get_session_factory
        from app.services import storage as storage_mod

        dedup_hash = "c" * 64
        real_ensure = storage_mod.ensure_unique_slug

        def _insert_the_winner(base_slug, exists):
            # Runs after resolve_or_create_model's SELECT found nothing and before
            # its INSERT lands — exactly the window the race lives in.
            with get_session_factory().session() as other:
                build_model(other, name="Winner", slug="winner", hash=dedup_hash)
            return real_ensure(base_slug, exists)

        monkeypatch.setattr(ingestion.storage, "ensure_unique_slug", _insert_the_winner)

        model, created = ingestion.resolve_or_create_model(
            db_session, dedup_hash=dedup_hash, model_name="Loser"
        )

        assert created is False
        assert model.name == "Winner"
        assert (
            len(db_session.exec(select(Model).where(Model.hash == dedup_hash)).all())
            == 1
        )


class TestReserveNextVersion:
    def test_version_numbers_increment_across_revisions(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        first = _persist(db_session, model, _staged(tmp_path, "v1.stl"))
        second = _persist(db_session, model, _staged(tmp_path, "v2.stl"))

        assert (first.version, second.version) == (1, 2)

    def test_concurrent_version_reservations_are_unique(self, tmp_path: Path) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'versions.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            concurrent_model = build_model(
                session, name="Concurrent", slug="concurrent", hash="c" * 64
            )
            model_id = concurrent_model.id
        assert model_id is not None

        start = threading.Barrier(3)
        versions: list[int] = []
        errors: list[BaseException] = []

        def reserve() -> None:
            try:
                with Session(engine) as session:
                    start.wait(timeout=5)
                    version = ingestion._reserve_next_version(session, model_id)
                    session.commit()
                    versions.append(version)
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)

        try:
            assert errors == []
            assert sorted(versions) == [1, 2]
            with Session(engine) as session:
                assert session.get(Model, model_id).next_file_version == 3
        finally:
            engine.dispose()

    def test_concurrent_artifacts_get_distinct_versions_under_contention(
        self, tmp_path: Path, storage
    ) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'artifacts.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            concurrent_model = build_model(
                session, name="Race", slug="race", hash="a" * 64
            )
            model_id = concurrent_model.id
        assert model_id is not None

        staged: list[tuple[Path, bytes]] = []
        for index in range(2):
            content = f"G28 ; artifact {index}\n".encode()
            path = tmp_path / f"race-{index}.gcode"
            path.write_bytes(content)
            staged.append((path, content))

        start = threading.Barrier(3)
        errors: list[BaseException] = []

        def persist(path: Path, content: bytes) -> None:
            try:
                with Session(engine) as session:
                    model_row = session.get(Model, model_id)
                    assert model_row is not None
                    start.wait(timeout=5)
                    ingestion.persist_artifact(
                        session,
                        model=model_row,
                        staged_path=path,
                        original_filename=path.name,
                        file_type=FileType.GCODE,
                        blob_hash=hashlib.sha256(content).hexdigest(),
                        meta={},
                        thumb_bytes=None,
                        overwrite_thumbnail=False,
                    )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=persist, args=item) for item in staged]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=15)

        try:
            assert errors == []
            with Session(engine) as session:
                rows = session.exec(
                    select(File).where(File.model_id == model_id).order_by(File.version)
                ).all()
            assert [row.version for row in rows] == [1, 2]
            assert sum(row.is_recommended for row in rows) == 1
            for row in rows:
                assert (
                    hashlib.sha256(Path(row.path).read_bytes()).hexdigest()
                    == row.sha256
                )
        finally:
            engine.dispose()


class TestMetadata:
    def test_persists_a_file_row_with_its_metadata_in_one_commit(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        file_row = _persist(db_session, model, _staged(tmp_path))

        assert file_row.id is not None
        md = db_session.exec(
            select(Metadata).where(Metadata.file_id == file_row.id)
        ).first()
        assert md is not None and md.estimated_time_s == 120

    def test_failed_metadata_does_not_leave_orphan_file_row(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A File row without its Metadata is the silent-corruption case."""

        def _boom(*_args, **_kwargs):
            raise RuntimeError("metadata boom")

        # ``Metadata`` is only ever called to construct the row; model_fields is read
        # first, so keep that attribute intact.
        _boom.model_fields = ingestion.Metadata.model_fields
        monkeypatch.setattr(ingestion, "Metadata", _boom)

        with pytest.raises(RuntimeError, match="metadata boom"):
            _persist(db_session, model, _staged(tmp_path))

        db_session.rollback()
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )
        assert not Path(storage.blob_key(model.slug, 1, "bracket.stl")).exists()


class TestProvenance:
    def test_provenance_attachment_shares_artifact_transaction(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A provenance failure must roll back the freshly-flushed Artifact too."""

        seen_file_ids: list[int] = []

        def _boom(session: Session, file_row: File, context: object) -> None:
            del session, context
            assert file_row.id is not None
            seen_file_ids.append(file_row.id)
            raise RuntimeError("provenance boom")

        monkeypatch.setattr(ingestion, "_attach_ingested_artifact", _boom)

        with pytest.raises(RuntimeError, match="provenance boom"):
            _persist(
                db_session,
                model,
                _staged(tmp_path),
                provenance_context=object(),
            )

        db_session.rollback()
        assert seen_file_ids
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )

    def test_deduplicated_pipeline_recapture_refreshes_provenance_without_new_artifact(
        self, db_session: Session, storage, tmp_path: Path
    ) -> None:
        """A reusable blob still records a newer source snapshot before terminal dedupe."""
        staged = _staged(tmp_path)
        blob_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        actor = build_user(db_session, "capture-owner", superuser=True)
        model = build_model(db_session, name="Bracket", slug="bracket", hash=blob_hash)
        db_session.refresh(actor)
        db_session.refresh(model)
        assert model.id is not None
        file_row = build_file(
            db_session,
            model,
            path="provenance/existing.stl",
            filename=staged.name,
            file_type=FileType.STL,
            size_bytes=staged.stat().st_size,
            sha256=blob_hash,
        )

        def manifest(title: str, revision: str):
            return provenance.CaptureManifestV2.from_dict(
                {
                    "schema_version": 2,
                    "kind": "model_files",
                    "source": {
                        "provider": "printables",
                        "canonical_url": "https://printables.com/model/42",
                        "source_item_id": "42",
                        "source_revision": revision,
                        "adapter_version": "test",
                        "tags": [],
                        "fields": {"title": {"value": title, "origin": "confirmed"}},
                    },
                    "files": [
                        {
                            "id": "42:file",
                            "name": staged.name,
                            "file_type": "stl",
                            "size": staged.stat().st_size,
                        }
                    ],
                    "selected_ids": ["42:file"],
                }
            )

        first = provenance.ProvenanceContext(
            manifest=manifest("Original", "r1"),
            source_file_id="42:file",
            source_filename=staged.name,
            blob_sha256=blob_hash,
            actor_id=actor.id,
        )
        link = provenance.attach_ingested_artifact(db_session, file_row, first)
        provenance.set_user_override(
            db_session,
            provenance_source_id=link.provenance_source_id,
            field_name="title",
            value="Local",
        )
        db_session.commit()
        job_id = registry.create(owner_user_id=actor.id)
        strategy = ingestion.IngestionStrategy(
            FileType.STL, True, lambda _path, _report: ({}, None), ()
        )
        engine = db_session.get_bind()
        assert isinstance(engine, Engine)
        ingestion.run_ingestion_pipeline(
            job_id=job_id,
            staged_path=staged,
            original_filename=staged.name,
            model_name="Ignored",
            collection=None,
            tags=None,
            source_hash=None,
            strategy=strategy,
            actor_user_id=actor.id,
            session_factory=SQLiteSessionFactory(engine),
            provenance_context=provenance.ProvenanceContext(
                manifest=manifest("Changed", "r2"),
                source_file_id="42:file",
                source_filename=staged.name,
                actor_id=actor.id,
            ),
        )
        assert db_session.exec(select(File).where(File.model_id == model.id)).all() == [
            file_row
        ]
        assert len(db_session.exec(select(ProvenanceCapture)).all()) == 2
        title = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == link.provenance_source_id
            )
        ).one()
        assert provenance.effective_value(title) == "Local"


class TestThumbnail:
    def test_failed_thumbnail_preserves_artifact_without_derived_pointer(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom(_data: bytes) -> bytes:
            raise ValueError("corrupt image")

        monkeypatch.setattr(thumbnail, "to_webp", _boom)

        file_row = _persist(
            db_session, model, _staged(tmp_path), thumb_bytes=b"not-an-image"
        )

        db_session.refresh(model)
        assert file_row.id is not None
        assert Path(file_row.path).exists()
        assert file_row.thumbnail_path is None
        assert model.thumbnail_file_id is None
        assert (
            db_session.exec(
                select(Metadata).where(Metadata.file_id == file_row.id)
            ).first()
            is not None
        )

    def test_a_thumbnail_collision_never_overwrites_the_occupying_bytes(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        occupied = Path(storage.thumbnail_key(1))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"user-owned thumbnail-shaped file")
        monkeypatch.setattr(storage, "thumbnail_key", lambda _file_id: str(occupied))

        file_row = _persist(
            db_session,
            model,
            _staged(tmp_path),
            thumb_bytes=(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
                b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
                b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
        )

        assert occupied.read_bytes() == b"user-owned thumbnail-shaped file"
        assert Path(file_row.path).exists()
        assert file_row.thumbnail_path is None

    def test_a_new_thumbnail_becomes_the_models_selected_one(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        file_row = _persist(db_session, model, _staged(tmp_path), thumb_bytes=png)

        db_session.refresh(model)
        assert model.thumbnail_file_id == file_row.id
        assert Path(storage.thumbnail_key(file_row.id)).exists()
