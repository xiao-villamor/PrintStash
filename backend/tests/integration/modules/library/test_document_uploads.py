"""Durable upload intents reuse completed Documents without touching original bytes."""

import io
import json
from dataclasses import dataclass

import pytest
from sqlmodel import Session, select

from app.core.errors import OperationError
from app.db.models import Document, DocumentKind, IngestRequest
from app.modules.library.document_uploads import publish
from app.modules.storage.storage_backend.runtime import get_backend


@dataclass
class StoredIntent:
    job_id: str
    document_id: int | None

    def record(self, session: Session, document_id: int) -> None:
        request = session.get(IngestRequest, self.job_id)
        request.manifest_json = json.dumps({"document_id": document_id})
        session.add(request)


@pytest.fixture
def upload_case(make_user, make_ingest_request):
    user = make_user(superuser=True)
    request = make_ingest_request(user)
    return user, request.job_id


def recovered(session, job_id):
    session.expire_all()
    request = session.get(IngestRequest, job_id)
    payload = json.loads(request.manifest_json or "{}")
    return StoredIntent(job_id, payload.get("document_id"))


class TestPublish:
    @pytest.mark.parametrize(
        "filename", ["guide.pdf", "photo.png", "README.md"], ids=str
    )
    def test_reuses_the_completed_document(self, db_session, upload_case, filename):
        user, job_id = upload_case
        original = b"# Assembly\n"
        first = publish(
            db_session,
            user,
            stream=io.BytesIO(original),
            filename=filename,
            size=len(original),
            name="Guide",
            collection_id=None,
            intent=StoredIntent(job_id, None),
        )
        again = publish(
            db_session,
            user,
            stream=io.BytesIO(b"replacement"),
            filename=filename,
            size=11,
            name="Guide",
            collection_id=None,
            intent=recovered(db_session, job_id),
        )
        assert again.id == first.id
        if first.kind is DocumentKind.MARKDOWN:
            assert again.body.encode() == original
        else:
            backend = get_backend()
            assert (
                backend.read_bytes(backend.document_file_key(again.id, again.filename))
                == original
            )
        assert len(db_session.exec(select(Document)).all()) == 1

    def test_replaces_an_interrupted_intent_without_reusing_its_id(
        self, db_session, upload_case, make_document
    ):
        user, job_id = upload_case
        pending = make_document(
            kind=DocumentKind.PDF,
            filename=None,
            size_bytes=None,
            sha256=None,
            created_by=user.id,
        )
        old_id = pending.id
        backend = get_backend()
        old_key = backend.document_file_key(old_id, "guide.pdf")
        # Bytes without adoption are not proof of ownership for this new attempt.
        backend.write_bytes(b"unowned interrupted bytes", old_key)
        doc = publish(
            db_session,
            user,
            stream=io.BytesIO(b"new"),
            filename="guide.pdf",
            size=3,
            name="Guide",
            collection_id=None,
            intent=StoredIntent(job_id, old_id),
        )
        assert doc.id != old_id
        assert backend.read_bytes(old_key) == b"unowned interrupted bytes"
        assert recovered(db_session, job_id).document_id == doc.id
        assert db_session.get(Document, old_id) is None

    def test_does_not_restore_a_trashed_result(
        self, db_session, upload_case, make_document
    ):
        user, job_id = upload_case
        doc = make_document(trashed=True, created_by=user.id)
        with pytest.raises(OperationError, match="document_import_result_missing"):
            publish(
                db_session,
                user,
                stream=io.BytesIO(b"new"),
                filename="guide.md",
                size=3,
                name="Guide",
                collection_id=None,
                intent=StoredIntent(job_id, doc.id),
            )
        db_session.refresh(doc)
        assert doc.deleted_at is not None

    def test_refuses_an_intent_owned_by_another_user(
        self, db_session, upload_case, make_document, make_user
    ):
        user, job_id = upload_case
        doc = make_document(created_by=make_user().id)
        with pytest.raises(OperationError, match="document_import_intent_mismatch"):
            publish(
                db_session,
                user,
                stream=io.BytesIO(b"new"),
                filename="guide.md",
                size=3,
                name="Guide",
                collection_id=None,
                intent=StoredIntent(job_id, doc.id),
            )
        db_session.refresh(doc)
        assert doc.body == "# Manual\n"

    def test_rolls_back_publication_when_the_intent_loses_authority(
        self, db_session, upload_case, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        user, job_id = upload_case
        record = StoredIntent.record
        commits = 0

        def fence(intent, session, document_id):
            nonlocal commits
            commits += 1
            if commits == 2:
                raise OperationCancelled()
            record(intent, session, document_id)

        monkeypatch.setattr(StoredIntent, "record", fence)
        with pytest.raises(OperationCancelled):
            publish(
                db_session,
                user,
                stream=io.BytesIO(b"pdf"),
                filename="guide.pdf",
                size=3,
                name="Guide",
                collection_id=None,
                intent=StoredIntent(job_id, None),
            )
        receipt = recovered(db_session, job_id)
        pending = db_session.get(Document, receipt.document_id)
        assert pending.filename is None
        backend = get_backend()
        assert not backend.exists(backend.document_file_key(pending.id, "guide.pdf"))

    def test_refuses_a_missing_previously_recorded_document(
        self, db_session, upload_case, make_document
    ):
        user, job_id = upload_case
        doc = make_document(created_by=user.id)
        identifier = doc.id
        db_session.delete(doc)
        db_session.commit()
        with pytest.raises(OperationError, match="document_import_result_missing"):
            publish(
                db_session,
                user,
                stream=io.BytesIO(b"new"),
                filename="guide.md",
                size=3,
                name="Guide",
                collection_id=None,
                intent=StoredIntent(job_id, identifier),
            )
        assert db_session.exec(select(Document)).all() == []

    @pytest.mark.parametrize(
        "name", ["", "  ", "a" * 256], ids=["empty", "blank", "too-long"]
    )
    def test_refuses_an_invalid_display_name(self, db_session, upload_case, name):
        user, job_id = upload_case
        with pytest.raises(OperationError, match="document_name_invalid"):
            publish(
                db_session,
                user,
                stream=io.BytesIO(b"new"),
                filename="guide.md",
                size=3,
                name=name,
                collection_id=None,
                intent=StoredIntent(job_id, None),
            )
        assert db_session.exec(select(Document)).all() == []

    def test_withdraws_an_intent_rejected_before_publication(
        self, db_session, upload_case, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        user, job_id = upload_case

        def refuse(*args):
            raise OperationCancelled()

        monkeypatch.setattr(StoredIntent, "record", refuse)
        with pytest.raises(OperationCancelled):
            publish(
                db_session,
                user,
                stream=io.BytesIO(b"pdf"),
                filename="guide.pdf",
                size=3,
                name="Guide",
                collection_id=None,
                intent=StoredIntent(job_id, None),
            )
        db_session.commit()
        assert db_session.exec(select(Document)).all() == []
        assert recovered(db_session, job_id).document_id is None
