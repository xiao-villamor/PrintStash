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
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import _overlay
from app.db.models import File, FileType, Metadata, Model
from app.db.session import _set_sqlite_pragmas
from app.services import ingestion, thumbnail
from app.services.storage_backend import get_backend


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
    model = Model(name="Bracket", slug="bracket", hash="h" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
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


def test_persists_file_and_metadata_together(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    file_row = _persist(db_session, model, _staged(tmp_path))

    assert file_row.id is not None
    md = db_session.exec(
        select(Metadata).where(Metadata.file_id == file_row.id)
    ).first()
    assert md is not None and md.estimated_time_s == 120


def test_failed_thumbnail_does_not_leave_partial_model(
    db_session: Session,
    storage,
    model: Model,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_data: bytes) -> bytes:
        raise ValueError("corrupt image")

    monkeypatch.setattr(thumbnail, "to_webp", _boom)

    with pytest.raises(ValueError, match="corrupt image"):
        _persist(db_session, model, _staged(tmp_path), thumb_bytes=b"not-an-image")

    db_session.rollback()
    assert db_session.exec(select(File).where(File.model_id == model.id)).all() == []
    assert db_session.exec(select(Metadata)).all() == []


def test_failed_metadata_does_not_leave_orphan_file_row(
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
    assert db_session.exec(select(File).where(File.model_id == model.id)).all() == []


def test_version_numbers_increment_across_revisions(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    first = _persist(db_session, model, _staged(tmp_path, "v1.stl"))
    second = _persist(db_session, model, _staged(tmp_path, "v2.stl"))

    assert (first.version, second.version) == (1, 2)


def test_concurrent_version_reservations_are_unique(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'versions.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        concurrent_model = Model(name="Concurrent", slug="concurrent", hash="c" * 64)
        session.add(concurrent_model)
        session.commit()
        session.refresh(concurrent_model)
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


def test_concurrent_artifacts_keep_distinct_versions_and_matching_hashes(
    tmp_path: Path, storage
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'artifacts.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        concurrent_model = Model(name="Race", slug="race", hash="a" * 64)
        session.add(concurrent_model)
        session.commit()
        session.refresh(concurrent_model)
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
            assert hashlib.sha256(Path(row.path).read_bytes()).hexdigest() == row.sha256
    finally:
        engine.dispose()


def test_thumbnail_is_written_and_selected(
    db_session: Session, storage, model: Model, tmp_path: Path
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


def test_concurrent_same_hash_upload_dedups_instead_of_crashing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
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
            other.add(Model(name="Winner", slug="winner", hash=dedup_hash))
            other.commit()
        return real_ensure(base_slug, exists)

    monkeypatch.setattr(ingestion.storage, "ensure_unique_slug", _insert_the_winner)

    model, created = ingestion.resolve_or_create_model(
        db_session, dedup_hash=dedup_hash, model_name="Loser"
    )

    assert created is False
    assert model.name == "Winner"
    assert (
        len(db_session.exec(select(Model).where(Model.hash == dedup_hash)).all()) == 1
    )
