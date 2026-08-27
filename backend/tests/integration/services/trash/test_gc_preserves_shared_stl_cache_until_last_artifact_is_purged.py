"""Defends gc preserves shared stl cache until last artifact is purged at the services trash integration boundary.

A regression could permanently delete bytes that are still owned or shared.
"""

from __future__ import annotations

from ._trash_shared import (
    Collection,
    File,
    Path,
    Session,
    _cleanup_orphan_blobs,
    _model_with_file,
    _owned_write,
    _write,
    gc_soft_deleted,
    timedelta,
    utcnow,
)


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
    cache_key = _owned_write(db_session, storage, storage.stl_cache_key(expired.sha256))

    gc_soft_deleted(retention_days=0)

    assert Path(cache_key).exists()
    db_session.expire_all()
    assert db_session.get(File, survivor.id) is not None


def test_gc_preserves_unreferenced_collection_images(
    db_session: Session, storage
) -> None:
    collection = Collection(name="Docs", slug="docs", path="docs")
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    unreferenced = _write(storage.collection_image_key(collection.id, "gone.png"))

    removed = _cleanup_orphan_blobs(db_session)

    assert removed == 0
    assert Path(unreferenced).exists()


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
    key = _owned_write(
        db_session, storage, storage.collection_image_key(collection.id, name)
    )

    _cleanup_orphan_blobs(db_session)

    assert Path(key).exists()


def test_gc_does_not_infer_ownership_from_expired_collection_namespace(
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

    assert Path(key).exists()
    db_session.expire_all()
    assert db_session.get(Collection, collection_id) is None


def test_gc_hard_deletes_expired_collection_referenced_image(
    db_session: Session, storage
) -> None:
    name = "a" * 64 + ".png"
    collection = Collection(
        name="Old docs",
        slug="old-docs",
        path="old-docs",
        deleted_at=utcnow() - timedelta(days=1),
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    collection.readme = f"![diagram](/api/v1/collections/{collection.id}/images/{name})"
    db_session.add(collection)
    db_session.commit()
    key = _owned_write(
        db_session, storage, storage.collection_image_key(collection.id, name)
    )

    gc_soft_deleted(retention_days=0)

    assert not Path(key).exists()
