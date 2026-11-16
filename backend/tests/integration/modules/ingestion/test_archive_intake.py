"""Archive grouping retains committed parts and guides across retryable failures."""

import hashlib
import json

import pytest
from sqlmodel import select

from app.core.config import settings
from app.db.models import (
    Document,
    File,
    IngestRequestKind,
    Job,
    MultipartModel,
    MultipartPart,
)
from app.db.session import get_session_factory
from app.modules.ingestion import background, importer, staging_leases
from app.modules.ingestion.archive_plan import MultipartIntake
from app.modules.library import document_uploads
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import service
from tests.factories.content import binary_stl, png, zip_bytes
from tests.factories.ops import build_job_context


@pytest.fixture
def intake_case(db_session, make_user, make_ingest_request):
    user = make_user(superuser=True)
    bodies = {
        "base.stl": binary_stl(triangles=4),
        "lid.stl": binary_stl(triangles=8),
        "assembly.pdf": b"%PDF-1.4\nInstructions",
        "README.md": b"# Assemble\n",
        "photo.png": png(),
    }
    plan = MultipartIntake(
        mode="multipart",
        name="Reviewed assembly",
        parts=[
            {"entry_name": "base.stl", "name": "Base"},
            {"entry_name": "lid.stl", "name": "Lid"},
        ],
        documents=["assembly.pdf", "README.md"],
    )
    request = make_ingest_request(
        user,
        kind=IngestRequestKind.ARCHIVE_SELECTION,
        selection_json=json.dumps(
            {
                "names": ["base.stl", "lid.stl"],
                "archive_name": "bundle.zip",
                "intake": plan.model_dump(),
                "_entry_identity_version": 1,
            }
        ),
    )
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    source = settings.incoming_dir / "bundle.zip"
    original = zip_bytes(bodies)
    source.write_bytes(original)
    staging_leases.create_job_lease(
        db_session,
        job_id=request.job_id,
        owner_user_id=user.id,
        path=source,
        size_bytes=len(original),
        sha256=hashlib.sha256(original).hexdigest(),
    )
    db_session.commit()

    def run(*, cover=None):
        background.run_archive_selection(
            job_context=build_job_context(request.job_id),
            archive=source,
            archive_name="bundle.zip",
            names=["base.stl", "lid.stl"],
            collection=None,
            tags=None,
            source_url=None,
            actor_user_id=user.id,
            session_factory=get_session_factory(),
            intake=plan.model_copy(update={"cover": cover}),
        )
        db_session.expire_all()
        return json.loads(db_session.get(Job, request.job_id).status_json)

    return user, request.job_id, source, original, bodies, run


class TestArchiveIntake:
    def test_creates_a_multipart_with_guides(self, db_session, intake_case):
        _, _, source, original, bodies, run = intake_case
        status = run()
        assert status["failed"] == 0, status
        aggregate = db_session.get(
            MultipartModel, status["result"]["multipart_model_id"]
        )
        assert aggregate.name == "Reviewed assembly"
        parts = db_session.exec(
            select(MultipartPart).where(
                MultipartPart.multipart_model_id == aggregate.id
            )
        ).all()
        assert {part.name for part in parts} == {"Base", "Lid"}
        docs = db_session.exec(
            select(Document).where(Document.multipart_model_id == aggregate.id)
        ).all()
        assert {doc.name for doc in docs} == {"assembly.pdf", "README.md"}
        backend = get_backend()
        for doc in docs:
            if doc.filename:
                assert (
                    backend.read_bytes(backend.document_file_key(doc.id, doc.filename))
                    == bodies[doc.name]
                )
            else:
                assert doc.body.encode() == bodies[doc.name]
        assert source.read_bytes() == original

    def test_defers_grouping_after_a_failed_part(
        self, db_session, intake_case, monkeypatch
    ):
        _, _, source, original, _, run = intake_case
        commit = importer._ingest_one_file

        def reject_lid(staged, original_filename, *args, **kwargs):
            if original_filename == "lid.stl":
                raise importer.ImportError_("source_unavailable")
            return commit(staged, original_filename, *args, **kwargs)

        monkeypatch.setattr(importer, "_ingest_one_file", reject_lid)
        result = run()
        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert "multipart_model_id" not in result["result"]
        assert db_session.exec(select(MultipartModel)).all() == []
        assert source.read_bytes() == original

    def test_retries_guides_without_duplicate_results(
        self, db_session, intake_case, monkeypatch
    ):
        user, job_id, _, _, _, run = intake_case
        publish = document_uploads.publish

        def reject_readme(*args, **kwargs):
            if kwargs["filename"] == "README.md":
                raise importer.ImportError_("source_unavailable")
            return publish(*args, **kwargs)

        monkeypatch.setattr(document_uploads, "publish", reject_readme)
        first = run()
        assert first["failed"] == 1, first
        files_before = {file.id for file in db_session.exec(select(File)).all()}
        guide = db_session.exec(
            select(Document).where(Document.name == "assembly.pdf")
        ).one()
        guide_id = guide.id
        assert db_session.exec(select(MultipartModel)).all() == []
        monkeypatch.setattr(document_uploads, "publish", publish)
        service.retry(job_id, actor=user)
        second = run()
        assert second["failed"] == 0, second.get("failed_items")
        assert {file.id for file in db_session.exec(select(File)).all()} == files_before
        assert (
            db_session.get(Document, guide_id).multipart_model_id
            == second["result"]["multipart_model_id"]
        )
        assert len(db_session.exec(select(Document)).all()) == 2
        assert len(db_session.exec(select(MultipartModel)).all()) == 1

    def test_preserves_a_guide_assigned_elsewhere_before_retry(
        self, db_session, intake_case, monkeypatch, make_multipart_model
    ):
        user, job_id, _, _, _, run = intake_case
        publish = document_uploads.publish

        def reject_readme(*args, **kwargs):
            if kwargs["filename"] == "README.md":
                raise importer.ImportError_("source_unavailable")
            return publish(*args, **kwargs)

        monkeypatch.setattr(document_uploads, "publish", reject_readme)
        assert run()["failed"] == 1
        guide = db_session.exec(
            select(Document).where(Document.name == "assembly.pdf")
        ).one()
        other = make_multipart_model(
            collection_id=guide.collection_id, created_by=user.id
        )
        guide.multipart_model_id = other.id
        db_session.add(guide)
        db_session.commit()
        monkeypatch.setattr(document_uploads, "publish", publish)
        service.retry(job_id, actor=user)
        result = run()
        assert result["failed"] == 1
        assert "multipart_model_id" not in result["result"]
        assert db_session.get(Document, guide.id).multipart_model_id == other.id
        assert len(db_session.exec(select(MultipartModel)).all()) == 1

    def test_does_not_restore_a_trashed_guide_on_retry(
        self, db_session, intake_case, monkeypatch
    ):
        from app.core.time import utcnow

        user, job_id, _, _, _, run = intake_case
        publish = document_uploads.publish

        def reject_readme(*args, **kwargs):
            if kwargs["filename"] == "README.md":
                raise importer.ImportError_("source_unavailable")
            return publish(*args, **kwargs)

        monkeypatch.setattr(document_uploads, "publish", reject_readme)
        assert run()["failed"] == 1
        guide = db_session.exec(
            select(Document).where(Document.name == "assembly.pdf")
        ).one()
        guide.deleted_at = utcnow()
        db_session.add(guide)
        db_session.commit()
        monkeypatch.setattr(document_uploads, "publish", publish)
        service.retry(job_id, actor=user)
        result = run()
        assert result["failed"] == 1
        assert db_session.get(Document, guide.id).deleted_at is not None
        assert len(db_session.exec(select(Document)).all()) == 2
        assert db_session.exec(select(MultipartModel)).all() == []

    def test_normalizes_the_explicit_cover(self, db_session, intake_case):
        _, _, source, original, _, run = intake_case
        result = run(cover="photo.png")
        assert result["failed"] == 0, result.get("failed_items")
        aggregate = db_session.get(
            MultipartModel, result["result"]["multipart_model_id"]
        )
        backend = get_backend()
        data = backend.read_bytes(
            backend.multipart_model_cover_key(aggregate.id, aggregate.cover_filename)
        )
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WEBP"
        assert source.read_bytes() == original

    def test_recovers_cover_without_duplicate_composition(
        self, db_session, intake_case, monkeypatch
    ):
        from app.modules.library import multipart_covers

        user, job_id, _, _, _, run = intake_case
        publish = multipart_covers.publish

        def fail(*args, **kwargs):
            raise importer.ImportError_("storage_unavailable")

        monkeypatch.setattr(multipart_covers, "publish", fail)
        first = run(cover="photo.png")
        assert first["failed"] == 1
        aggregate_id = first["result"]["multipart_model_id"]
        assert len(db_session.exec(select(MultipartPart)).all()) == 2
        monkeypatch.setattr(multipart_covers, "publish", publish)
        service.retry(job_id, actor=user)
        second = run(cover="photo.png")
        assert second["failed"] == 0, second.get("failed_items")
        assert second["result"]["multipart_model_id"] == aggregate_id
        assert len(db_session.exec(select(MultipartModel)).all()) == 1
        assert len(db_session.exec(select(Document)).all()) == 2

    def test_refuses_cover_recovery_after_a_user_edit(
        self, db_session, intake_case, monkeypatch
    ):
        from app.modules.library import multipart_covers

        user, job_id, _, _, _, run = intake_case
        publish = multipart_covers.publish

        def fail(*args, **kwargs):
            raise importer.ImportError_("storage_unavailable")

        monkeypatch.setattr(multipart_covers, "publish", fail)
        first = run(cover="photo.png")
        aggregate = db_session.get(
            MultipartModel, first["result"]["multipart_model_id"]
        )
        aggregate.name = "User edited assembly"
        db_session.add(aggregate)
        db_session.commit()
        monkeypatch.setattr(multipart_covers, "publish", publish)
        service.retry(job_id, actor=user)
        second = run(cover="photo.png")
        assert second["failed_items"][0]["reason"] == "edit_conflict"
        db_session.refresh(aggregate)
        assert aggregate.name == "User edited assembly"
        assert aggregate.cover_filename is None

    def test_fences_cover_publication_after_cancellation(
        self, db_session, intake_case, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled
        from app.db.models import JobState
        from app.modules.library import multipart_covers

        _, job_id, _, _, _, run = intake_case
        prepare = multipart_covers.prepare_bytes

        def cancel_after_preparation(session, *args, **kwargs):
            candidate = prepare(session, *args, **kwargs)
            job = session.get(Job, job_id)
            job.state = JobState.CANCELLED
            session.add(job)
            session.commit()
            return candidate

        monkeypatch.setattr(multipart_covers, "prepare_bytes", cancel_after_preparation)
        with pytest.raises(OperationCancelled):
            run(cover="photo.png")
        db_session.expire_all()
        aggregate = db_session.exec(select(MultipartModel)).one()
        assert aggregate.cover_filename is None
        assert {
            file.original_filename
            for file in db_session.exec(
                select(File).where(File.original_filename.in_(["base.stl", "lid.stl"]))
            ).all()
        } == {"base.stl", "lid.stl"}

    def test_recovers_a_lost_final_completion(
        self, db_session, intake_case, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled
        from app.modules.ingestion.batch_session import BatchSession

        _, _, _, _, _, run = intake_case
        finish = BatchSession.finish

        def interrupt(*args, **kwargs):
            raise OperationCancelled()

        monkeypatch.setattr(BatchSession, "finish", interrupt)
        with pytest.raises(OperationCancelled):
            run(cover="photo.png")
        db_session.expire_all()
        aggregate = db_session.exec(select(MultipartModel)).one()
        cover_filename = aggregate.cover_filename
        documents = {doc.id for doc in db_session.exec(select(Document)).all()}
        monkeypatch.setattr(BatchSession, "finish", finish)
        result = run(cover="photo.png")
        assert result["failed"] == 0, result.get("failed_items")
        assert result["result"]["multipart_model_id"] == aggregate.id
        assert (
            db_session.get(MultipartModel, aggregate.id).cover_filename
            == cover_filename
        )
        assert {doc.id for doc in db_session.exec(select(Document)).all()} == documents
        assert len(db_session.exec(select(MultipartModel)).all()) == 1
