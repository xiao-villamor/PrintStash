"""Canonical Artifact and provenance commits include fenced batch receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from sqlmodel import select

from app.core.cancellation import OperationCancelled
from app.db.models import (
    File,
    FileType,
    InboxItemState,
    IngestionEntry,
    IngestionEntryState,
    JobKind,
    Metadata,
    ModelProvenanceField,
    ProvenanceCapture,
    User,
)
from app.db.session import get_session_factory
from app.modules.ingestion import batch_store, ingestion
from app.modules.ingestion.batch_contracts import (
    BatchCommitReference,
    EntrySpec,
    InboxBatch,
    JobBatch,
    LocalSource,
)
from app.modules.library import provenance
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import service
from app.modules.work.contracts import JobContext, JobExecution
from tests.factories.capture import capture_source
from tests.factories.geometry import tetrahedron
from tests.factories.ops import build_job_context


@dataclass(frozen=True)
class _Batch:
    context: JobContext
    actor: User
    owner: JobBatch
    reference: BatchCommitReference
    entry_key: str
    ingestion_key: str
    artifact: ingestion.StagedArtifact
    body: bytes


@pytest.fixture
def batch(make_user, make_job, tmp_path, local_storage):
    actor = make_user(superuser=True)
    job = make_job(owner=actor)
    ctx = build_job_context(job.id)
    owner = JobBatch(job.id)
    body = tetrahedron().export(file_type="stl")
    source = tmp_path / "part.stl"
    source.write_bytes(body)
    spec = EntrySpec("source-file", source.name, LocalSource("snapshot"), len(body))
    record = batch_store.freeze_entries(ctx, owner, (spec,))[0]
    reference = BatchCommitReference(
        owner, record.id, JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch)
    )
    return _Batch(
        ctx,
        actor,
        owner,
        reference,
        record.key,
        record.ingestion_key,
        ingestion.StagedArtifact(source, source.name, "Atomic part", FileType.STL),
        body,
    )


def _capture(batch: _Batch, revision: str) -> provenance.ProvenanceContext:
    manifest = provenance.CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": capture_source(
                provider="printables",
                canonical_url="https://printables.com/model/42",
                source_item_id="42",
                source_revision=revision,
                fields={"title": {"value": revision, "origin": "confirmed"}},
            ),
            "files": [
                {
                    "id": "42:file",
                    "name": "part.stl",
                    "file_type": "stl",
                    "size": len(batch.body),
                }
            ],
            "selected_ids": ["42:file"],
        }
    )
    return provenance.ProvenanceContext(
        manifest,
        "42:file",
        "part.stl",
        source_selection_id="42:file",
        actor_id=batch.actor.id,
    )


@pytest.fixture
def captured_batch(batch):
    existing = ingestion.commit_staged_artifact(
        batch.artifact,
        ingestion_key=f"original-{batch.context.job_id}",
        actor_user_id=batch.actor.id,
        provenance_context=_capture(batch, "r1"),
    )
    batch.artifact.staged_path.write_bytes(batch.body)
    return batch, existing


def _commit(batch: _Batch, **kwargs):
    return ingestion.commit_staged_artifact(
        batch.artifact,
        ingestion_key=batch.ingestion_key,
        actor_user_id=batch.actor.id,
        batch_commit=batch.reference,
        **kwargs,
    )


def _facts():
    with get_session_factory().scoped_session() as session:
        captures = session.exec(
            select(ProvenanceCapture).order_by(ProvenanceCapture.id)
        ).all()
        fields = session.exec(
            select(ModelProvenanceField).order_by(ModelProvenanceField.id)
        ).all()
        return (
            tuple((row.id, row.snapshot_sha256, row.snapshot_json) for row in captures),
            tuple(
                (row.id, row.captured_value_json, row.user_value_json) for row in fields
            ),
        )


class _ConfirmationProbe:
    """Observe real flushed domain rows without substituting a business outcome."""

    def __init__(self, fail=False):
        self.original = batch_store.record_committed
        self.fail = fail
        self.snapshots = []
        self.published_keys = []

    def __call__(self, session, reference, file, *, deduplicated):
        self.original(session, reference, file, deduplicated=deduplicated)
        metadata = session.exec(
            select(Metadata).where(Metadata.file_id == file.id)
        ).one()
        receipt = session.get(IngestionEntry, reference.entry_id)
        assert receipt is not None
        self.snapshots.append(
            (file.id, metadata.file_id, receipt.file_id, receipt.state)
        )
        self.published_keys.append(file.path)
        assert get_backend().read_bytes(file.path)
        if self.fail:
            raise RuntimeError("entry_confirmation_failure")


def _lost_acknowledgement(stage, _key):
    if stage == "after_commit":
        raise OSError("commit_acknowledgement_lost")


def _reject_source_read(*_args, **_kwargs):
    raise AssertionError("confirmed_source_was_read")


class TestAtomicBatchCommit:
    def test_commits_entry_with_file_metadata(self, batch, monkeypatch):
        probe = _ConfirmationProbe()
        monkeypatch.setattr(batch_store, "record_committed", probe)

        outcome = _commit(batch)

        assert probe.snapshots == [
            (
                outcome.file_id,
                outcome.file_id,
                outcome.file_id,
                IngestionEntryState.IMPORTED,
            )
        ]
        with get_session_factory().scoped_session() as session:
            file = session.get(File, outcome.file_id)
            metadata = session.exec(
                select(Metadata).where(Metadata.file_id == outcome.file_id)
            ).one()
            entry = session.get(IngestionEntry, batch.reference.entry_id)
            assert file is not None and entry is not None
            assert (file.id, metadata.file_id, entry.file_id) == (outcome.file_id,) * 3
            assert entry.state is IngestionEntryState.IMPORTED
            assert get_backend().read_bytes(file.path) == batch.body

    def test_rolls_back_artifact_when_entry_confirmation_fails(
        self, batch, monkeypatch
    ):
        probe = _ConfirmationProbe(fail=True)
        monkeypatch.setattr(batch_store, "record_committed", probe)

        with pytest.raises(RuntimeError, match="entry_confirmation_failure"):
            _commit(batch)

        assert len(probe.snapshots) == 1
        with get_session_factory().scoped_session() as session:
            assert session.get(File, probe.snapshots[0][0]) is None
            assert (
                session.exec(
                    select(Metadata).where(Metadata.file_id == probe.snapshots[0][0])
                ).all()
                == []
            )
            entry = session.get(IngestionEntry, batch.reference.entry_id)
            assert entry is not None and entry.state is IngestionEntryState.PENDING
        assert not get_backend().exists(probe.published_keys[0])

    def test_resumes_commit_after_lost_acknowledgement_without_source_reads(
        self, batch, monkeypatch
    ):
        with monkeypatch.context() as fault:
            fault.setattr(
                ingestion, "_fault_injection_checkpoint", _lost_acknowledgement
            )
            with pytest.raises(OSError, match="commit_acknowledgement_lost"):
                _commit(batch)
        confirmed = batch_store.confirmed(batch.context, batch.owner, batch.entry_key)
        assert confirmed is not None and confirmed.published
        assert not batch.artifact.staged_path.exists()
        monkeypatch.setattr(ingestion, "sha256_file", _reject_source_read)

        resumed = _commit(batch)

        assert resumed.resumed and resumed.file_id == confirmed.file_id
        assert batch_store.counts(batch.owner).succeeded == 1
        with get_session_factory().scoped_session() as session:
            files = session.exec(
                select(File).where(File.ingestion_key == batch.ingestion_key)
            ).all()
            assert len(files) == 1
            assert (
                session.exec(
                    select(Metadata).where(Metadata.file_id == resumed.file_id)
                )
                .one()
                .file_id
                == resumed.file_id
            )
            assert get_backend().read_bytes(files[0].path) == batch.body

    def test_commits_deduplicated_entry_with_updated_provenance(
        self, captured_batch, monkeypatch
    ):
        batch, existing = captured_batch
        before = _facts()
        probe = _ConfirmationProbe()
        monkeypatch.setattr(batch_store, "record_committed", probe)

        outcome = _commit(batch, provenance_context=_capture(batch, "r2"))

        assert outcome.deduplicated and outcome.file_id == existing.file_id
        assert probe.snapshots == [
            (
                existing.file_id,
                existing.file_id,
                existing.file_id,
                IngestionEntryState.DEDUPLICATED,
            )
        ]
        assert len(_facts()[0]) == len(before[0]) + 1
        # Portable merge retains the existing effective fields while recording
        # the new source snapshot; that established contract is intentional.
        assert _facts()[1] == before[1]
        snapshot = json.loads(_facts()[0][-1][2])
        assert snapshot["source_revision"] == "r2"
        assert snapshot["fields"]["title"]["value"] == "r2"
        confirmed = batch_store.confirmed(batch.context, batch.owner, batch.entry_key)
        assert (
            confirmed is not None
            and confirmed.state is IngestionEntryState.DEDUPLICATED
        )
        with get_session_factory().scoped_session() as session:
            assert (
                session.exec(select(File).where(File.model_id == existing.model_id))
                .one()
                .id
                == existing.file_id
            )

    def test_rolls_back_recapture_when_entry_confirmation_fails(
        self, captured_batch, monkeypatch
    ):
        batch, existing = captured_batch
        before = _facts()
        probe = _ConfirmationProbe(fail=True)
        monkeypatch.setattr(batch_store, "record_committed", probe)

        with pytest.raises(RuntimeError, match="entry_confirmation_failure"):
            _commit(batch, provenance_context=_capture(batch, "r2"))

        assert _facts() == before
        assert (
            batch_store.results(batch.owner, limit=1)[0].state
            is IngestionEntryState.PENDING
        )
        with get_session_factory().scoped_session() as session:
            file = session.get(File, existing.file_id)
            assert file is not None
            assert get_backend().read_bytes(file.path) == batch.body
            assert (
                session.exec(select(Metadata).where(Metadata.file_id == file.id))
                .one()
                .file_id
                == file.id
            )

    def test_refuses_stale_job_epoch_before_provenance_publication(
        self, captured_batch
    ):
        batch, existing = captured_batch
        before = _facts()
        service.cancel(batch.context.job_id, actor=batch.actor)
        service.retry(batch.context.job_id, actor=batch.actor)
        current = build_job_context(batch.context.job_id)
        assert current.execution_epoch != batch.reference.execution.execution_epoch

        with pytest.raises(OperationCancelled):
            _commit(batch, provenance_context=_capture(batch, "r2"))

        assert _facts() == before
        assert (
            batch_store.results(batch.owner, limit=1)[0].state
            is IngestionEntryState.PENDING
        )
        with get_session_factory().scoped_session() as session:
            file = session.get(File, existing.file_id)
            assert (
                file is not None and get_backend().read_bytes(file.path) == batch.body
            )

    def test_refuses_replaced_inbox_job_before_provenance_publication(
        self, captured_batch, make_job, make_inbox_item, db_session
    ):
        batch, existing = captured_batch
        before = _facts()
        original = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            owner=batch.actor,
            subject="original-inbox",
        )
        ctx = build_job_context(original.id)
        item = make_inbox_item(
            batch.actor, state=InboxItemState.IMPORTING, job_id=original.id
        )
        owner = InboxBatch(item.id)
        spec = EntrySpec(
            "source-file", "part.stl", LocalSource("snapshot"), len(batch.body)
        )
        entry = batch_store.freeze_entries(ctx, owner, (spec,))[0]
        reference = BatchCommitReference(
            owner, entry.id, JobExecution(ctx.job_id, ctx.attempt, ctx.execution_epoch)
        )
        replacement = make_job(
            kind=JobKind.INGESTION_INBOX_IMPORT,
            owner=batch.actor,
            subject="replacement-inbox",
        )
        item.job_id = replacement.id
        db_session.add(item)
        db_session.commit()

        with pytest.raises(OperationCancelled):
            ingestion.commit_staged_artifact(
                batch.artifact,
                ingestion_key=entry.ingestion_key,
                actor_user_id=batch.actor.id,
                provenance_context=_capture(batch, "r2"),
                batch_commit=reference,
            )

        assert _facts() == before
        assert (
            batch_store.results(owner, limit=1)[0].state is IngestionEntryState.PENDING
        )
        with get_session_factory().scoped_session() as session:
            file = session.get(File, existing.file_id)
            assert (
                file is not None and get_backend().read_bytes(file.path) == batch.body
            )
