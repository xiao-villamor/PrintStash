"""Orphan-blob GC must delete only blobs no live DB row claims.

Regression pack for the census bug: the sweep used to compare every key under
``data_dir`` against ``File.path`` alone, so a Document's PDF looked like an
orphan and was deleted on the next hourly cycle.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.core.time import utcnow
from app.db.models import Collection, Document, DocumentKind, File, FileType, Model
from app.services.storage_backend import get_backend
from app.services.trash import _cleanup_orphan_blobs, gc_soft_deleted


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


def _write(key: str, data: bytes = b"x") -> str:
    p = Path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return key


def _model_with_file(session: Session, storage, slug: str) -> File:
    model = Model(name=slug, slug=slug, hash=f"hash-{slug}")
    session.add(model)
    session.commit()
    session.refresh(model)
    key = _write(storage.blob_key(slug, 1, f"{slug}.stl"))
    f = File(
        model_id=model.id,
        path=key,
        original_filename=f"{slug}.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=1,
        sha256=f"sha-{slug}",
    )
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


def _binary_document(session: Session, storage, name: str = "manual.pdf") -> Document:
    doc = Document(name=name, kind=DocumentKind.PDF)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    doc.filename = name
    doc.size_bytes = 1
    session.add(doc)
    session.commit()
    _write(storage.document_file_key(doc.id, name))
    return doc


def test_gc_preserves_document_blobs(db_session: Session, storage) -> None:
    doc = _binary_document(db_session, storage)
    key = storage.document_file_key(doc.id, doc.filename)

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists(), "GC deleted a live document's blob"


def test_gc_preserves_model_blobs(db_session: Session, storage) -> None:
    f = _model_with_file(db_session, storage, "widget")

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()


def test_gc_preserves_file_derivatives_while_artifact_exists(
    db_session: Session, storage
) -> None:
    artifact = _model_with_file(db_session, storage, "derived-live")
    keys = (
        storage.thumbnail_key(artifact.id),
        storage.legacy_thumbnail_key(artifact.id),
        storage.stl_cache_key(artifact.sha256),
    )
    for key in keys:
        _write(key)

    _cleanup_orphan_blobs(db_session)

    assert all(Path(key).exists() for key in keys)


def test_gc_preserves_trashed_model_blobs(db_session: Session, storage) -> None:
    """A trashed model's bytes must survive until hard delete — restore needs them."""
    f = _model_with_file(db_session, storage, "trashed")
    f.deleted_at = utcnow()
    db_session.add(f)
    db_session.commit()

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()


def test_gc_preserves_document_blobs_when_models_exist(
    db_session: Session, storage
) -> None:
    """The two censuses must union, not shadow each other."""
    f = _model_with_file(db_session, storage, "widget")
    doc = _binary_document(db_session, storage)

    _cleanup_orphan_blobs(db_session)

    assert Path(f.path).exists()
    assert Path(storage.document_file_key(doc.id, doc.filename)).exists()


def test_gc_deletes_actual_orphans(db_session: Session, storage) -> None:
    orphan_blob = _write(storage.blob_key("gone", 1, "gone.stl"))
    orphan_doc = _write(storage.document_file_key(999, "gone.pdf"))

    removed = _cleanup_orphan_blobs(db_session)

    assert not Path(orphan_blob).exists()
    assert not Path(orphan_doc).exists()
    assert removed == 2


def test_gc_deletes_blob_of_hard_deleted_document(db_session: Session, storage) -> None:
    doc = _binary_document(db_session, storage)
    key = storage.document_file_key(doc.id, doc.filename)
    db_session.delete(doc)
    db_session.commit()

    _cleanup_orphan_blobs(db_session)

    assert not Path(key).exists()


def test_gc_ignores_markdown_documents(db_session: Session, storage) -> None:
    """Markdown docs own no blob — they must not contribute a bogus key."""
    doc = Document(name="notes", kind=DocumentKind.MARKDOWN, body="# hi")
    db_session.add(doc)
    db_session.commit()

    assert _cleanup_orphan_blobs(db_session) == 0


def test_gc_hard_deletes_expired_document_and_its_blob(
    db_session: Session, storage
) -> None:
    doc = _binary_document(db_session, storage)
    document_id = doc.id
    key = storage.document_file_key(doc.id, doc.filename)
    doc.deleted_at = utcnow()
    db_session.add(doc)
    db_session.commit()

    result = gc_soft_deleted(retention_days=0)

    assert result["rows"] >= 1
    assert not Path(key).exists()
    db_session.expire_all()
    assert db_session.get(Document, document_id) is None


def test_gc_hard_deletes_expired_artifact_and_its_derivatives(
    db_session: Session, storage
) -> None:
    artifact = _model_with_file(db_session, storage, "derived-expired")
    artifact_id = artifact.id
    keys = (
        artifact.path,
        storage.thumbnail_key(artifact.id),
        storage.legacy_thumbnail_key(artifact.id),
        storage.stl_cache_key(artifact.sha256),
    )
    for key in keys[1:]:
        _write(key)
    artifact.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(artifact)
    db_session.commit()

    gc_soft_deleted(retention_days=0)

    assert all(not Path(key).exists() for key in keys)
    db_session.expire_all()
    assert db_session.get(File, artifact_id) is None


def test_gc_preserves_shared_stl_cache_until_last_artifact_is_purged(
    db_session: Session, storage
) -> None:
    expired = _model_with_file(db_session, storage, "cache-expired")
    survivor = _model_with_file(db_session, storage, "cache-survivor")
    survivor.sha256 = expired.sha256
    expired.deleted_at = utcnow() - timedelta(days=1)
    db_session.add(expired)
    db_session.add(survivor)
    db_session.commit()
    cache_key = _write(storage.stl_cache_key(expired.sha256))

    gc_soft_deleted(retention_days=0)

    assert Path(cache_key).exists()
    db_session.expire_all()
    assert db_session.get(File, survivor.id) is not None


def test_gc_sweeps_unreferenced_collection_images(db_session: Session, storage) -> None:
    collection = Collection(name="Docs", slug="docs", path="docs")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    unreferenced = _write(storage.collection_image_key(collection.id, "gone.png"))

    removed = _cleanup_orphan_blobs(db_session)

    assert removed == 1
    assert not Path(unreferenced).exists()


def test_gc_preserves_referenced_collection_images(
    db_session: Session, storage
) -> None:
    collection = Collection(name="Docs", slug="docs", path="docs")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    name = "a" * 64 + ".png"
    collection.readme = f"![diagram](/api/v1/collections/{collection.id}/images/{name})"
    db_session.add(collection)
    db_session.commit()
    key = _write(storage.collection_image_key(collection.id, name))

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists()


def test_gc_hard_deletes_expired_collection_image_namespace(
    db_session: Session, storage
) -> None:
    collection = Collection(
        name="Old docs",
        slug="old-docs",
        path="old-docs",
        deleted_at=utcnow() - timedelta(days=1),
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    collection_id = collection.id
    key = _write(storage.collection_image_key(collection.id, "unlinked.png"))

    gc_soft_deleted(retention_days=0)

    assert not Path(key).exists()
    db_session.expire_all()
    assert db_session.get(Collection, collection_id) is None
