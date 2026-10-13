"""Unit coverage for app/modules/ingestion/inbox.py's internal orchestration —
resolve/run_import/retry/dismiss/reconcile/prune — that the API-level tests
in test_inbox_api.py don't reach."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from printstash_core.imports import CaptureManifestV2
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    ArtifactProvenanceLink,
    CaptureUploadSlot,
    Collection,
    File,
    FileType,
    InboxItem,
    InboxItemCompletion,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    InboxSourceKind,
    Job,
    JobKind,
    Model,
    ModelProvenanceSource,
    StagingLease,
    StorageDeleteIntent,
    User,
)
from app.db.session import get_session_factory
from app.modules.ingestion import import_resolvers, importer, inbox, staging_leases
from app.modules.work.contracts import JobOutcome
from app.modules.work.jobs import jobs
from app.modules.work.submission import nudge
from app.runtime.engine.inline import InlineJobEngine
from app.schemas.inbox import CaptureUploadSlotsCreate, InboxItemUpdate
from tests.factories import build_collection, build_file, build_model, build_user
from tests.factories.ops import build_job_context


def _make_user(session: Session, username: str, *, admin: bool = True) -> User:
    user = build_user(
        session, username=username, password="Password123", superuser=admin
    )
    return user


def _make_collection(session: Session, path: str = "vault") -> Collection:
    col = build_collection(session, name=path, slug=path, path=path)
    return col


def _make_item(session: Session, owner: User, **overrides) -> InboxItem:
    defaults = dict(
        owner_user_id=owner.id,
        source_url="https://example.com/model",
        source_hostname="example.com",
        state=InboxItemState.CAPTURED,
    )
    defaults.update(overrides)
    row = InboxItem(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def imported_model(db_session: Session) -> Model:
    """The model a completed import produced.

    `inbox_items.resulting_model_id` is a foreign key, so the id a fake
    `import_assets` reports has to belong to a real row — an arbitrary integer is
    refused here exactly as it is in production.
    """
    return build_model(db_session, name="imported", slug="imported")


class TestBeginImport:
    def test_rolls_back_when_the_staging_transfer_fails(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = _make_user(db_session, "lease-transfer-rollback")
        staged = tmp_path / "capture.stl"
        staged.write_bytes(b"solid x endsolid")
        item = _make_item(
            db_session,
            owner,
            source_kind=InboxSourceKind.BROWSER,
            state=InboxItemState.REVIEW,
            staging_key=str(staged),
            manifest_json=json.dumps(
                {"kind": "browser_file", "filename": "capture.stl"}
            ),
        )
        lease = staging_leases.create_review_lease(
            db_session,
            inbox_item_id=item.id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=staged.stat().st_size,
            sha256="a" * 64,
        )
        original = (lease.id, lease.inbox_item_id, lease.path, lease.expires_at)
        db_session.commit()

        def fail_transfer(*_args, **_kwargs) -> StagingLease:
            raise staging_leases.StagingLeaseError("injected")

        monkeypatch.setattr(staging_leases, "transfer_inbox_to_job", fail_transfer)
        assert inbox.begin_import(db_session, item, []) is None

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, item.id)
            assert fresh is not None
            assert fresh.state == InboxItemState.FAILED
            assert fresh.error_code == "staging_expired"
            assert fresh.retryable is False
            assert not session.exec(select(Job)).all()
            retained = session.get(StagingLease, original[0])
            assert retained is not None
            assert (retained.inbox_item_id, retained.path) == original[1:3]
            assert retained.expires_at.replace(tzinfo=None) == original[3].replace(
                tzinfo=None
            )


# --------------------------------------------------------------------------- #
# sanitize_source_url / _json_dict / requested_tags
# --------------------------------------------------------------------------- #


_TWO_FILE_MANIFEST = json.dumps(
    {
        "schema_version": 2,
        "kind": "model_files",
        "files": [{"id": "ok"}, {"id": "other"}],
        "selected_ids": ["ok", "other"],
    }
)


class TestValidateImportSelection:
    @pytest.mark.parametrize("requested", [["missing"], ["ok", "missing"], [""]])
    def test_v2_import_selection_rejects_invalid_ids_without_fallback(
        self, db_session: Session, requested: list[str]
    ) -> None:
        owner = _make_user(db_session, "selection-validation")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {
                    "schema_version": 2,
                    "kind": "model_files",
                    "files": [{"id": "ok"}, {"id": "other"}],
                    "selected_ids": ["ok", "other"],
                }
            ),
        )

        with pytest.raises(OperationError) as exc_info:
            inbox.validate_import_selection(row, requested)

        assert exc_info.value.kind is ErrorKind.UNPROCESSABLE
        assert exc_info.value.detail == "file_selection_invalid"

    def test_accepts_a_subset_of_the_manifests_file_ids(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "selection-validation-subset")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=_TWO_FILE_MANIFEST,
        )

        assert inbox.validate_import_selection(row, ["other"]) == ["other"]

    def test_defaults_an_empty_selection_to_every_selected_id(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "selection-validation-default")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=_TWO_FILE_MANIFEST,
        )

        assert inbox.validate_import_selection(row, []) == ["ok", "other"]


# --------------------------------------------------------------------------- #
# list_visible
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# prune_history
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# update()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# resolve()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# run_import()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# retry() / dismiss()
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# reconcile_interrupted_items()
# --------------------------------------------------------------------------- #


class TestReconcileInterruptedItems:
    def test_returns_an_interrupted_resolve_to_the_resolve_source(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "reconcile-resolving")
        row = _make_item(db_session, owner, state=InboxItemState.RESOLVING)

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.CAPTURED

    def test_leaves_an_item_whose_job_is_still_active(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "reconcile-active-job")
        job_id = jobs.create(
            definition=JobKind.INGESTION_INBOX_IMPORT,
            subject_key="inbox_item/active",
            owner_user_id=owner.id,
        )
        row = _make_item(
            db_session, owner, state=InboxItemState.IMPORTING, job_id=job_id
        )

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.IMPORTING

    def test_reconcile_completes_importing_item_with_finished_job(
        self,
        imported_model: Model,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "reconcile-importing-ok")
        job_id = jobs.create(
            definition=JobKind.INGESTION_INBOX_IMPORT,
            subject_key=f"inbox_item/test-{owner.id}",
            owner_user_id=owner.id,
        )
        jobs.finish(job_id, JobOutcome.COMPLETED, model_id=imported_model.id)
        row = _make_item(
            db_session, owner, state=InboxItemState.IMPORTING, job_id=job_id
        )

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.COMPLETED
            assert fresh.resulting_model_id == imported_model.id

    def test_reconcile_finished_capture_runs_normal_terminalization(
        self, db_session: Session
    ) -> None:
        """A restarted job cleans slots after ownership moves to its origin lease.

        Upload publication has already removed each local spool by the time the
        import job is terminalized.  The cleanup seam must therefore use the
        transferred ``capture_upload_slot_origin_id`` lease, not try to look the
        slot up through its pre-import owner column.
        """
        from PIL import Image

        from app.db.models import OwnedStorageObject, StorageObjectState
        from app.modules.storage.storage_backend.runtime import get_backend

        owner = _make_user(db_session, "reconcile-capture-terminalization")
        file_bytes = b"captured-model"
        cover_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
            "AScY42YAAAAASUVORK5CYII="
        )
        payload = CaptureUploadSlotsCreate.model_validate(
            {
                "source_url": "https://makerworld.com/en/models/1234-widget",
                "capture_source": {
                    "provider": "makerworld",
                    "canonical_url": "https://makerworld.com/en/models/1234-widget",
                    "source_item_id": "1234",
                    "adapter_version": "extension-v1",
                    "fields": {},
                    "tags": [],
                },
                "files": [
                    {
                        "id": "widget.3mf",
                        "filename": "widget.3mf",
                        "media_type": "application/octet-stream",
                        "size_bytes": len(file_bytes),
                        "sha256": hashlib.sha256(file_bytes).hexdigest(),
                    }
                ],
                "cover": {
                    "id": "cover",
                    "filename": "cover.png",
                    "media_type": "image/png",
                    "size_bytes": len(cover_bytes),
                    "sha256": hashlib.sha256(cover_bytes).hexdigest(),
                },
            }
        )
        row, slots = inbox.create_capture_upload_slots(db_session, owner, payload)
        slot_ids = {slot.id for slot in slots}
        file_slot = next(slot for slot in slots if slot.role == "file")
        cover_slot = next(slot for slot in slots if slot.role == "cover")
        inbox.upload_capture_slot(
            db_session,
            file_slot,
            stream=BytesIO(file_bytes),
            media_type=file_slot.media_type,
        )
        inbox.upload_capture_slot(
            db_session,
            cover_slot,
            stream=BytesIO(cover_bytes),
            media_type=cover_slot.media_type,
        )
        inbox.finalize_capture_upload(db_session, owner, row.id)

        model = build_model(
            db_session, name="Widget", slug="reconcile-widget", hash="f" * 64
        )
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="makerworld",
            source_item_id="1234",
            canonical_url="https://makerworld.com/en/models/1234-widget",
            identity_key="reconcile-widget",
        )
        artifact = build_file(
            db_session,
            model,
            path="reconcile/widget.3mf",
            filename="widget.3mf",
            file_type=FileType.THREE_MF,
            size_bytes=len(file_bytes),
            sha256=hashlib.sha256(file_bytes).hexdigest(),
        )
        db_session.add_all([source, artifact])
        db_session.flush()
        assert source.id is not None
        assert artifact.id is not None
        db_session.add(
            ArtifactProvenanceLink(
                file_id=artifact.id,
                provenance_source_id=source.id,
                source_file_id="widget.3mf",
                source_filename="widget.3mf",
                blob_sha256=artifact.sha256,
                import_key="reconcile-widget-import",
            )
        )
        db_session.commit()

        job_id = jobs.create(
            definition=JobKind.INGESTION_INBOX_IMPORT,
            subject_key=f"inbox_item/test-{owner.id}",
            owner_user_id=owner.id,
        )
        jobs.finish(
            job_id,
            JobOutcome.COMPLETED,
            model_id=model.id,
            result={
                "items": [
                    {
                        "source_selection_id": "widget.3mf",
                        "result_key": "self",
                        "name": "widget.3mf",
                        "model_id": model.id,
                        "file_id": artifact.id,
                    }
                ]
            },
        )
        row = db_session.get(InboxItem, row.id)
        assert row is not None
        row.state = InboxItemState.IMPORTING
        row.job_id = job_id
        staging_leases.transfer_capture_slots_to_job(
            db_session, inbox_item_id=row.id, job_id=job_id
        )
        db_session.add(row)
        db_session.commit()

        for slot in slots:
            assert not staging_leases.capture_slot_staging_path(slot.id).exists()
        with get_session_factory().scoped_session() as session:
            transferred = session.exec(
                select(StagingLease).where(
                    StagingLease.capture_upload_slot_origin_id.in_(slot_ids),
                    StagingLease.job_id == job_id,
                    StagingLease.capture_upload_slot_id.is_(None),
                )
            ).all()
            assert {
                lease.capture_upload_slot_origin_id for lease in transferred
            } == slot_ids

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh is not None
            assert fresh.state == InboxItemState.COMPLETED, fresh.error_code
            assert fresh.resulting_model_id == model.id
            assert fresh.retryable is False
            assert fresh.error_code is None
            result = session.exec(
                select(InboxItemResult).where(InboxItemResult.inbox_item_id == row.id)
            ).one()
            assert (
                result.source_selection_id,
                result.result_key,
                result.model_id,
                result.file_id,
                result.provenance_source_id,
                result.retryable,
            ) == ("widget.3mf", "self", model.id, artifact.id, source.id, False)
            assert (
                session.exec(
                    select(CaptureUploadSlot).where(
                        CaptureUploadSlot.inbox_item_id == row.id
                    )
                ).all()
                == []
            )
            assert (
                session.exec(
                    select(StagingLease).where(StagingLease.job_id == job_id)
                ).all()
                == []
            )
            intents = session.exec(select(StorageDeleteIntent)).all()
            assert {intent.resource_id for intent in intents} == slot_ids
            cover = inbox.source_covers.get(session, source.id)
            assert cover is not None
            assert cover.provenance_source_id == source.id
            assert cover.content_type == "image/webp"
            stored = get_backend().read_bytes(cover.storage_key)
            with Image.open(BytesIO(stored)) as image:
                image.load()
                assert image.format == "WEBP"
                width = int(settings.model_thumbnail_width)
                assert image.size == (width, round(width * 3 / 4))
            receipt = session.exec(
                select(OwnedStorageObject).where(
                    OwnedStorageObject.key == cover.storage_key,
                    OwnedStorageObject.object_kind == "model_source_cover",
                    OwnedStorageObject.state == StorageObjectState.COMMITTED,
                )
            ).one()
            assert receipt.size_bytes == len(stored)
            assert receipt.sha256 == hashlib.sha256(stored).hexdigest()

    def test_reconcile_completed_capture_cleanup_pending_preserves_imported_result(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, make_job
    ) -> None:
        """Startup cleanup repairs a completed item without re-running ingestion."""
        owner = _make_user(db_session, "reconcile-completed-cleanup")
        file_bytes = b"already-imported-model"
        source_url = "https://makerworld.com/en/models/9876-widget"
        payload = CaptureUploadSlotsCreate.model_validate(
            {
                "source_url": source_url,
                "capture_source": {
                    "provider": "makerworld",
                    "canonical_url": source_url,
                    "source_item_id": "9876",
                    "adapter_version": "extension-v1",
                    "fields": {},
                    "tags": [],
                },
                "files": [
                    {
                        "id": "widget.3mf",
                        "filename": "widget.3mf",
                        "media_type": "application/octet-stream",
                        "size_bytes": len(file_bytes),
                        "sha256": hashlib.sha256(file_bytes).hexdigest(),
                    }
                ],
            }
        )
        row, slots = inbox.create_capture_upload_slots(db_session, owner, payload)
        slot = slots[0]
        inbox.upload_capture_slot(
            db_session, slot, stream=BytesIO(file_bytes), media_type=slot.media_type
        )
        assert slot.storage_key is not None
        slot_id = slot.id
        slot_key = slot.storage_key

        model = build_model(
            db_session,
            name="Completed widget",
            slug="completed-widget",
            hash="c" * 64,
            source_url=source_url,
        )
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="makerworld",
            source_item_id="9876",
            canonical_url=source_url,
            identity_key="completed-widget-source",
        )
        artifact = build_file(
            db_session,
            model,
            path="completed/widget.3mf",
            filename="widget.3mf",
            file_type=FileType.THREE_MF,
            size_bytes=len(file_bytes),
            sha256=hashlib.sha256(file_bytes).hexdigest(),
        )
        db_session.add_all([source, artifact])
        db_session.flush()
        link = ArtifactProvenanceLink(
            file_id=artifact.id,
            provenance_source_id=source.id,
            source_file_id="widget.3mf",
            source_filename="widget.3mf",
            blob_sha256=artifact.sha256,
            import_key="completed-widget-import",
        )
        result = InboxItemResult(
            inbox_item_id=row.id,
            source_selection_id="widget.3mf",
            result_key="self",
            original_filename="widget.3mf",
            state=InboxItemResultState.IMPORTED,
            model_id=model.id,
            file_id=artifact.id,
            provenance_source_id=source.id,
            retryable=False,
        )
        db_session.add_all([link, result])
        from app.db.models import JobState

        job_id = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            subject=f"inbox_item/{row.id}",
            owner=owner,
            state=JobState.COMPLETED,
        ).id
        row.state = InboxItemState.COMPLETED
        row.job_id = job_id
        row.resulting_model_id = model.id
        row.completion = InboxItemCompletion.COMPLETE
        row.retryable = True
        row.error_code = "capture_upload_cleanup_pending"
        row.completed_at = utcnow()
        staging_leases.transfer_capture_slots_to_job(
            db_session, inbox_item_id=row.id, job_id=job_id
        )
        db_session.add(row)
        db_session.commit()

        model_snapshot = (model.name, model.source_url, model.hash)
        artifact_snapshot = (
            artifact.path,
            artifact.original_filename,
            artifact.file_type,
            artifact.size_bytes,
            artifact.sha256,
        )
        result_snapshot = (
            result.source_selection_id,
            result.result_key,
            result.model_id,
            result.file_id,
            result.provenance_source_id,
            result.state,
        )
        monkeypatch.setattr(
            inbox,
            "_finish_import",
            lambda *_args: pytest.fail("completed cleanup must not re-run ingestion"),
        )
        monkeypatch.setattr(
            inbox,
            "_attach_capture_cover",
            lambda *_args: pytest.fail("completed cleanup must not re-attach cover"),
        )

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh is not None
            assert fresh.state == InboxItemState.COMPLETED
            assert fresh.resulting_model_id == model.id
            assert fresh.retryable is False
            assert fresh.error_code is None
            assert (
                session.exec(
                    select(CaptureUploadSlot).where(
                        CaptureUploadSlot.inbox_item_id == row.id
                    )
                ).all()
                == []
            )
            assert (
                session.exec(
                    select(StagingLease).where(StagingLease.job_id == job_id)
                ).all()
                == []
            )
            intent = session.exec(select(StorageDeleteIntent)).one()
            assert intent.key == slot_key
            assert intent.resource_id == slot_id
            preserved_model = session.get(Model, model.id)
            preserved_artifact = session.get(File, artifact.id)
            preserved_result = session.exec(
                select(InboxItemResult).where(InboxItemResult.inbox_item_id == row.id)
            ).one()
            assert preserved_model is not None
            assert preserved_artifact is not None
            assert (
                preserved_model.name,
                preserved_model.source_url,
                preserved_model.hash,
            ) == model_snapshot
            assert (
                preserved_artifact.path,
                preserved_artifact.original_filename,
                preserved_artifact.file_type,
                preserved_artifact.size_bytes,
                preserved_artifact.sha256,
            ) == artifact_snapshot
            assert (
                preserved_result.source_selection_id,
                preserved_result.result_key,
                preserved_result.model_id,
                preserved_result.file_id,
                preserved_result.provenance_source_id,
                preserved_result.state,
            ) == result_snapshot
        assert inbox.get_backend().exists(slot_key)

    def test_reconcile_completed_v2_job_without_results_stays_retryable(
        self,
        imported_model: Model,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "reconcile-v2-no-results")
        job_id = jobs.create(
            definition=JobKind.INGESTION_INBOX_IMPORT,
            subject_key=f"inbox_item/test-{owner.id}",
            owner_user_id=owner.id,
        )
        jobs.finish(job_id, JobOutcome.COMPLETED, model_id=imported_model.id)
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.IMPORTING,
            job_id=job_id,
            manifest_json=json.dumps(
                {
                    "schema_version": 2,
                    "kind": "model_files",
                    "source": {},
                    "files": [],
                    "selected_ids": [],
                }
            ),
        )

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh is not None
            assert fresh.state == InboxItemState.FAILED
            assert fresh.retryable is True
            assert fresh.resulting_model_id is None

    def test_reconcile_fails_importing_item_without_finished_job(
        self,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "reconcile-importing-fail")
        row = _make_item(db_session, owner, state=InboxItemState.IMPORTING, job_id=None)

        inbox.reconcile_interrupted_items()

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.error_code == "import_interrupted"


class TestSanitizeSourceUrl:
    def test_sanitize_source_url_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ValueError, match="url_invalid"):
            inbox.sanitize_source_url("ftp://example.com/model.stl")

    def test_sanitize_source_url_rejects_missing_hostname(self) -> None:
        with pytest.raises(ValueError, match="url_invalid"):
            inbox.sanitize_source_url("https:///model.stl")

    def test_sanitizes_a_source_url_down_to_its_safe_parts(self) -> None:
        result = inbox.sanitize_source_url(
            "HTTPS://Example.com:8443/model?token=secret&view=files"
        )
        assert result == "https://example.com:8443/model?view=files"

    def test_sanitize_source_url_rejects_userinfo_before_redaction(self) -> None:
        with pytest.raises(ValueError, match="url_invalid"):
            inbox.sanitize_source_url(
                "HTTPS://alice:password@Example.com/model?view=files"
                "&X-Amz-Credential=credential&x_amz.signature=signature"
                "&X-Amz-Security-Token=session#private"
            )

    def test_sanitize_source_url_redacts_normalized_signed_query_keys(self) -> None:
        result = inbox.sanitize_source_url(
            "HTTPS://Example.com/model?view=files"
            "&X-Amz-Credential=credential&x_amz.signature=signature"
            "&X-Amz-Security-Token=session#private"
        )
        assert result == "https://example.com/model?view=files"


class TestListVisible:
    def test_lists_the_items_the_owner_owns(self, db_session: Session) -> None:
        owner = _make_user(db_session, "inbox-owner", admin=False)
        other = _make_user(db_session, "inbox-other", admin=False)
        mine = _make_item(db_session, owner)
        _make_item(db_session, other)
        done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)

        rows = inbox.list_visible(db_session, owner)

        assert {row.id for row in rows} == {mine.id, done.id}

    def test_omits_completed_items_when_asked_to(self, db_session: Session) -> None:
        owner = _make_user(db_session, "inbox-owner", admin=False)
        mine = _make_item(db_session, owner)
        _make_item(db_session, owner, state=InboxItemState.COMPLETED)

        rows = inbox.list_visible(db_session, owner, include_completed=False)

        assert {row.id for row in rows} == {mine.id}

    def test_shows_an_admin_every_owners_items(self, db_session: Session) -> None:
        owner = _make_user(db_session, "inbox-owner", admin=False)
        admin = _make_user(db_session, "inbox-admin", admin=True)
        mine = _make_item(db_session, owner)
        done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)

        rows = inbox.list_visible(db_session, admin)

        assert {row.id for row in rows} >= {mine.id, done.id}


class TestPruneHistory:
    def test_prune_history_removes_only_old_terminal_items(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "prune-owner")
        old_done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)
        old_done.updated_at = utcnow() - timedelta(days=40)
        recent_done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)
        still_review = _make_item(db_session, owner, state=InboxItemState.REVIEW)
        still_review.updated_at = utcnow() - timedelta(days=40)
        db_session.add_all([old_done, recent_done, still_review])
        db_session.commit()
        old_done_id, recent_done_id, still_review_id = (
            old_done.id,
            recent_done.id,
            still_review.id,
        )

        pruned = inbox.prune_history(retention_days=30)

        assert pruned == 1
        with get_session_factory().scoped_session() as session:
            assert session.get(InboxItem, old_done_id) is None
            assert session.get(InboxItem, recent_done_id) is not None
            assert session.get(InboxItem, still_review_id) is not None


class TestUpdate:
    def test_update_rejects_invalid_v2_selection_before_persisting(
        self,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "selection-update-validation")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {
                    "schema_version": 2,
                    "kind": "model_files",
                    "files": [{"id": "ok"}],
                    "selected_ids": ["ok"],
                }
            ),
        )
        original = row.manifest_json

        with pytest.raises(OperationError) as exc_info:
            inbox.update(db_session, owner, row, InboxItemUpdate(selected_ids=["bad"]))

        assert exc_info.value.detail == "file_selection_invalid"
        assert row.manifest_json == original

    def test_update_empty_v2_selection_persists_manifest_default_selection(
        self,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "selection-update-empty")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {
                    "schema_version": 2,
                    "kind": "model_files",
                    "source": {
                        "provider": "makerworld",
                        "canonical_url": "https://makerworld.com/en/models/1234-widget",
                        "source_item_id": "1234",
                        "source_revision": "1",
                        "adapter_version": "test",
                        "fields": {},
                        "tags": [],
                    },
                    "files": [
                        {
                            "id": "first",
                            "name": "first.stl",
                            "file_type": "stl",
                            "size": None,
                        },
                        {
                            "id": "second",
                            "name": "second.stl",
                            "file_type": "stl",
                            "size": None,
                        },
                    ],
                    "selected_ids": ["first"],
                }
            ),
        )

        updated = inbox.update(db_session, owner, row, InboxItemUpdate(selected_ids=[]))

        manifest = json.loads(updated.manifest_json)
        assert manifest["selected_ids"] == ["first"]
        # The persisted value must remain parseable by the strict V2 contract.
        CaptureManifestV2.from_dict(manifest)

    def test_update_rejects_terminal_states(self, db_session: Session) -> None:
        owner = _make_user(db_session, "update-owner")
        row = _make_item(db_session, owner, state=InboxItemState.IMPORTING)
        with pytest.raises(OperationError) as exc:
            inbox.update(db_session, owner, row, InboxItemUpdate())
        assert exc.value.kind is ErrorKind.CONFLICT

    def test_update_root_collection_requires_superuser(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "update-owner3", admin=False)
        row = _make_item(db_session, owner)
        with pytest.raises(OperationError) as exc:
            inbox.update(db_session, owner, row, InboxItemUpdate(collection_id=None))
        assert exc.value.kind is ErrorKind.FORBIDDEN

    def test_update_merges_selected_ids_into_manifest(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "update-owner2")
        row = _make_item(
            db_session, owner, manifest_json=json.dumps({"kind": "archive"})
        )
        updated = inbox.update(
            db_session, owner, row, InboxItemUpdate(selected_ids=["a.stl", "b.stl"])
        )
        manifest = json.loads(updated.manifest_json)
        assert manifest["selected_ids"] == ["a.stl", "b.stl"]
        assert manifest["kind"] == "archive"


class TestResolve:
    @pytest.mark.asyncio
    async def test_resolve_ignores_item_in_wrong_state(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "resolve-wrong-state")
        row = _make_item(db_session, owner, state=InboxItemState.REVIEW)
        await inbox.resolve(row.id)
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.REVIEW

    def test_resolve_completion_does_not_resurrect_dismissed_item_or_leak_staging(
        self,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "resolve-dismiss-race")
        managed = (
            settings.incoming_dir / "inbox" / "resolve-dismiss-race" / "source.zip"
        )
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_bytes(b"resolver-owned")
        row = _make_item(db_session, owner, state=InboxItemState.DISMISSED)
        staging_leases.create_review_lease(
            db_session,
            inbox_item_id=row.id,
            owner_user_id=owner.id,
            path=managed,
            size_bytes=managed.stat().st_size,
            sha256=hashlib.sha256(managed.read_bytes()).hexdigest(),
        )
        db_session.commit()

        inbox._finish_resolve(
            row.id,
            {"kind": "archive", "title": "must-not-publish"},
            managed,
        )

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh is not None
            assert fresh.state == InboxItemState.DISMISSED
            assert fresh.manifest_json == "{}"
            assert fresh.staging_key is None
            assert (
                session.exec(
                    select(StagingLease).where(StagingLease.inbox_item_id == row.id)
                ).first()
                is None
            )
        assert not managed.exists()

    @pytest.mark.asyncio
    async def test_resolve_marks_failed_when_source_url_missing(
        self,
        db_session: Session,
    ) -> None:
        owner = _make_user(db_session, "resolve-no-url")
        row = _make_item(db_session, owner, source_url=None)
        await inbox.resolve(row.id)
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.retryable is True

    @pytest.mark.asyncio
    async def test_resolve_collection_success_builds_manifest(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = _make_user(db_session, "resolve-collection")
        row = _make_item(db_session, owner)
        monkeypatch.setattr(
            import_resolvers, "classify_collection", lambda _url: "printables"
        )

        async def fake_resolve_collection_url(_url: str):
            return "My Collection", [
                import_resolvers.CollectionMember(
                    page_url="https://example.com/model/1", title="Part", source_id="1"
                )
            ]

        monkeypatch.setattr(
            import_resolvers, "resolve_collection_url", fake_resolve_collection_url
        )

        await inbox.resolve(row.id)

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.REVIEW
            manifest = json.loads(fresh.manifest_json)
            assert manifest["kind"] == "collection"
            assert manifest["members"][0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_resolve_collection_failure_marks_item_failed(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = _make_user(db_session, "resolve-collection-fail")
        row = _make_item(db_session, owner)
        monkeypatch.setattr(
            import_resolvers, "classify_collection", lambda _url: "printables"
        )

        async def no_result(_url: str):
            return None

        monkeypatch.setattr(import_resolvers, "resolve_collection_url", no_result)

        await inbox.resolve(row.id)

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.error_code == "collection_resolve_failed"

    @pytest.mark.asyncio
    async def test_resolve_model_files_listing_builds_manifest(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = _make_user(db_session, "resolve-model-files")
        row = _make_item(db_session, owner)
        monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

        async def fake_list_model_files(_url: str):
            return "Bracket", [
                import_resolvers.ModelFile(
                    file_id="f1", name="bracket.stl", file_type="stl", size=10
                )
            ]

        monkeypatch.setattr(import_resolvers, "list_model_files", fake_list_model_files)

        await inbox.resolve(row.id)

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.REVIEW
            manifest = json.loads(fresh.manifest_json)
            assert manifest["kind"] == "model_files"
            assert manifest["files"][0]["id"] == "f1"

    @pytest.mark.asyncio
    async def test_resolve_direct_download_archive_manifest(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        owner = _make_user(db_session, "resolve-archive")
        row = _make_item(db_session, owner)
        monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

        async def no_listing(_url: str):
            return None

        monkeypatch.setattr(import_resolvers, "list_model_files", no_listing)

        async def no_page_url(_url: str):
            return None

        monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)

        staged = tmp_path / "download.bin"
        staged.write_bytes(b"pk-zip-stub")

        async def fake_download(_url: str, **kwargs):
            return staged, "bundle.zip"

        monkeypatch.setattr(importer, "download_to_staging", fake_download)
        monkeypatch.setattr(
            importer,
            "inspect_archive",
            lambda _path: [
                importer.ArchiveEntry(
                    entry_id="0:00000000:1",
                    name="a.stl",
                    size_bytes=1,
                    file_type="stl",
                    is_image=False,
                ),
                importer.ArchiveEntry(
                    entry_id="1:00000000:1",
                    name="readme.txt",
                    size_bytes=1,
                    file_type=None,
                    is_image=False,
                ),
            ],
        )

        await inbox.resolve(row.id)

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.REVIEW
            manifest = json.loads(fresh.manifest_json)
            assert manifest["kind"] == "archive"
            assert [entry["id"] for entry in manifest["entries"]] == ["a.stl"]
            assert fresh.staging_key is not None
            assert Path(fresh.staging_key).exists()

    @pytest.mark.asyncio
    async def test_resolve_direct_download_non_archive_manifest(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        owner = _make_user(db_session, "resolve-direct")
        row = _make_item(db_session, owner)
        monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

        async def no_listing(_url: str):
            return None

        monkeypatch.setattr(import_resolvers, "list_model_files", no_listing)

        async def no_page_url(_url: str):
            return None

        monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)

        staged = tmp_path / "download.stl"
        staged.write_bytes(b"solid x endsolid")

        async def fake_download(_url: str, **kwargs):
            return staged, "model.stl"

        monkeypatch.setattr(importer, "download_to_staging", fake_download)

        await inbox.resolve(row.id)

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.REVIEW
            manifest = json.loads(fresh.manifest_json)
            assert manifest["kind"] == "direct"
        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_resolve_unexpected_exception_marks_item_failed(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        owner = _make_user(db_session, "resolve-boom")
        row = _make_item(db_session, owner)

        def boom(_url: str):
            raise RuntimeError("boom")

        monkeypatch.setattr(import_resolvers, "classify_collection", boom)

        await inbox.resolve(row.id)

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.retryable is True


def _import_through_job(
    session: Session, engine: InlineJobEngine, item_id: int, selected: list[str]
) -> str | None:
    """Accept an import the way the route does, then run the Job it queued."""
    row = session.get(InboxItem, item_id)
    assert row is not None
    job_id = inbox.begin_import(session, row, selected)
    nudge(JobKind.INGESTION_INBOX_IMPORT)
    engine.drain()
    session.expire_all()
    return job_id


class TestBeginImportThroughJob:
    def test_queues_an_import_job_for_a_reviewed_item(
        self, db_session: Session, work_engine: InlineJobEngine
    ) -> None:
        owner = _make_user(db_session, "begin-import-queued")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps({"kind": "direct"}),
        )

        job_id = inbox.begin_import(db_session, row, [])

        status = jobs.get(job_id or "")
        assert status is not None
        assert (status.kind, status.state) == (JobKind.INGESTION_INBOX_IMPORT, "queued")

    def test_moves_the_item_to_importing_under_its_job(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "begin-import-importing")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps({"kind": "direct"}),
        )

        job_id = inbox.begin_import(db_session, row, [])

        db_session.refresh(row)
        assert (row.state, row.job_id) == (InboxItemState.IMPORTING, job_id)

    def test_refuses_an_item_that_is_not_in_review(self, db_session: Session) -> None:
        owner = _make_user(db_session, "run-import-wrong-state")
        row = _make_item(db_session, owner, state=InboxItemState.CAPTURED)

        with pytest.raises(OperationError) as exc:
            inbox.begin_import(db_session, row, [])

        assert exc.value.kind is ErrorKind.CONFLICT

    def test_requires_access_to_the_target_collection(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "run-import-no-access", admin=False)
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            target_collection_id=None,
            manifest_json=json.dumps({"kind": "direct"}),
        )

        with pytest.raises(OperationError) as exc:
            inbox.begin_import(db_session, row, [])

        assert exc.value.kind is ErrorKind.FORBIDDEN


class TestRunImportJob:
    def test_cancels_a_job_whose_item_moved_on(
        self, db_session: Session, work_engine: InlineJobEngine
    ) -> None:
        owner = _make_user(db_session, "run-import-moved-on")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps({"kind": "direct"}),
        )
        job_id = inbox.begin_import(db_session, row, [])
        row.state = InboxItemState.DISMISSED
        db_session.add(row)
        db_session.commit()

        inbox.run_import_job(row.id, build_job_context(job_id or ""))

        status = jobs.get(job_id or "")
        assert status is not None and status.state == "cancelled"

    def test_records_the_resulting_model_when_a_direct_import_completes(
        self, db_session, monkeypatch, tmp_path, work_engine
    ):
        from tests.factories.content import gcode

        owner = _make_user(db_session, "direct-window")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps({"kind": "direct"}),
        )
        payload = gcode(marker="direct-window")

        async def download(_url, *, window_max_bytes=None, window=None):
            assert window is not None
            path = window.directory / "download.gcode"
            assert len(payload) <= window.max_bytes
            path.write_bytes(payload)
            # Successful production downloads seal their exact inode before
            # strict importer cleanup can authorize releasing that output.
            window.seal(path)
            return path, "part.gcode"

        monkeypatch.setattr(importer, "download_to_staging", download)

        async def resolve(_url):
            return "https://example.com/part.gcode"

        monkeypatch.setattr(import_resolvers, "resolve_page_url", resolve)
        _import_through_job(db_session, work_engine, row.id, [])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.COMPLETED, fresh.error_code
            assert fresh.resulting_model_id is not None
            files = session.exec(
                select(File).where(File.model_id == fresh.resulting_model_id)
            ).all()
            assert len(files) == 1
            from app.modules.storage.storage_backend.runtime import get_backend

            assert get_backend().read_bytes(files[0].path) == payload
        assert fresh.completed_at is not None

    def test_fails_retryably_when_an_archive_item_has_no_staging_key(
        self, db_session: Session, work_engine: InlineJobEngine
    ) -> None:
        owner = _make_user(db_session, "run-import-archive-missing")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {"kind": "archive", "entries": [{"id": "a.stl"}, {"id": "b.stl"}]}
            ),
            staging_key=None,
        )

        _import_through_job(db_session, work_engine, row.id, ["a.stl"])

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.retryable is True

    def test_run_import_archive_completes(self, db_session, tmp_path, work_engine):
        import zipfile

        from tests.factories.content import gcode

        owner = _make_user(db_session, "archive-window")
        _overlay["staging_dir"] = tmp_path / "staging"
        settings.incoming_dir.mkdir(parents=True)
        staged_archive = settings.incoming_dir / "bundle.zip"
        payload = gcode(marker="archive-selected")
        with zipfile.ZipFile(staged_archive, "w") as archive:
            archive.writestr("a.gcode", payload)
            archive.writestr("b.gcode", gcode(marker="archive-unselected"))
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {"kind": "archive", "entries": [{"id": "a.gcode"}, {"id": "b.gcode"}]}
            ),
            staging_key=str(staged_archive),
        )
        _import_through_job(db_session, work_engine, row.id, ["a.gcode"])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.COMPLETED
            assert fresh.resulting_model_id is not None
            files = session.exec(
                select(File).where(File.model_id == fresh.resulting_model_id)
            ).all()
            assert len(files) == 1
            from app.modules.storage.storage_backend.runtime import get_backend

            assert get_backend().read_bytes(files[0].path) == payload
            assert files[0].original_filename == "a.gcode"
            assert fresh.staging_key is None
        assert not staged_archive.exists()

    def test_releases_staging_after_importing_a_browser_file_copy(
        self, db_session, monkeypatch, tmp_path, work_engine
    ):
        from tests.factories.content import gcode

        owner = _make_user(db_session, "browser-window")
        _overlay["staging_dir"] = tmp_path / "staging"
        settings.incoming_dir.mkdir(parents=True)
        staged = settings.incoming_dir / "inbox" / "1" / "source.gcode"
        staged.parent.mkdir(parents=True)
        payload = gcode(marker="browser-copy")
        staged.write_bytes(payload)
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {"kind": "browser_file", "filename": "widget.gcode"}
            ),
            staging_key=str(staged),
        )
        original = importer._ingest_one_file
        observed = []

        def commit_copy(path, name, **kwargs):
            assert path != staged
            assert path.read_bytes() == staged.read_bytes() == payload
            assert name == "widget.gcode"
            observed.append(path)
            return original(path, name, **kwargs)

        monkeypatch.setattr(importer, "_ingest_one_file", commit_copy)
        _import_through_job(db_session, work_engine, row.id, [])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.COMPLETED
            assert fresh.resulting_model_id is not None
            files = session.exec(
                select(File).where(File.model_id == fresh.resulting_model_id)
            ).all()
            assert len(files) == 1
            from app.modules.storage.storage_backend.runtime import get_backend

            assert get_backend().read_bytes(files[0].path) == payload
            assert fresh.staging_key is None
        assert len(observed) == 1
        assert not observed[0].exists()
        assert not staged.exists()

    def test_run_import_model_files_completes(
        self, db_session, monkeypatch, tmp_path, work_engine
    ):
        from tests.factories.content import gcode

        owner = _make_user(db_session, "model_files-window")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {
                    "kind": "model_files",
                    "files": [
                        {
                            "id": "f1",
                            "name": "part.gcode",
                            "file_type": "gcode",
                            "size": 64,
                        }
                    ],
                }
            ),
        )
        payload = gcode(marker="model_files-window")

        async def download(_url, *, window_max_bytes=None, window=None):
            assert window is not None
            path = window.directory / "download.gcode"
            assert len(payload) <= window.max_bytes
            path.write_bytes(payload)
            # Successful production downloads seal their exact inode before
            # strict importer cleanup can authorize releasing that output.
            window.seal(path)
            return path, "part.gcode"

        monkeypatch.setattr(importer, "download_to_staging", download)

        async def resolve(_url, chosen):
            assert [file.file_id for file in chosen] == ["f1"]
            return (
                import_resolvers.SelectedFileDownload(
                    chosen[0], "https://example.com/part.gcode"
                ),
            )

        monkeypatch.setattr(import_resolvers, "resolve_selected_sources", resolve)
        _import_through_job(db_session, work_engine, row.id, [])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.COMPLETED, fresh.error_code
            assert fresh.resulting_model_id is not None
            files = session.exec(
                select(File).where(File.model_id == fresh.resulting_model_id)
            ).all()
            assert len(files) == 1
            from app.modules.storage.storage_backend.runtime import get_backend

            assert get_backend().read_bytes(files[0].path) == payload

    def test_run_import_collection_completes(
        self, db_session, monkeypatch, tmp_path, work_engine
    ):
        from tests.factories.content import gcode

        owner = _make_user(db_session, "collection-window")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps(
                {
                    "kind": "collection",
                    "members": [
                        {"id": "m1", "page_url": "https://example.com/model/1"}
                    ],
                }
            ),
        )
        payload = gcode(marker="collection-window")

        async def download(_url, *, window_max_bytes=None, window=None):
            assert window is not None
            path = window.directory / "download.gcode"
            assert len(payload) <= window.max_bytes
            path.write_bytes(payload)
            # Successful production downloads seal their exact inode before
            # strict importer cleanup can authorize releasing that output.
            window.seal(path)
            return path, "part.gcode"

        monkeypatch.setattr(importer, "download_to_staging", download)

        async def resolve(_url):
            return "https://example.com/part.gcode"

        monkeypatch.setattr(import_resolvers, "resolve_page_url", resolve)
        _import_through_job(db_session, work_engine, row.id, [])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.COMPLETED, fresh.error_code
            assert fresh.resulting_model_id is not None
            files = session.exec(
                select(File).where(File.model_id == fresh.resulting_model_id)
            ).all()
            assert len(files) == 1
            from app.modules.storage.storage_backend.runtime import get_backend

            assert get_backend().read_bytes(files[0].path) == payload

    def test_run_import_job_not_completed_marks_failed(
        self, db_session, monkeypatch, work_engine
    ):
        owner = _make_user(db_session, "failed-window")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps({"kind": "direct"}),
        )

        async def resolve(_url):
            return "https://example.com/part.gcode"

        async def download(_url, *, window_max_bytes=None, window=None):
            raise importer.ImportError_("ingest_exploded")

        monkeypatch.setattr(import_resolvers, "resolve_page_url", resolve)
        monkeypatch.setattr(importer, "download_to_staging", download)
        _import_through_job(db_session, work_engine, row.id, [])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.error_code == "ingest_exploded"
            assert fresh.retryable is True

    def test_run_import_exception_marks_failed(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        work_engine: InlineJobEngine,
    ) -> None:
        owner = _make_user(db_session, "run-import-boom")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=json.dumps({"kind": "direct"}),
        )

        async def boom(_url: str):
            raise RuntimeError("network exploded")

        monkeypatch.setattr(import_resolvers, "resolve_page_url", boom)

        _import_through_job(db_session, work_engine, row.id, [])

        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh.state == InboxItemState.FAILED
            assert fresh.retryable is True


class TestRetry:
    def test_retry_partial_reselects_only_failed_source_ids(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "retry-partial")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.COMPLETED,
            completion=InboxItemCompletion.PARTIAL,
            retryable=True,
            manifest_json=json.dumps(
                {"kind": "model_files", "selected_ids": ["ok", "bad"]}
            ),
        )
        db_session.add_all(
            [
                InboxItemResult(
                    inbox_item_id=row.id,
                    source_selection_id="ok",
                    result_key="self",
                    original_filename="ok.stl",
                    state=InboxItemResultState.IMPORTED,
                    retryable=False,
                ),
                InboxItemResult(
                    inbox_item_id=row.id,
                    source_selection_id="bad",
                    result_key="self",
                    original_filename="bad.stl",
                    state=InboxItemResultState.FAILED,
                    error_code="captured_artifact_trashed",
                    retryable=True,
                ),
            ]
        )
        db_session.commit()

        retried = inbox.retry(db_session, row)

        assert retried.state == InboxItemState.REVIEW
        assert retried.completion is None
        assert inbox._json_dict(retried.manifest_json)["selected_ids"] == ["bad"]

    def test_refuses_to_retry_an_item_that_did_not_fail(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "retry-owner")
        row = _make_item(db_session, owner, state=InboxItemState.REVIEW)
        with pytest.raises(OperationError) as exc:
            inbox.retry(db_session, row)
        assert exc.value.kind is ErrorKind.CONFLICT

    def test_retry_returns_to_review_when_manifest_present(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "retry-owner2")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.FAILED,
            retryable=True,
            manifest_json=json.dumps({"kind": "direct"}),
        )
        updated = inbox.retry(db_session, row)
        assert updated.state == InboxItemState.REVIEW

    def test_retry_returns_to_captured_without_manifest(
        self, db_session: Session
    ) -> None:
        owner = _make_user(db_session, "retry-owner3")
        row = _make_item(db_session, owner, state=InboxItemState.FAILED, retryable=True)
        updated = inbox.retry(db_session, row)
        assert updated.state == InboxItemState.CAPTURED

    def test_legacy_browser_file_failure_retry_then_success_returns_lease_to_review(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        work_engine: InlineJobEngine,
    ) -> None:
        from app.modules.storage.storage_backend.runtime import get_backend
        from tests.factories.content import gcode

        owner = _make_user(db_session, "legacy-browser-retry")
        _overlay["staging_dir"] = tmp_path / "staging"
        staged = settings.incoming_dir / "legacy" / "widget.gcode"
        staged.parent.mkdir(parents=True)
        payload = gcode(marker="legacy-browser-file")
        staged.write_bytes(payload)
        row = _make_item(
            db_session,
            owner,
            source_kind=InboxSourceKind.BROWSER,
            state=InboxItemState.REVIEW,
            source_url="https://makerworld.com/en/models/1234-widget",
            manifest_json=json.dumps(
                {"kind": "browser_file", "filename": "widget.gcode"}
            ),
            staging_key=str(staged),
        )
        staging_leases.create_review_lease(
            db_session,
            inbox_item_id=row.id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        db_session.commit()
        baseline = {
            file.id: file.model_dump() for file in db_session.exec(select(File)).all()
        }
        consume = importer._ingest_one_file
        consumed = []

        def fail_first_copy(path, name, **kwargs):
            assert path != staged
            assert path.read_bytes() == staged.read_bytes() == payload
            assert name == "widget.gcode"
            consumed.append(path)
            raise RuntimeError("first import failed")

        monkeypatch.setattr(importer, "_ingest_one_file", fail_first_copy)
        _import_through_job(db_session, work_engine, row.id, [])

        db_session.expire_all()
        failed = db_session.get(InboxItem, row.id)
        assert failed is not None and failed.state == InboxItemState.FAILED
        assert failed.job_id is not None
        first_job = failed.job_id
        lease = db_session.exec(
            select(StagingLease).where(StagingLease.job_id == first_job)
        ).one()
        assert lease.inbox_item_id is None
        assert lease.path == str(staged)
        assert staged.read_bytes() == payload
        assert len(consumed) == 1 and not consumed[0].exists()
        assert {
            file.id: file.model_dump() for file in db_session.exec(select(File)).all()
        } == baseline
        retried = inbox.retry(db_session, failed)
        assert retried.state == InboxItemState.REVIEW
        returned = db_session.exec(
            select(StagingLease).where(StagingLease.id == lease.id)
        ).one()
        assert returned.inbox_item_id == row.id and returned.job_id is None
        assert returned.path == str(staged)

        monkeypatch.setattr(importer, "_ingest_one_file", consume)
        _import_through_job(db_session, work_engine, row.id, [])
        with get_session_factory().scoped_session() as session:
            fresh = session.get(InboxItem, row.id)
            assert fresh is not None and fresh.state == InboxItemState.COMPLETED
            assert fresh.resulting_model_id is not None
            assert fresh.job_id != first_job
            assert fresh.staging_key is None
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            created = [file for file in files if file.id not in baseline]
            assert len(created) == 1
            assert created[0].model_id == fresh.resulting_model_id
            assert get_backend().read_bytes(created[0].path) == payload
        assert not staged.exists()


class TestDismiss:
    def test_dismiss_rejects_item_while_resolving(self, db_session: Session) -> None:
        owner = _make_user(db_session, "dismiss-resolving")
        managed = settings.incoming_dir / "inbox" / "dismiss-resolving" / "source.zip"
        managed.parent.mkdir(parents=True, exist_ok=True)
        managed.write_bytes(b"resolver-owned")
        row = _make_item(
            db_session,
            owner,
            state=InboxItemState.RESOLVING,
            staging_key=str(managed),
        )

        with pytest.raises(OperationError, match="pending_import_busy"):
            inbox.dismiss(db_session, row)

        db_session.rollback()
        fresh = db_session.get(InboxItem, row.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.RESOLVING
        assert fresh.staging_key == str(managed)
        assert managed.exists()
        managed.unlink()
        managed.parent.rmdir()

    def test_dismiss_rejects_importing_item(self, db_session: Session) -> None:
        owner = _make_user(db_session, "dismiss-owner")
        row = _make_item(db_session, owner, state=InboxItemState.IMPORTING)
        with pytest.raises(OperationError) as exc:
            inbox.dismiss(db_session, row)
        assert exc.value.kind is ErrorKind.CONFLICT

    def test_dismiss_cleans_up_staging_directory(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        owner = _make_user(db_session, "dismiss-owner2")
        _overlay["staging_dir"] = tmp_path / "staging"
        staging_dir = settings.incoming_dir / "inbox-item"
        staging_dir.mkdir(parents=True)
        staged_file = staging_dir / "source.stl"
        staged_file.write_bytes(b"solid x endsolid")
        row = _make_item(
            db_session, owner, state=InboxItemState.REVIEW, staging_key=str(staged_file)
        )

        inbox.dismiss(db_session, row)

        assert row.state == InboxItemState.DISMISSED
        assert row.staging_key is None
        assert not staged_file.exists()
        assert not staging_dir.exists()


class TestJsonDict:
    def test_json_dict_returns_empty_on_bad_json(self) -> None:
        assert inbox._json_dict("not json") == {}
        assert inbox._json_dict("[]") == {}  # valid JSON but not a dict
        assert inbox._json_dict("") == {}


class TestRequestedTags:
    def test_requested_tags_returns_empty_on_bad_json(self) -> None:
        assert inbox.requested_tags("not json") == []
        assert inbox.requested_tags("{}") == []  # valid JSON but not a list
        assert inbox.requested_tags(json.dumps(["a", "b"])) == ["a", "b"]


class TestInboxRead:
    @pytest.mark.parametrize(
        "raw",
        [
            "{}",
            "not json",
            "null",
            '{"schema_version": 2}',
            '{"schema_version": 2, "kind": "model_files", "files": [], "selected_ids": []}',
        ],
        ids=["empty", "malformed", "null", "missing-kind", "v2-missing-source"],
    )
    def test_corrupt_manifest_has_readable_fallback(
        self, make_user, make_inbox_item, raw
    ) -> None:
        item = make_inbox_item(make_user(), manifest_json=raw)

        result = inbox.read(item)

        assert result.manifest.model_dump() == {
            "kind": "model_files",
            "files": [],
            "selected_ids": [],
            "schema_version": 1,
        }

    def test_read_preserves_corrupt_persisted_manifest(
        self, db_session: Session, make_user, make_inbox_item
    ) -> None:
        raw = '{"schema_version": 2, "kind": "model_files", "files": [{"id": "recoverable"}]}'
        item = make_inbox_item(make_user(), manifest_json=raw)

        inbox.read(item, db_session)
        db_session.commit()
        db_session.refresh(item)

        assert item.manifest_json == raw

    def test_valid_v2_manifest_is_preserved(self, make_user, make_inbox_item) -> None:
        from tests.factories.capture import capture_source

        manifest = {
            "schema_version": 2,
            "kind": "model_files",
            "source": capture_source(),
            "files": [
                {"id": "file-1", "name": "part.stl", "file_type": "stl", "size": 100}
            ],
            "selected_ids": ["file-1"],
        }
        item = make_inbox_item(make_user(), manifest=manifest)

        result = inbox.read(item)

        assert result.manifest.model_dump() == manifest

    def test_legacy_manifest_is_preserved(self, make_user, make_inbox_item) -> None:
        manifest = {"kind": "direct", "url": "https://example.com/part.stl"}
        item = make_inbox_item(make_user(), manifest=manifest)

        result = inbox.read(item)

        assert result.manifest.model_dump() == manifest
