"""Real local Artifact bytes survive resumable geometric analysis unchanged."""

import hashlib
import io
import json

import pytest
from sqlmodel import select

from app.db.models import (
    FileType,
    GeometryFingerprint,
    SimilarityCandidate,
    SimilarityRun,
    ThumbnailRenderSlot,
)
from app.db.session import get_session_factory
from app.modules.similarity import configuration, runs
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories.geometry import tetrahedron


@pytest.fixture
def local_pair(db_session, make_model, make_file, make_user, local_storage):
    actor = make_user(superuser=True)
    configuration.update_settings(
        db_session, actor, {"enabled": True, "sample_points": 256}
    )
    backend = get_backend()
    files = []
    for index in range(2):
        mesh = tetrahedron()
        if index:
            mesh.apply_translation([35, -8, 2])
        content = mesh.export(file_type="stl")
        file = make_file(
            make_model(),
            file_type=FileType.STL,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        file.path = backend.blob_key(
            file.model.slug, file.version, file.original_filename
        )
        db_session.add(file)
        db_session.commit()
        backend.write_stream(io.BytesIO(content), file.path)
        files.append(file)
    return actor, files


class TestProcessing:
    def test_preserves_source_bytes_during_analysis(
        self, db_session, local_pair, make_file
    ):
        actor, files = local_pair
        revision = make_file(files[0].model, file_type=FileType.GCODE, recommended=True)
        run = runs.start(db_session, actor)
        # Construct a fresh processor each time, as after process restarts. The
        # durable database checkpoint, not an in-memory iterator, owns progress.
        for _ in range(35):
            SimilarityProcessor(get_session_factory(), get_backend()).work_one()
            db_session.expire_all()
            if db_session.get(SimilarityRun, run.id).state in runs.TERMINAL:
                break
        db_session.refresh(run)
        assert run.state == "completed", run.failure_code
        candidates = db_session.exec(select(SimilarityCandidate)).all()
        assert len(candidates) == 1, (
            run.counters_json,
            [
                (fp.state, fp.failure_code)
                for fp in db_session.exec(select(GeometryFingerprint)).all()
            ],
        )
        assert candidates[0].evidence_class == "identical_geometry"
        assert candidates[0].confidence == 1.0
        assert json.loads(run.counters_json)["ready"] == 2
        for file in files:
            assert (
                hashlib.sha256(get_backend().read_bytes(file.path)).hexdigest()
                == file.sha256
            )
        db_session.refresh(revision)
        assert revision.model_id == files[0].model_id
        assert revision.is_recommended
        assert all(
            row.lease_token is None
            for row in db_session.exec(select(ThumbnailRenderSlot))
        )

    def test_cancel_stops_before_next_mesh(self, db_session, local_pair):
        actor, _ = local_pair
        run = runs.start(db_session, actor)
        processor = SimilarityProcessor(get_session_factory(), get_backend())
        processor.work_one()
        db_session.refresh(run)
        assert json.loads(run.counters_json)["ready"] == 1
        runs.cancel(db_session, actor, run.id)
        processor.work_one()
        db_session.refresh(run)
        assert run.state == "cancelled"
        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 2

    def test_waits_for_shared_render_budget(self, db_session, local_pair):
        from app.core.config import settings
        from app.modules.media.compute_slots import acquire

        actor, _ = local_pair
        for index in range(settings.max_render_jobs):
            assert acquire(db_session, f"thumbnail-{index}") is not None
        run = runs.start(db_session, actor)
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert json.loads(run.checkpoint_json) == {}
        assert db_session.exec(select(GeometryFingerprint)).one().state == "pending"


class TestDisabledCancellation:
    def test_drains_cancel_request_after_disabling(self, db_session, local_pair):
        from app.db.session import get_session_factory
        from app.modules.similarity import runs
        from app.modules.similarity.configuration import update_settings
        from app.modules.similarity.processing import SimilarityProcessor

        actor, _files = local_pair
        backend = get_backend()
        run = runs.start(db_session, actor, scope="library")
        runs.cancel(db_session, actor, run.id)
        update_settings(db_session, actor, {"enabled": False})
        assert SimilarityProcessor(get_session_factory(), backend).work_one() is True
        db_session.refresh(run)
        assert run.state == "cancelled"
        assert run.active_scope_key is None


class TestProcessingFailures:
    @pytest.mark.parametrize("owner", ["inactive", "absent", "permission_lost"])
    def test_stops_when_scope_owner_is_unavailable(self, db_session, local_pair, owner):
        actor, _ = local_pair
        run = runs.start(
            db_session, actor, scope="models", ids=[local_pair[1][0].model_id]
        )
        if owner == "absent":
            run.actor_id = None
        elif owner == "inactive":
            actor.is_active = False
        else:
            actor.is_superuser = False
        db_session.add_all([actor, run])
        db_session.commit()
        assert SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert run.state == "failed"
        assert run.failure_code == (
            "scope_permission_changed"
            if owner == "permission_lost"
            else "actor_unavailable"
        )
        assert run.active_scope_key is None
        assert db_session.exec(select(SimilarityCandidate)).all() == []

    def test_uses_previously_committed_fingerprints(self, db_session, local_pair):
        actor, _ = local_pair
        run = runs.start(db_session, actor)
        processor = SimilarityProcessor(get_session_factory(), get_backend())
        processor.work_one()
        db_session.refresh(run)
        run.checkpoint_json = "{}"
        db_session.add(run)
        db_session.commit()
        processor.work_one()
        db_session.refresh(run)
        assert json.loads(run.counters_json)["cached"] == 1
        whole = db_session.exec(
            select(GeometryFingerprint).where(GeometryFingerprint.component_index == 0)
        ).one()
        assert whole.attempts == 1
        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 2

    def test_changed_storage_bytes_fail_the_derivative(self, db_session, local_pair):
        from app.modules.storage import artifact_content

        actor, files = local_pair
        with artifact_content.resolve(
            files[0], backend=get_backend()
        ).materialize() as path:
            path.write_bytes(b"changed-source")
        run = runs.start(db_session, actor)
        SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert json.loads(run.counters_json)["failed"] == 1
        fp = db_session.exec(select(GeometryFingerprint)).one()
        assert fp.failure_code == "source_changed"
        assert all(
            row.lease_token is None
            for row in db_session.exec(select(ThumbnailRenderSlot))
        )

    def test_cancels_between_committed_verification_pairs(self, db_session, local_pair):
        actor, _ = local_pair
        run = runs.start(db_session, actor)
        processor = SimilarityProcessor(get_session_factory(), get_backend())
        for _ in range(35):
            processor.work_one()
            db_session.refresh(run)
            if json.loads(run.counters_json).get("verified", 0):
                break
        assert json.loads(run.counters_json)["verified"] > 0
        assert json.loads(run.checkpoint_json)["pending_pairs"]
        before = [
            (row.id, row.version)
            for row in db_session.exec(select(SimilarityCandidate))
        ]
        runs.cancel(db_session, actor, run.id)
        processor.work_one()
        db_session.refresh(run)
        assert run.state == "cancelled"
        assert [
            (row.id, row.version)
            for row in db_session.exec(select(SimilarityCandidate))
        ] == before


class TestPairRecovery:
    @pytest.mark.parametrize(
        "failure",
        [
            "source_hash",
            "source_bytes",
            "missing_content",
            "missing_component",
            "missing_fingerprint",
            "same_model",
        ],
    )
    def test_discards_unusable_pair_without_publishing(
        self, db_session, local_pair, failure
    ):
        from app.modules.storage import artifact_content

        actor, files = local_pair
        run = runs.start(db_session, actor)
        worker = SimilarityProcessor(get_session_factory(), get_backend())
        worker.work_one()
        worker.work_one()
        fingerprints = db_session.exec(
            select(GeometryFingerprint)
            .where(GeometryFingerprint.component_index == 0)
            .order_by(GeometryFingerprint.id)
        ).all()
        assert len(fingerprints) == 2
        pair = [row.id for row in fingerprints]
        if failure == "source_hash":
            files[1].sha256 = "e" * 64
            db_session.add(files[1])
        elif failure in ("source_bytes", "missing_content"):
            with artifact_content.resolve(
                files[1], backend=get_backend()
            ).materialize() as path:
                if failure == "source_bytes":
                    path.write_bytes(b"changed source")
                else:
                    path.unlink()
        elif failure == "missing_component":
            fingerprints[1].component_index = 999
            db_session.add(fingerprints[1])
        elif failure == "missing_fingerprint":
            pair[1] = 999999
        else:
            pair[1] = pair[0]
        db_session.refresh(run)
        run.phase = "candidates"
        run.checkpoint_json = json.dumps({"pending_pairs": [pair]})
        db_session.add(run)
        db_session.commit()
        assert worker.work_one()
        db_session.refresh(run)
        assert run.state == "running"
        assert json.loads(run.checkpoint_json)["pending_pairs"] == []
        counts = json.loads(run.counters_json)
        if failure in ("source_hash", "source_bytes"):
            assert counts["stale"] == 1
        elif failure in ("missing_content", "missing_component"):
            assert counts["verification_failed"] == 1
        assert counts.get("verified", 0) == 0
        assert db_session.exec(select(SimilarityCandidate)).all() == []
        assert all(
            row.lease_token is None
            for row in db_session.exec(select(ThumbnailRenderSlot))
        )

    def test_retries_pair_after_render_capacity_returns(self, db_session, local_pair):
        from app.core.config import settings
        from app.modules.media import compute_slots

        actor, _ = local_pair
        run = runs.start(db_session, actor)
        worker = SimilarityProcessor(get_session_factory(), get_backend())
        for _ in range(4):
            worker.work_one()
        db_session.refresh(run)
        before = json.loads(run.checkpoint_json)
        assert before["pending_pairs"]
        for index in range(settings.max_render_jobs):
            assert compute_slots.acquire(db_session, f"preview-{index}") is not None
        assert worker.work_one()
        db_session.refresh(run)
        assert json.loads(run.checkpoint_json) == before
        assert db_session.exec(select(SimilarityCandidate)).all() == []


class TestUnexpectedAnalysisFailure:
    @pytest.mark.parametrize("failure", ["busy", "forbidden", "unexpected"])
    def test_preserves_checkpoint_when_engine_fails(
        self, db_session, local_pair, monkeypatch, failure
    ):
        from app.core.errors import ErrorKind, OperationError
        from app.modules.media.thumbnail_engine import ThumbnailEngine

        actor, _ = local_pair

        def refuse(*args, **kwargs):
            if failure == "unexpected":
                raise ValueError("private/source/path")
            raise OperationError(
                "private/source/path",
                kind=ErrorKind.BUSY if failure == "busy" else ErrorKind.FORBIDDEN,
            )

        monkeypatch.setattr(ThumbnailEngine, "generate", refuse)
        run = runs.start(db_session, actor)
        result = SimilarityProcessor(get_session_factory(), get_backend()).work_one()
        db_session.refresh(run)
        assert result is (failure != "busy")
        assert run.state == ("running" if failure == "busy" else "failed")
        assert (
            run.failure_code
            == {
                "busy": None,
                "forbidden": "analysis_unavailable",
                "unexpected": "analysis_failed",
            }[failure]
        )
        assert json.loads(run.checkpoint_json) == {}
        assert "private" not in run.counters_json
        assert db_session.exec(select(SimilarityCandidate)).all() == []
        assert all(
            row.lease_token is None
            for row in db_session.exec(select(ThumbnailRenderSlot))
        )


@pytest.fixture
def reanalysis_source(db_session, local_storage, make_user, make_model, make_file):
    from tests.paths import TESTDATA_DIR

    actor = make_user(superuser=True)
    configuration.update_settings(db_session, actor, {"enabled": True})
    content = (TESTDATA_DIR / "Calibration Cube.stl").read_bytes()
    file = make_file(
        make_model(),
        file_type=FileType.STL,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    file.path = get_backend().blob_key(
        file.model.slug, file.version, file.original_filename
    )
    get_backend().write_stream(io.BytesIO(content), file.path)
    db_session.add(file)
    db_session.commit()
    return actor, file


class TestIncompleteReanalysis:
    @pytest.mark.parametrize("state", ["failed", "unsupported", "partial"])
    def test_manual_run_retries_incomplete_geometry(
        self, db_session, reanalysis_source, make_geometry_fingerprint, state
    ):
        actor, file = reanalysis_source
        fingerprint = make_geometry_fingerprint(file, state=state, attempts=1)
        run = runs.start(db_session, actor)

        assert SimilarityProcessor(get_session_factory(), get_backend()).work_one()

        db_session.refresh(fingerprint)
        db_session.refresh(run)
        assert fingerprint.state == "ready"
        assert fingerprint.attempts == 2
        assert json.loads(run.counters_json)["ready"] == 1
        assert (
            hashlib.sha256(get_backend().read_bytes(file.path)).hexdigest()
            == file.sha256
        )

    @pytest.mark.parametrize("state", ["failed", "unsupported", "partial"])
    def test_scheduled_run_keeps_cached_incomplete_geometry(
        self, db_session, reanalysis_source, make_geometry_fingerprint, state
    ):
        actor, file = reanalysis_source
        fingerprint = make_geometry_fingerprint(file, state=state, attempts=1)
        run = runs.start(db_session, actor, trigger="scheduled")

        assert SimilarityProcessor(get_session_factory(), get_backend()).work_one()

        db_session.refresh(fingerprint)
        db_session.refresh(run)
        assert fingerprint.state == state
        assert fingerprint.attempts == 1
        assert json.loads(run.counters_json)["cached"] == 1
