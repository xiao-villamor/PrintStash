"""Enrichment retains the active Vault until its derivative publication finishes."""

import hashlib
import shutil

import pytest
from sqlmodel import select

from app.db.models import ArtifactAnalysisGeneration, FileType, Metadata
from app.modules.ingestion.ingestion import persist_artifact
from app.modules.media import enrichment
from app.modules.media.thumbnail_engine import (
    ThumbnailFailureReason,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.storage.storage_backend import generations
from app.runtime.enrichment import process_one
from tests.paths import TESTDATA_DIR


class TestProcessOne:
    @pytest.mark.parametrize("busy", [True, False], ids=["busy", "failure"])
    def test_releases_admission_after_processor_errors(
        self, db_session, monkeypatch, busy
    ):
        from app.core.errors import ErrorKind, OperationError
        from app.runtime import maintenance

        def fail(_processor):
            raise OperationError(
                "processor_unavailable",
                kind=ErrorKind.BUSY if busy else ErrorKind.CONFLICT,
            )

        monkeypatch.setattr(enrichment.EnrichmentProcessor, "work_one", fail)
        if busy:
            assert process_one() is False
        else:
            with pytest.raises(OperationError, match="processor_unavailable"):
                process_one()
        maintenance.begin_restore_maintenance()
        maintenance.end_restore_maintenance()
        assert maintenance.begin_destructive_operation()
        maintenance.end_destructive_operation()

    def test_prevents_vault_activation_during_derivative_publication(
        self, db_session, make_model, tmp_path, monkeypatch, local_storage
    ):
        source = tmp_path / "cube.stl"
        shutil.copyfile(TESTDATA_DIR / "Calibration Cube.stl", source)
        file = persist_artifact(
            db_session,
            model=make_model(),
            staged_path=source,
            original_filename=source.name,
            file_type=FileType.STL,
            blob_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
            meta={},
            thumb_bytes=None,
            overwrite_thumbnail=True,
        )
        blocked = []

        class Engine:
            def generate(self, request):
                with pytest.raises(TimeoutError, match="vault_reader_drain_timeout"):
                    with generations.activation(timeout=0):
                        raise AssertionError("active enrichment lost its Vault")
                blocked.append(request.path.exists())
                return ThumbnailResult(
                    image=None,
                    geometry={"triangle_count": 12},
                    strategy=ThumbnailStrategy.NONE,
                    complete=False,
                    failure_reason=ThumbnailFailureReason.RENDERER_NO_OUTPUT,
                    duration_ms=0,
                    peak_rss_bytes=None,
                )

        monkeypatch.setattr(enrichment, "ThumbnailEngine", Engine)

        assert process_one() is True

        db_session.expire_all()
        assert blocked == [True]
        assert (
            db_session.exec(select(ArtifactAnalysisGeneration.state)).one() == "ready"
        )
        assert (
            db_session.exec(select(Metadata).where(Metadata.file_id == file.id))
            .one()
            .triangle_count
            == 12
        )
        with generations.activation(timeout=0):
            assert generations.has_readers(generations.current_epoch()) is False

    @pytest.mark.parametrize("gate", ["foreground", "restore"])
    def test_defers_behind_foreground_maintenance(self, gate):
        from app.runtime import maintenance

        if gate == "foreground":
            assert maintenance.begin_mutating_operation(foreground=True)
        else:
            maintenance.begin_restore_maintenance()
        try:
            assert process_one() is False
        finally:
            if gate == "foreground":
                maintenance.end_mutating_operation(foreground=True)
            else:
                maintenance.end_restore_maintenance()
        assert maintenance.restore_in_progress() is False
        assert maintenance.foreground_mutations_pending() is False

    def test_defers_while_accepted_sources_await_persistence(self, db_session):
        from app.db.models import BackgroundJob
        from app.modules.ingestion.commands import enqueue
        from app.runtime.jobs import registry

        job_id = registry.create(session=db_session)
        enqueue(db_session, job_id, "artifact", {})
        db_session.commit()

        assert process_one() is False

        db_session.expire_all()
        source = db_session.get(BackgroundJob, job_id)
        assert source.state == "pending"
        assert source.claim_token is None
