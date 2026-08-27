"""The database ownership census is shared by backup and vault maintenance.

It must retain restorable trash, separate user-owned external bytes, and cover
every explicitly referenced primary or embedded vault blob without inferring
delete authority from storage discovery.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.db.models import (
    SENTINEL_FILE_HASH,
    Collection,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
)
from app.services.storage_backend import get_backend
from app.services.storage_utils import all_owned_blob_keys, ownership_snapshot

DELETED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _model(session: Session, slug: str) -> Model:
    row = Model(name=slug, slug=slug, hash=slug.ljust(64, "0")[:64])
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _file(
    session: Session,
    model: Model,
    *,
    key: str,
    sha256: str,
    version: int = 1,
    external: bool = False,
    deleted_at: datetime | None = None,
) -> File:
    row = File(
        model_id=model.id,
        path=key,
        original_filename=key.rsplit("/", 1)[-1],
        file_type=FileType.STL,
        version=version,
        size_bytes=12,
        sha256=sha256,
        is_external=external,
        deleted_at=deleted_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class TestOwnershipSnapshot:
    def test_inventories_live_and_trashed_internal_artifacts_with_integrity_data(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        model = _model(db_session, "internal")
        live = _file(
            db_session,
            model,
            key=backend.blob_key("internal", 1, "live.stl"),
            sha256="a" * 64,
        )
        trashed = _file(
            db_session,
            model,
            key=backend.blob_key("internal", 2, "trashed.stl"),
            sha256="b" * 64,
            version=2,
            deleted_at=DELETED_AT,
        )

        snapshot = ownership_snapshot(db_session, discover=False)

        observed = {
            (blob.key, blob.expected_size, blob.expected_sha256)
            for blob in snapshot.primary
        }
        assert observed == {
            (live.path, 12, "a" * 64),
            (trashed.path, 12, "b" * 64),
        }

    def test_classifies_external_artifacts_without_claiming_their_primary_bytes(
        self, db_session: Session
    ) -> None:
        model = _model(db_session, "external")
        external = _file(
            db_session,
            model,
            key="/mnt/nas/user-owned.stl",
            sha256="c" * 64,
            external=True,
        )

        snapshot = ownership_snapshot(db_session, discover=False)

        assert [blob.key for blob in snapshot.external] == [external.path]
        assert external.path not in snapshot.claimed_keys

    def test_skips_the_database_only_external_job_sentinel(
        self, db_session: Session
    ) -> None:
        model = _model(db_session, "sentinel")
        _file(
            db_session,
            model,
            key="/dev/null",
            sha256=SENTINEL_FILE_HASH,
        )

        snapshot = ownership_snapshot(db_session, discover=False)

        assert snapshot.primary == []
        assert snapshot.derived == []

    def test_inventories_canonical_legacy_and_cache_derivatives(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        model = _model(db_session, "derivatives")
        file_row = _file(
            db_session,
            model,
            key=backend.blob_key("derivatives", 1, "part.stl"),
            sha256="d" * 64,
        )

        snapshot = ownership_snapshot(db_session, discover=False)

        assert {blob.key for blob in snapshot.derived} == {
            backend.thumbnail_key(file_row.id),
            backend.legacy_thumbnail_key(file_row.id),
            backend.stl_cache_key(file_row.sha256),
        }

    def test_inventories_binary_and_embedded_document_blobs(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        binary = Document(
            name="Manual",
            kind=DocumentKind.PDF,
            filename="manual.pdf",
            size_bytes=100,
            sha256="e" * 64,
        )
        markdown = Document(name="Notes", kind=DocumentKind.MARKDOWN)
        db_session.add(binary)
        db_session.add(markdown)
        db_session.commit()
        db_session.refresh(binary)
        db_session.refresh(markdown)
        image_name = f"{'f' * 64}.png"
        markdown.body = (
            f"![diagram](/api/v1/documents/{markdown.id}/images/{image_name})"
        )
        db_session.add(markdown)
        db_session.commit()

        snapshot = ownership_snapshot(db_session, discover=False)

        assert {blob.key for blob in snapshot.primary} == {
            backend.document_file_key(binary.id, "manual.pdf")
        }
        assert {blob.key for blob in snapshot.embedded} == {
            backend.document_image_key(markdown.id, image_name)
        }

    def test_inventories_only_collection_image_references_owned_by_that_row(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        collection = Collection(name="Docs", slug="docs", path="docs")
        db_session.add(collection)
        db_session.commit()
        db_session.refresh(collection)
        owned_name = f"{'a' * 64}.png"
        foreign_name = f"{'b' * 64}.png"
        collection.readme = (
            f"![owned](/api/v1/collections/{collection.id}/images/{owned_name}) "
            f"![foreign](/api/v1/collections/{collection.id + 1}/images/{foreign_name})"
        )
        db_session.add(collection)
        db_session.commit()

        snapshot = ownership_snapshot(db_session, discover=False)

        assert {blob.key for blob in snapshot.embedded} == {
            backend.collection_image_key(collection.id, owned_name)
        }

    def test_discovers_unclaimed_data_and_thumbnail_keys_read_only(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        data_key = backend.blob_key("unclaimed", 1, "part.stl")
        thumbnail_key = backend.thumbnail_key(999)
        backend.create_bytes(b"data", data_key)
        backend.create_bytes(b"thumb", thumbnail_key)

        snapshot = ownership_snapshot(db_session)

        assert data_key in snapshot.discovered_keys
        assert thumbnail_key in snapshot.discovered_keys
        assert snapshot.claimed_keys == set()

    def test_skips_storage_discovery_when_database_only_is_requested(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        unclaimed = backend.blob_key("unclaimed", 1, "part.stl")
        backend.create_bytes(b"data", unclaimed)

        snapshot = ownership_snapshot(db_session, discover=False)

        assert snapshot.discovered_keys == set()
        assert unclaimed not in snapshot.claimed_keys


class TestAllOwnedBlobKeys:
    def test_includes_internal_external_trashed_derived_and_embedded_keys(
        self, db_session: Session
    ) -> None:
        backend = get_backend()
        model = _model(db_session, "all-owned")
        internal = _file(
            db_session,
            model,
            key=backend.blob_key("all-owned", 1, "part.stl"),
            sha256="1" * 64,
            deleted_at=DELETED_AT,
        )
        external = _file(
            db_session,
            model,
            key="/mnt/nas/part.stl",
            sha256="2" * 64,
            version=2,
            external=True,
        )

        keys = all_owned_blob_keys(db_session)

        assert keys == {
            internal.path,
            external.path,
            backend.thumbnail_key(internal.id),
            backend.legacy_thumbnail_key(internal.id),
            backend.stl_cache_key(internal.sha256),
            backend.thumbnail_key(external.id),
            backend.legacy_thumbnail_key(external.id),
            backend.stl_cache_key(external.sha256),
        }
