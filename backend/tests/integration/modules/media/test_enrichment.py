"""Saved Artifacts remain usable while independent derived outputs are produced."""

import hashlib
import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from app.db.models import (
    ArtifactAnalysisGeneration,
    File,
    FileType,
    Metadata,
    ThumbnailGeneration,
)
from app.db.session import get_session_factory
from app.modules.ingestion.ingestion import persist_artifact
from app.modules.media.enrichment import EnrichmentProcessor
from app.modules.media.thumbnail_engine import (
    ThumbnailFailureReason,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.storage.storage_backend.runtime import get_backend
from tests.paths import TESTDATA_DIR


@pytest.fixture
def stored_mesh(db_session, make_model, tmp_path, local_storage):
    source = tmp_path / "cube.stl"
    shutil.copyfile(TESTDATA_DIR / "Calibration Cube.stl", source)
    return persist_artifact(
        db_session,
        model=make_model("Cube"),
        staged_path=source,
        original_filename=source.name,
        file_type=FileType.STL,
        blob_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        meta={},
        thumb_bytes=None,
        overwrite_thumbnail=True,
    )


class _MissingPreview:
    def generate(self, request):
        return ThumbnailResult(
            image=None,
            geometry={"bbox_x_mm": 20, "triangle_count": 12},
            strategy=ThumbnailStrategy.NONE,
            complete=False,
            failure_reason=ThumbnailFailureReason.RENDERER_NO_OUTPUT,
            duration_ms=1,
            peak_rss_bytes=None,
        )


class TestEnrichmentProcessor:
    def test_defers_enrichment_while_another_consumer_owns_the_compute_permit(
        self, db_session, stored_mesh, monkeypatch
    ):
        from app.core.config import _overlay
        from app.modules.media import compute_slots

        monkeypatch.setitem(_overlay, "max_render_jobs", 1)
        permit = compute_slots.acquire(db_session, "similarity-worker")
        assert permit is not None
        processor = EnrichmentProcessor(get_session_factory(), get_backend())

        assert processor.work_one() is False

        db_session.expire_all()
        generation = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        assert (generation.state, generation.error_code, generation.attempts) == (
            "pending",
            "compute_busy",
            0,
        )
        assert db_session.get(File, stored_mesh.id).thumbnail_path is None

    def test_generates_outputs_from_a_saved_source(self, db_session, stored_mesh):
        processor = EnrichmentProcessor(get_session_factory(), get_backend())

        assert processor.work_one() is True
        db_session.expire_all()
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == stored_mesh.id)
        ).one()
        generation = db_session.exec(select(ThumbnailGeneration)).one()
        assert metadata.triangle_count > 0
        assert metadata.bbox_x_mm > 0
        assert generation.state == "ready"
        assert get_backend().exists(generation.storage_key)
        assert (
            db_session.exec(select(ArtifactAnalysisGeneration.state)).one() == "ready"
        )
        assert processor.work_one() is False

    def test_keeps_geometry_when_preview_generation_fails(
        self, db_session, stored_mesh
    ):
        processor = EnrichmentProcessor(
            get_session_factory(), get_backend(), engine=_MissingPreview()
        )

        processor.work_one()

        db_session.expire_all()
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == stored_mesh.id)
        ).one()
        assert metadata.bbox_x_mm == 20
        assert (
            db_session.exec(select(ArtifactAnalysisGeneration.state)).one() == "ready"
        )
        assert db_session.exec(select(ThumbnailGeneration.state)).one() == "failed"
        assert get_backend().exists(db_session.get(File, stored_mesh.id).path)

    def test_leaves_source_available_before_processing(self, db_session, stored_mesh):
        assert get_backend().exists(stored_mesh.path)
        assert stored_mesh.thumbnail_path is None
        assert (
            db_session.exec(select(ArtifactAnalysisGeneration.state)).one() == "pending"
        )
        assert db_session.exec(select(ThumbnailGeneration.state)).one() == "pending"

    def test_returns_idle_without_pending_sources(self):
        assert (
            EnrichmentProcessor(get_session_factory(), get_backend()).work_one()
            is False
        )


class TestEnrichmentContract:
    @pytest.mark.parametrize("source_state", ["offline", "replaced"])
    def test_unavailable_external_source_cannot_publish_old_derivatives(
        self, db_session, make_model, make_file, tmp_path, source_state
    ):
        from app.core.time import ensure_utc, utcnow
        from app.modules.media.analysis_generations import request_enrichment

        replacement = tmp_path / "nas" / "replaced.stl"
        replacement.parent.mkdir()
        replacement.write_bytes(b"different geometry")
        source = {
            "offline": tmp_path / "offline" / "cube.stl",
            "replaced": replacement,
        }[source_state]
        file = make_file(
            make_model(),
            path=str(source),
            external=True,
            file_type=FileType.STL,
            size_bytes=100,
            sha256=hashlib.sha256(b"accepted geometry").hexdigest(),
        )
        request_enrichment(db_session, file, promote_thumbnail=True)
        db_session.commit()
        processor = EnrichmentProcessor(get_session_factory(), get_backend())

        assert processor.work_one() is True

        db_session.expire_all()
        analysis = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        assert analysis.state == "pending"
        assert analysis.error_code == "source_unavailable"
        assert ensure_utc(analysis.next_attempt_at) > utcnow()
        assert db_session.get(File, file.id).thumbnail_path is None
        assert db_session.exec(select(ThumbnailGeneration)).one().storage_key is None
        assert processor.work_one() is False

    def test_unavailable_source_backs_off_without_publishing(
        self, db_session, stored_mesh
    ):
        from app.core.time import ensure_utc, utcnow

        Path(stored_mesh.path).unlink()
        processor = EnrichmentProcessor(get_session_factory(), get_backend())
        assert processor.work_one() is True
        db_session.expire_all()
        analysis = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        assert analysis.state == "pending"
        assert analysis.error_code == "source_unavailable"
        assert ensure_utc(analysis.next_attempt_at) > utcnow()
        assert db_session.get(File, stored_mesh.id).thumbnail_path is None
        assert db_session.exec(select(ThumbnailGeneration)).one().storage_key is None
        assert processor.work_one() is False

    def test_preview_failure_keeps_a_completed_import_saved(
        self, db_session, stored_mesh, make_background_job
    ):
        from app.db.models import BackgroundJob

        job = make_background_job(
            state="completed", result_json='{"file_id": %d}' % stored_mesh.id
        )
        processor = EnrichmentProcessor(
            get_session_factory(), get_backend(), engine=_MissingPreview()
        )
        processor.work_one()
        db_session.expire_all()
        assert db_session.get(BackgroundJob, job.id).state == "completed"
        assert get_backend().exists(db_session.get(File, stored_mesh.id).path)
        assert db_session.exec(select(ThumbnailGeneration.state)).one() == "failed"


class TestEnrichmentRecovery:
    def test_defers_when_capacity_cannot_be_reserved(
        self, db_session, stored_mesh, monkeypatch
    ):
        from app.core.errors import ErrorKind, OperationError
        from app.db.models import ThumbnailRenderSlot
        from app.modules.storage.capacity import CapacityManager

        def unavailable(*args, **kwargs):
            raise OperationError("storage_capacity_unavailable", kind=ErrorKind.BUSY)

        monkeypatch.setattr(CapacityManager, "reserve", unavailable)
        processor = EnrichmentProcessor(get_session_factory(), get_backend())

        assert processor.work_one() is False

        db_session.expire_all()
        generation = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        assert (generation.state, generation.error_code, generation.attempts) == (
            "pending",
            "capacity_busy",
            0,
        )
        assert get_backend().exists(stored_mesh.path)
        assert all(
            slot.lease_token is None
            for slot in db_session.exec(select(ThumbnailRenderSlot)).all()
        )

    @pytest.mark.parametrize("failure", ["busy", "storage", "unexpected"])
    def test_retains_source_after_execution_error(
        self, db_session, stored_mesh, failure
    ):
        from app.core.errors import ErrorKind, OperationError
        from app.db.models import ThumbnailRenderSlot

        class FailedEngine:
            def generate(self, request):
                if failure == "unexpected":
                    raise RuntimeError("renderer failed")
                raise OperationError(
                    "storage_unavailable",
                    kind=ErrorKind.BUSY if failure == "busy" else ErrorKind.CONFLICT,
                )

        processor = EnrichmentProcessor(
            get_session_factory(), get_backend(), engine=FailedEngine()
        )

        assert processor.work_one() is True

        db_session.expire_all()
        generation = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        assert generation.state == "pending"
        assert generation.error_code == (
            "storage_busy" if failure == "busy" else "enrichment_failed"
        )
        assert generation.attempts == (0 if failure == "busy" else 1)
        assert db_session.get(File, stored_mesh.id).thumbnail_path is None
        assert get_backend().exists(stored_mesh.path)
        assert all(
            slot.lease_token is None
            for slot in db_session.exec(select(ThumbnailRenderSlot)).all()
        )

    def test_rejects_empty_analysis_without_inventing_metadata(
        self, db_session, stored_mesh
    ):
        class EmptyEngine:
            def generate(self, request):
                return ThumbnailResult(
                    image=None,
                    geometry={},
                    strategy=ThumbnailStrategy.NONE,
                    complete=False,
                    failure_reason=ThumbnailFailureReason.NO_GEOMETRY,
                    duration_ms=0,
                    peak_rss_bytes=None,
                )

        processor = EnrichmentProcessor(
            get_session_factory(), get_backend(), engine=EmptyEngine()
        )

        assert processor.work_one() is True

        db_session.expire_all()
        generation = db_session.exec(select(ArtifactAnalysisGeneration)).one()
        assert (generation.state, generation.error_code) == ("failed", "no_geometry")
        assert (
            db_session.exec(select(Metadata).where(Metadata.file_id == stored_mesh.id))
            .one()
            .triangle_count
            is None
        )
        assert get_backend().exists(stored_mesh.path)

    def test_renders_preview_after_metadata_is_ready(self, db_session, stored_mesh):
        from app.modules.media import analysis_generations
        from app.modules.media.thumbnail_engine import ThumbnailEngine

        claim = analysis_generations.claim_next(db_session)
        assert analysis_generations.publish_metadata(
            db_session, claim, {"triangle_count": 123}
        )
        assert analysis_generations.finish_analysis(db_session, claim)
        requests = []

        class PreviewEngine:
            def generate(self, request):
                requests.append(request)
                return ThumbnailEngine().generate(request)

        processor = EnrichmentProcessor(
            get_session_factory(), get_backend(), engine=PreviewEngine()
        )

        assert processor.work_one() is True

        db_session.expire_all()
        assert len(requests) == 1
        assert requests[0].include_geometry is False
        assert requests[0].include_thumbnail is True
        assert (
            db_session.exec(select(Metadata).where(Metadata.file_id == stored_mesh.id))
            .one()
            .triangle_count
            == 123
        )
        preview = db_session.exec(select(ThumbnailGeneration)).one()
        assert preview.state == "ready"
        assert get_backend().exists(preview.storage_key)
