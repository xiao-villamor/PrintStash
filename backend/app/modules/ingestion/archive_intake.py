"""Recover guides and finalize a reference-only Multipart after all selected parts."""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from printstash_core.files import slugify
from sqlmodel import Session, select

from app.core.cancellation import OperationCancelled
from app.core.errors import OperationError
from app.db.models import (
    Collection,
    CollectionRole,
    Document,
    JobState,
    MultipartModel,
    User,
)
from app.db.projections import content_changed
from app.db.scopes import live
from app.modules.library import document_uploads, multipart_covers, multipart_models
from app.modules.library.edit_preconditions import EditKind, etag
from app.modules.media.source_cover_processing import (
    MAX_SOURCE_COVER_BYTES,
    process_source_cover_upload,
)
from app.modules.work.contracts import JobContext
from app.modules.work.jobs import jobs
from app.schemas.editing import EditPrecondition
from app.schemas.multipart_models import MultipartChoiceWrite, MultipartPartWrite

from . import importer, requests, scratch_windows
from .archive_plan import ArchiveIntake, MultipartIntake
from .batch_contracts import BatchSupplement, FinalizationFailure, ImportedDocument
from .batch_session import BatchSession, import_failure_code


def _fence(session: Session, ctx: JobContext) -> None:
    if (
        jobs.lock_execution(
            session,
            ctx.job_id,
            epoch=ctx.execution_epoch,
            attempt=ctx.attempt,
            states=(JobState.RUNNING,),
        )
        is None
    ):
        raise OperationCancelled()


def _receipt(session: Session, ctx: JobContext) -> dict:
    request = requests.load(session, ctx.job_id)
    value = json.loads(request.manifest_json or "{}")
    if not isinstance(value, dict):
        raise OperationError("archive_receipt_invalid")
    return value


def _store(session: Session, ctx: JobContext, value: dict) -> None:
    _fence(session, ctx)
    request = requests.load(session, ctx.job_id)
    request.manifest_json = json.dumps(value, separators=(",", ":"))
    session.add(request)


@dataclass
class ArchiveDocumentIntent:
    context: JobContext
    name: str
    document_id: int | None

    def record(self, session: Session, document_id: int) -> None:
        _fence(session, self.context)
        receipt = _receipt(session, self.context)
        receipt.setdefault("documents", {})[self.name] = document_id
        _store(session, self.context, receipt)


def finalize(
    batch: BatchSession,
    *,
    archive: Path,
    plan: ArchiveIntake,
    source_id: str,
    collection_path: str,
    actor_user_id: int,
) -> BatchSupplement:
    """Published part receipts and guide receipts survive an interrupted final step."""
    parts = batch.source_entries(source_id)
    if not parts or not all(part.published for part in parts):
        return BatchSupplement()
    documents: list[ImportedDocument] = []
    failures: list[FinalizationFailure] = []
    ctx = batch.context
    with batch.factory.scoped_session() as session:
        user = session.get(User, actor_user_id)
        if user is None or not user.is_active:
            raise OperationError("archive_actor_unavailable")
        path = "/".join(
            slugify(part.strip()) for part in collection_path.split("/") if part.strip()
        )
        collection = session.exec(
            select(Collection).where(Collection.path == path, live(Collection))
        ).first()
        if collection is None:
            raise OperationError("archive_collection_unavailable")
        collection_id = collection.id
        receipt = _receipt(session, ctx)
        if receipt.get("multipart_model_id") is not None:
            aggregate = multipart_models.require(
                session, user, receipt["multipart_model_id"], CollectionRole.EDIT
            )
            return _finish_cover(
                batch,
                session,
                user,
                aggregate,
                archive,
                plan,
                tuple(
                    ImportedDocument(name, doc_id)
                    for name, doc_id in receipt.get("documents", {}).items()
                ),
            )
        session.commit()
        for name in plan.documents:
            try:
                receipt = _receipt(session, ctx)
                intent = ArchiveDocumentIntent(
                    ctx, name, receipt.get("documents", {}).get(name)
                )
                session.commit()
                with closing(
                    importer.iter_archive_entries(
                        archive,
                        [name],
                        auxiliary=True,
                        owner=scratch_windows.JobWindowOwner(
                            ctx.job_id, ctx.execution_epoch
                        ),
                        session_factory=batch.factory,
                    )
                ) as entries:
                    found = next(entries, None)
                    if found is None:
                        raise OperationError("archive_document_missing")
                    entry, (staged, _) = found
                    with staged.open("rb") as stream:
                        document = document_uploads.publish(
                            session,
                            user,
                            stream=stream,
                            filename=entry.name,
                            size=entry.size_bytes,
                            name=Path(entry.name).name,
                            collection_id=collection_id,
                            intent=intent,
                        )
                    assert document.id is not None
                    documents.append(ImportedDocument(name, document.id))
            except Exception as exc:
                session.rollback()
                failures.append(FinalizationFailure(name, import_failure_code(exc)))
        if failures or not isinstance(plan, MultipartIntake):
            return BatchSupplement(documents=tuple(documents), failures=tuple(failures))
        try:
            _fence(session, ctx)
            by_name = {part.spec.display_name: part for part in parts}
            requested_parts = []
            for part in plan.parts:
                record = by_name[part.entry_name.replace("\\", "/")]
                assert record.model_id is not None
                requested_parts.append(
                    MultipartPartWrite(
                        name=part.name,
                        choices=[MultipartChoiceWrite(model_id=record.model_id)],
                    )
                )
            aggregate = multipart_models.create_composition_in_transaction(
                session,
                user,
                name=plan.name,
                collection_id=collection_id,
                parts=requested_parts,
            )
            for result in documents:
                doc = session.exec(
                    select(Document).where(
                        Document.id == result.document_id, live(Document)
                    )
                ).one()
                if (
                    doc.collection_id != collection_id
                    or doc.created_by != user.id
                    or doc.multipart_model_id is not None
                ):
                    raise OperationError("document_import_intent_mismatch")
                doc.multipart_model_id = aggregate.id
                session.add(doc)
                content_changed(session, "document", [doc.id])
            session.flush()
            session.refresh(aggregate)
            receipt = _receipt(session, ctx)
            assert aggregate.id is not None
            receipt["multipart_model_id"] = aggregate.id
            receipt["cover_base"] = etag(
                EditKind.MULTIPART,
                aggregate.id,
                aggregate.edit_version,
                aggregate.edit_epoch,
            )
            _store(session, ctx, receipt)
            session.commit()
            return _finish_cover(
                batch, session, user, aggregate, archive, plan, tuple(documents)
            )
        except Exception as exc:
            session.rollback()
            return BatchSupplement(
                documents=tuple(documents),
                failures=(FinalizationFailure(plan.name, import_failure_code(exc)),),
            )


def _finish_cover(
    batch: BatchSession,
    session: Session,
    user: User,
    aggregate: MultipartModel,
    archive: Path,
    plan: ArchiveIntake,
    documents: tuple[ImportedDocument, ...],
) -> BatchSupplement:
    receipt = _receipt(session, batch.context)
    if (
        not isinstance(plan, MultipartIntake)
        or plan.cover is None
        or receipt.get("cover_complete")
    ):
        return BatchSupplement(documents=documents, multipart_model_id=aggregate.id)
    try:
        precondition = EditPrecondition(
            if_match=receipt["cover_base"], contract="conditional-v1"
        )
        session.commit()
        with closing(
            importer.iter_archive_entries(
                archive,
                [plan.cover],
                auxiliary=True,
                owner=scratch_windows.JobWindowOwner(
                    batch.context.job_id, batch.context.execution_epoch
                ),
                session_factory=batch.factory,
            )
        ) as entries:
            found = next(entries, None)
            if found is None:
                raise OperationError("archive_cover_missing")
            entry, (staged, _) = found
            content_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(Path(entry.name).suffix.lower())
            with staged.open("rb") as stream:
                processed = process_source_cover_upload(
                    stream.read(MAX_SOURCE_COVER_BYTES + 1), content_type
                )

        def record(session: Session) -> None:
            value = _receipt(session, batch.context)
            value["cover_complete"] = True
            _store(session, batch.context, value)

        multipart_covers.publish(
            session, user, aggregate, processed, precondition, before_commit=record
        )
        return BatchSupplement(documents=documents, multipart_model_id=aggregate.id)
    except Exception as exc:
        session.rollback()
        return BatchSupplement(
            documents=documents,
            multipart_model_id=aggregate.id,
            failures=(FinalizationFailure(plan.cover, import_failure_code(exc)),),
        )
