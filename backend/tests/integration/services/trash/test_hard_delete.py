"""What a purge has to clean up *before* the row it is deleting disappears.

A purge is irreversible and it is not one statement: rows in other tables point
at the thing being deleted, and each pointer has to be dealt with by hand or the
purge fails at commit time — or, worse, succeeds and leaves a pointer to an id
that no longer exists. Two of those pointers are user-visible. A model's
thumbnail points at one artifact; purge that artifact and the model must stop
claiming to have a thumbnail, or every list view asks for bytes that are gone.
And exactly one G-code revision per model is the recommended one; purge that
revision and the recommendation has to move, because a model with G-code and no
recommendation sends the next print to the wrong revision.

The `id is None` guards are the other half of this file. `hard_delete_*` is
called from a GC loop and from the API, and a row that was never persisted has
no dependents, no blobs and no claim to take — returning quietly is right, while
falling through would issue a `DELETE ... WHERE id IS NULL`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Document,
    DocumentKind,
    FileType,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    Model,
)
from app.services import trash
from tests.factories import (
    build_model,
    build_stored_file,
    build_user,
    detached_collection,
    detached_file,
    detached_model,
)


class TestHardDeleteFile:
    def test_clears_the_thumbnail_pointer_of_the_model_it_illustrated(
        self, db_session: Session, storage
    ) -> None:
        model = build_model(db_session, "illustrated")
        artifact = build_stored_file(
            db_session, storage, model, filename="illustrated.stl"
        )
        model.thumbnail_file_id = artifact.id
        model.thumbnail_path = "thumbs/illustrated.png"
        db_session.add(model)
        db_session.commit()

        trash.hard_delete_file(db_session, artifact)
        db_session.commit()

        db_session.refresh(model)
        # Otherwise every list view asks for bytes the purge just removed.
        assert (model.thumbnail_file_id, model.thumbnail_path) == (None, None)

    def test_leaves_another_models_thumbnail_pointer_alone(
        self, db_session: Session, storage
    ) -> None:
        subject = build_model(db_session, "subject")
        bystander = build_model(db_session, "bystander")
        artifact = build_stored_file(
            db_session, storage, subject, filename="subject.stl"
        )
        keeper = build_stored_file(
            db_session, storage, bystander, filename="bystander.stl"
        )
        bystander.thumbnail_file_id = keeper.id
        db_session.add(bystander)
        db_session.commit()

        trash.hard_delete_file(db_session, artifact)
        db_session.commit()

        db_session.refresh(bystander)
        assert bystander.thumbnail_file_id == keeper.id

    def test_moves_the_recommendation_to_the_newest_surviving_revision(
        self, db_session: Session, storage
    ) -> None:
        model = build_model(db_session, "revised")
        build_stored_file(
            db_session, storage, model, filename="v1.gcode", file_type=FileType.GCODE
        )
        second = build_stored_file(
            db_session, storage, model, filename="v2.gcode", file_type=FileType.GCODE
        )
        recommended = build_stored_file(
            db_session,
            storage,
            model,
            filename="v3.gcode",
            file_type=FileType.GCODE,
            recommended=True,
        )

        trash.hard_delete_file(db_session, recommended)
        db_session.commit()

        db_session.refresh(second)
        # Newest, not oldest: a model with G-code and no recommendation sends
        # the next print to whichever revision the UI happens to list first.
        assert second.is_recommended is True

    def test_leaves_nothing_recommended_when_no_revision_survives(
        self, db_session: Session, storage
    ) -> None:
        model = build_model(db_session, "only-revision")
        recommended = build_stored_file(
            db_session,
            storage,
            model,
            filename="only.gcode",
            file_type=FileType.GCODE,
            recommended=True,
        )
        mesh = build_stored_file(db_session, storage, model, filename="only.stl")

        trash.hard_delete_file(db_session, recommended)
        db_session.commit()

        db_session.refresh(mesh)
        # A mesh is not a print target; it must never inherit the recommendation.
        assert mesh.is_recommended is False

    def test_leaves_a_trashed_revision_out_of_the_running(
        self, db_session: Session, storage
    ) -> None:
        model = build_model(db_session, "trashed-candidate")
        trashed_revision = build_stored_file(
            db_session, storage, model, filename="old.gcode", file_type=FileType.GCODE
        )
        trashed_revision.deleted_at = utcnow()
        db_session.add(trashed_revision)
        db_session.commit()
        recommended = build_stored_file(
            db_session,
            storage,
            model,
            filename="new.gcode",
            file_type=FileType.GCODE,
            recommended=True,
        )

        trash.hard_delete_file(db_session, recommended)
        db_session.commit()

        db_session.refresh(trashed_revision)
        # Promoting a trashed revision would make it recommended *and* invisible.
        assert trashed_revision.is_recommended is False

    def test_does_nothing_for_an_artifact_that_was_never_persisted(
        self, db_session: Session
    ) -> None:
        unsaved = detached_file(
            path="never/written.stl",
            original_filename="written.stl",
            sha256="sha-unsaved",
        )

        trash.hard_delete_file(db_session, unsaved)


class TestHardDeleteDocument:
    def test_does_nothing_for_a_document_that_was_never_persisted(
        self, db_session: Session
    ) -> None:
        trash.hard_delete_document(
            db_session, Document(name="unsaved", kind=DocumentKind.MARKDOWN)
        )


class TestRestoreDocument:
    def test_restores_a_trashed_document(self, db_session: Session) -> None:
        document = Document(
            name="notes", kind=DocumentKind.MARKDOWN, deleted_at=utcnow()
        )
        db_session.add(document)
        db_session.commit()
        db_session.refresh(document)

        trash.restore_document(db_session, document)
        db_session.commit()

        db_session.refresh(document)
        assert (document.deleted_at, document.deleted_by) == (None, None)

    def test_refuses_while_a_purge_holds_the_claim(self, db_session: Session) -> None:
        document = Document(
            name="doomed", kind=DocumentKind.MARKDOWN, deleted_at=utcnow()
        )
        db_session.add(document)
        db_session.commit()
        db_session.refresh(document)
        trash._claim_purge(db_session, document)

        # The claim means a purge is mid-flight: its bytes may already be gone,
        # so restoring would produce a document that cannot be opened.
        with pytest.raises(trash.PurgeConflictError, match="storage_cleanup_blocked"):
            trash.restore_document(db_session, document)


class TestHardDeleteCollection:
    def test_does_nothing_for_a_collection_that_was_never_persisted(
        self, db_session: Session
    ) -> None:
        trash.hard_delete_collection(
            db_session, detached_collection(name="unsaved", slug="unsaved")
        )


class TestHardDeleteModel:
    def test_detaches_the_pending_import_that_produced_the_model(
        self, db_session: Session, storage
    ) -> None:
        model = build_model(db_session, "imported")
        owner = build_user(db_session, "importer")
        db_session.add(owner)
        db_session.commit()
        db_session.refresh(owner)
        item = InboxItem(
            owner_user_id=owner.id,
            source_kind=InboxSourceKind.URL,
            state=InboxItemState.COMPLETED,
            resulting_model_id=model.id,
        )
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)

        trash.hard_delete_model(db_session, model)
        db_session.commit()

        db_session.refresh(item)
        # The import history outlives the model, so the pointer is cleared
        # rather than the row being deleted with it.
        assert item.resulting_model_id is None
        assert db_session.get(InboxItem, item.id) is not None

    def test_does_nothing_for_a_model_that_was_never_persisted(
        self, db_session: Session
    ) -> None:
        trash.hard_delete_model(
            db_session,
            detached_model(name="unsaved", slug="unsaved", hash="hash-unsaved"),
        )


class TestHardDeleteExpiredModels:
    def test_purges_a_model_past_the_retention_window(
        self, db_session: Session, storage
    ) -> None:
        model = build_model(db_session, "expired")
        model.deleted_at = utcnow() - timedelta(days=2)
        db_session.add(model)
        db_session.commit()

        purged = trash.hard_delete_expired_models(db_session, 1)

        assert purged == [model.id]

    def test_purges_nothing_when_retention_is_disabled(
        self, db_session: Session
    ) -> None:
        model = build_model(db_session, "kept-forever")
        model.deleted_at = utcnow() - timedelta(days=365)
        db_session.add(model)
        db_session.commit()

        # A negative retention is the operator saying "never empty the trash".
        assert trash.hard_delete_expired_models(db_session, -1) == []
        assert db_session.get(Model, model.id) is not None
