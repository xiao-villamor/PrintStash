"""Producers: read an Artifact's bytes once, derive what is owed, publish each.

A producer derives only the kinds still needed and records every outcome on the
kind's row: ready (with where the output lives), skipped (nothing to produce
for these bytes), or failed, and a failure is transient (backs off) unless
retrying the same bytes could never help. Viewers of the Model are told each
time one of its derivatives changes, so a placeholder turns into a thumbnail
without a reload.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import trimesh
from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
)
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    FileType,
    GeometryFingerprint,
    JobKind,
    JobState,
    Metadata,
    Model,
)
from app.modules.derivatives import kinds, producers
from app.modules.ingestion import extensions
from app.modules.library.volume_metadata import apply_volume, read_volume
from app.modules.media import mesh_isolation, toolpath
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.mesh_protocol import BasicOutput, GeometryOutput, ThumbnailOutput
from app.modules.media.thumbnail_publication import ThumbnailPublicationError
from app.modules.media.worker_bootstrap import command
from app.modules.similarity import ingestion as similarity_ingestion
from app.modules.storage.capacity import CapacityReservation
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work import events, runner, service
from app.modules.work.jobs import jobs
from app.modules.work.submission import submit
from tests.factories import content
from tests.factories.geometry import three_mf
from tests.paths import FIXTURES_DIR


@pytest.fixture
def announced() -> Iterator[list[dict]]:
    notices: list[dict] = []

    class Recorder:
        def publish_threadsafe(self, channel, payload):
            notices.append({"channel": channel, **payload})

    events.bind(Recorder())
    try:
        yield notices
    finally:
        events.bind(None)


@pytest.fixture
def stale_mesh_measurement_reply() -> ThumbnailResult:
    return ThumbnailResult(
        image=None,
        geometry={
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 10.0,
            "bbox_z_mm": 10.0,
            "triangle_count": 12,
            "volume_mm3": 2.0,
        },
        geometry_outcome=GeometryReady(),
        volume=VolumeMeasured(2.0),
        strategy=ThumbnailStrategy.NONE,
        coverage=MeshCoverage(
            SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.NOT_PRODUCED
        ),
        failure_reason=None,
        duration_ms=1,
        peak_rss_bytes=None,
    )


@pytest.fixture
def emit_mesh_outputs():
    """Emit the requested basic outputs at the isolation boundary."""

    def emit(
        request: ThumbnailRequest,
        reply: ThumbnailResult,
        on_output: Callable[[BasicOutput], None],
    ) -> ThumbnailResult:
        if request.include_geometry:
            on_output(
                GeometryOutput(
                    reply.geometry,
                    reply.geometry_outcome,
                    reply.volume,
                    MeshCoverage(
                        reply.coverage.source_scan,
                        reply.coverage.geometry,
                        PreviewCoverage.NOT_PRODUCED,
                    ),
                    reply.duration_ms,
                    reply.peak_rss_bytes,
                )
            )
        if request.include_thumbnail:
            on_output(
                ThumbnailOutput(
                    reply.image,
                    reply.strategy,
                    reply.coverage,
                    reply.failure_reason,
                    reply.duration_ms,
                    reply.peak_rss_bytes,
                )
            )
        return reply

    return emit


class FingerprintSink:
    """Similarity's side of the mesh load: asks for fingerprints, takes them."""

    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.received: list[int] = []

    def extraction_options(self, _sessions):
        return {"include_fingerprint": True}

    def publish_mesh_fingerprint_continuation(self, _session, file, result):
        from app.modules.ingestion.extensions import MeshFingerprintPublished

        if self.broken:
            raise RuntimeError("similarity store unavailable")
        self.received.append(file.id)
        return MeshFingerprintPublished(result.state)

    def after_commit(
        self, _sessions, file_id, _actor_id, _result, *, source_sha256, execution=None
    ):
        if self.broken:
            raise RuntimeError("similarity store unavailable")
        self.received.append(file_id)
        return "stored"


def _rows(session: Session, file_id: int) -> dict[str, ArtifactDerivative]:
    session.expire_all()
    return {
        row.kind: row
        for row in session.exec(
            select(ArtifactDerivative).where(ArtifactDerivative.file_id == file_id)
        ).all()
    }


class TestDeriveMesh:
    def test_required_capability_replaces_old_parser_receipts(
        self,
        db_session,
        stored,
        make_metadata,
        make_derivative,
        make_geometry_fingerprint,
        make_system_config,
        monkeypatch,
    ):
        from app.modules.media.fingerprints import ALGORITHM_VERSION
        from app.modules.media.thumbnail_engine import ThumbnailEngine

        original = three_mf(extras={"Metadata/thumbnail.png": content.png()})
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["3D/3dmodel.model"] = entries["3D/3dmodel.model"].replace(
            b"<model ",
            b'<model xmlns:future="urn:printstash:test:unsupported" requiredextensions="future" ',
            1,
        )
        source = content.zip_bytes(entries)
        artifact = stored("required.3mf", source)
        metadata = make_metadata(
            artifact, bbox_x_mm=10, triangle_count=4, volume=VolumeMeasured(500)
        )
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=9)
        make_derivative(artifact, DerivativeKind.THUMBNAIL, recipe_version=8)
        old = make_geometry_fingerprint(
            artifact, algorithm_version="geometry-v4-sh5f4577c4", state="ready"
        )
        make_system_config(
            similarity_settings_json=json.dumps(
                {"enabled": True, "fingerprint_on_ingest": True}
            )
        )
        monkeypatch.setattr(extensions, "_derivatives", similarity_ingestion)
        monkeypatch.setattr(mesh_isolation, "generate", ThumbnailEngine().generate)

        outcome = producers.derive_mesh(artifact.id)

        db_session.expire_all()
        db_session.refresh(metadata)
        assert outcome.kinds == {
            DerivativeKind.METADATA: DerivativeState.FAILED,
            DerivativeKind.THUMBNAIL: DerivativeState.READY,
        }
        assert metadata.bbox_x_mm is None and metadata.triangle_count is None
        assert read_volume(metadata) == VolumeNotCalculated(
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
        )
        rows = _rows(db_session, artifact.id)
        assert (
            rows[DerivativeKind.METADATA].recipe_version == kinds.MESH_GEOMETRY_RECIPE
        )
        assert rows[DerivativeKind.METADATA].failure_reason == "unsupported_capability"
        assert (
            rows[DerivativeKind.THUMBNAIL].recipe_version == kinds.MESH_THUMBNAIL_RECIPE
        )
        assert (
            json.loads(rows[DerivativeKind.THUMBNAIL].output_json)["strategy"]
            == "embedded"
        )
        assert get_backend().exists(rows[DerivativeKind.THUMBNAIL].storage_key)
        assert get_backend().read_bytes(artifact.path) == source
        cached = db_session.exec(
            select(GeometryFingerprint).where(
                GeometryFingerprint.file_id == artifact.id,
                GeometryFingerprint.algorithm_version == ALGORITHM_VERSION,
            )
        ).one()
        assert cached.algorithm_version == ALGORITHM_VERSION
        assert cached.state == "unsupported"
        assert cached.failure_code == "unsupported_3mf_capability"
        db_session.refresh(old)
        assert old.state == "ready"
        assert old.algorithm_version == "geometry-v4-sh5f4577c4"

    @pytest.mark.parametrize("failure", ["unsupported_capability", "timeout"])
    def test_refusal_withdraws_only_unsupported_geometry_authority(
        self,
        db_session,
        stored,
        make_metadata,
        make_derivative,
        monkeypatch,
        failure,
        emit_mesh_outputs,
    ):
        artifact = stored("required-extension.3mf", three_mf())
        prior = VolumeMeasured(500.0)
        metadata = make_metadata(
            artifact,
            bbox_x_mm=10,
            bbox_y_mm=20,
            bbox_z_mm=30,
            triangle_count=4,
            volume=prior,
        )
        make_derivative(
            artifact,
            DerivativeKind.METADATA,
            recipe_version=kinds.MESH_GEOMETRY_RECIPE - 1,
        )
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        reason = ThumbnailFailureReason(failure)
        reply = ThumbnailResult(
            image=None,
            geometry={
                "bbox_x_mm": None,
                "bbox_y_mm": None,
                "bbox_z_mm": None,
                "triangle_count": None,
                "volume_mm3": None,
            },
            geometry_outcome=GeometryRefused(reason),
            volume=VolumeNotCalculated(VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE),
            strategy=ThumbnailStrategy.NONE,
            coverage=MeshCoverage(
                SourceScanState.NOT_SCANNED,
                GeometryNotLoaded(),
                PreviewCoverage.NOT_PRODUCED,
            ),
            failure_reason=reason,
            duration_ms=1,
            peak_rss_bytes=None,
        )

        def generated(request, *, on_output):
            return emit_mesh_outputs(request, reply, on_output)

        monkeypatch.setattr(mesh_isolation, "generate", generated)
        outcome = producers.derive_mesh(artifact.id)
        db_session.refresh(metadata)
        assert outcome.kinds == {DerivativeKind.METADATA: DerivativeState.FAILED}
        row = _rows(db_session, artifact.id)[DerivativeKind.METADATA]
        assert row.failure_reason == failure
        if failure == "unsupported_capability":
            assert (
                metadata.bbox_x_mm,
                metadata.bbox_y_mm,
                metadata.bbox_z_mm,
                metadata.triangle_count,
            ) == (None, None, None, None)
            assert read_volume(metadata) == VolumeNotCalculated(
                VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE
            )
            assert row.next_attempt_at is None
        else:
            assert (
                metadata.bbox_x_mm,
                metadata.bbox_y_mm,
                metadata.bbox_z_mm,
                metadata.triangle_count,
            ) == (10, 20, 30, 4)
            assert read_volume(metadata) == prior
            assert row.next_attempt_at is not None

    @pytest.mark.parametrize(
        "superseded_by", ["cancel", "source", "recipe", "replacement"]
    )
    def test_unsupported_geometry_withdrawal_respects_publication_fence(
        self,
        db_session,
        stored,
        make_metadata,
        make_derivative,
        monkeypatch,
        superseded_by,
        emit_mesh_outputs,
    ):
        from app.core.time import utcnow
        from app.modules.derivatives import records

        artifact = stored("late-extension.3mf", three_mf())
        prior = VolumeMeasured(500.0)
        metadata = make_metadata(artifact, bbox_x_mm=10, triangle_count=4, volume=prior)
        make_derivative(
            artifact,
            DerivativeKind.METADATA,
            recipe_version=kinds.MESH_GEOMETRY_RECIPE - 1,
        )
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        reason = ThumbnailFailureReason.UNSUPPORTED_CAPABILITY
        reply = ThumbnailResult(
            image=None,
            geometry={
                "bbox_x_mm": None,
                "bbox_y_mm": None,
                "bbox_z_mm": None,
                "triangle_count": None,
                "volume_mm3": None,
            },
            geometry_outcome=GeometryRefused(reason),
            volume=VolumeNotCalculated(VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE),
            strategy=ThumbnailStrategy.NONE,
            coverage=MeshCoverage(
                SourceScanState.NOT_SCANNED,
                GeometryNotLoaded(),
                PreviewCoverage.NOT_PRODUCED,
            ),
            failure_reason=reason,
            duration_ms=1,
            peak_rss_bytes=None,
        )
        replacement = VolumeMeasured(7.0)

        def delayed_refusal(request, *, on_output):
            if superseded_by in ("cancel", "replacement"):
                records.cancel(
                    db_session,
                    artifact,
                    kinds.group(JobKind.DERIVATIVES_MESH).kinds,
                    now=utcnow(),
                )
                db_session.commit()
                if superseded_by == "replacement":
                    row = records.begin(
                        db_session,
                        artifact,
                        DerivativeKind.METADATA,
                        kinds.MESH_GEOMETRY_RECIPE,
                        now=utcnow(),
                    )
                    attempt = records.attempt(db_session, artifact, row)
                    records.mark_ready(db_session, attempt, now=utcnow())
                    apply_volume(metadata, replacement)
                    db_session.add(metadata)
                    db_session.commit()
            elif superseded_by == "source":
                artifact.sha256 = "f" * 64
                db_session.add(artifact)
                db_session.commit()
            else:
                monkeypatch.setitem(
                    kinds.group(JobKind.DERIVATIVES_MESH).kinds,
                    DerivativeKind.METADATA,
                    kinds.MESH_GEOMETRY_RECIPE + 1,
                )
            return emit_mesh_outputs(request, reply, on_output)

        monkeypatch.setattr(mesh_isolation, "generate", delayed_refusal)
        outcome = producers.derive_mesh(artifact.id)
        db_session.refresh(metadata)
        assert outcome.kinds == {}
        assert metadata.bbox_x_mm == 10
        assert metadata.triangle_count == 4
        assert read_volume(metadata) == (
            replacement if superseded_by == "replacement" else prior
        )

    @pytest.mark.parametrize(
        "preview, expected_complete",
        [
            (PreviewCoverage.COMPLETE, True),
            (PreviewCoverage.PARTIAL, False),
            (PreviewCoverage.DOCUMENT_SUPPLIED, True),
        ],
    )
    def test_publishes_preview_completeness_independently_of_source_scan(
        self,
        db_session,
        stored,
        monkeypatch,
        preview,
        expected_complete,
        emit_mesh_outputs,
    ):
        artifact = stored("preview.stl", content.binary_stl())
        from printstash_core.mesh.measurements import (
            VolumeNotCalculated,
            VolumeNotCalculatedCause,
        )

        reply = ThumbnailResult(
            image=content.png(),
            geometry={
                "bbox_x_mm": 10.0,
                "bbox_y_mm": 10.0,
                "bbox_z_mm": 10.0,
                "triangle_count": 12,
                "volume_mm3": None,
            },
            geometry_outcome=GeometryReady(),
            volume=VolumeNotCalculated(VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED),
            strategy=(
                ThumbnailStrategy.EMBEDDED
                if preview is PreviewCoverage.DOCUMENT_SUPPLIED
                else ThumbnailStrategy.FALLBACK
            ),
            coverage=MeshCoverage(
                SourceScanState.COMPLETE, GeometryNotLoaded(), preview
            ),
            failure_reason=None,
            duration_ms=1,
            peak_rss_bytes=None,
        )

        def generated(request, *, on_output):
            return emit_mesh_outputs(request, reply, on_output)

        monkeypatch.setattr(mesh_isolation, "generate", generated)

        result = producers.derive_mesh(artifact.id)

        assert result.kinds[DerivativeKind.THUMBNAIL] is DerivativeState.READY
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert json.loads(row.output_json)["complete"] is expected_complete
        db_session.refresh(artifact)
        assert get_backend().exists(artifact.thumbnail_path)

    @pytest.mark.parametrize("failure_code", ["geometry_work_limit", "resource_limit"])
    def test_replaces_terminal_fingerprint_cap_refusals(
        self,
        db_session,
        stored,
        make_derivative,
        make_geometry_fingerprint,
        make_system_config,
        monkeypatch,
        failure_code,
    ):
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=10)
        artifact = stored("sphere.3mf", three_mf(meshes={1: mesh}))
        make_derivative(
            artifact,
            DerivativeKind.METADATA,
            recipe_version=6,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="resource_limit",
        )
        make_derivative(
            artifact,
            DerivativeKind.THUMBNAIL,
            recipe_version=4,
            state=DerivativeState.FAILED,
            exhausted=True,
            failure_reason="resource_limit",
        )
        cached = make_geometry_fingerprint(
            artifact, state="failed", failure_code=failure_code
        )
        make_system_config(
            similarity_settings_json=json.dumps(
                {"enabled": True, "fingerprint_on_ingest": True, "triangle_cap": 100}
            )
        )
        monkeypatch.setattr(extensions, "_derivatives", similarity_ingestion)

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: DerivativeState.READY,
            DerivativeKind.THUMBNAIL: DerivativeState.READY,
        }
        db_session.expire_all()
        rows = _rows(db_session, artifact.id)
        assert (
            rows[DerivativeKind.METADATA].recipe_version == kinds.MESH_GEOMETRY_RECIPE
        )
        assert (
            rows[DerivativeKind.THUMBNAIL].recipe_version == kinds.MESH_THUMBNAIL_RECIPE
        )
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert metadata.triangle_count == 320
        assert metadata.bbox_x_mm == 20
        fresh = db_session.get(File, artifact.id)
        assert fresh.thumbnail_path is not None
        assert get_backend().exists(fresh.thumbnail_path)
        fingerprint = db_session.exec(
            select(GeometryFingerprint).where(
                GeometryFingerprint.file_id == artifact.id
            )
        ).one()
        assert fingerprint.id == cached.id
        assert fingerprint.state == "failed"
        assert fingerprint.failure_code == "geometry_work_limit"

    def test_derives_every_mesh_kind_from_one_load(
        self, db_session: Session, stored, announced
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "ready",
            DerivativeKind.THUMBNAIL: "ready",
        }
        rows = _rows(db_session, artifact.id)
        assert {kind: row.state for kind, row in rows.items()} == {
            DerivativeKind.METADATA: DerivativeState.READY,
            DerivativeKind.THUMBNAIL: DerivativeState.READY,
        }
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert meta.triangle_count == 12
        published = db_session.get(File, artifact.id)
        assert published is not None and published.thumbnail_path
        assert get_backend().exists(published.thumbnail_path)
        assert rows[DerivativeKind.THUMBNAIL].storage_key == published.thumbnail_path

    def test_replaces_rounded_small_measurements(
        self, db_session, stored, make_derivative, make_metadata
    ):
        mesh = trimesh.creation.box(extents=[0.001, 0.002, 0.003])
        artifact = stored("small.3mf", three_mf(meshes={1: mesh}))
        make_metadata(artifact, bbox_x_mm=0, bbox_y_mm=0, bbox_z_mm=0, volume_mm3=0)
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=4)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        outcome = producers.derive_mesh(artifact.id)

        db_session.expire_all()
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert outcome.kinds[DerivativeKind.METADATA] == DerivativeState.READY
        assert (meta.bbox_x_mm, meta.bbox_y_mm, meta.bbox_z_mm) == pytest.approx(
            (0.001, 0.002, 0.003), rel=1e-12, abs=0
        )
        assert meta.volume_mm3 == pytest.approx(6e-9, rel=1e-12, abs=0)
        assert read_volume(meta) == VolumeMeasured(meta.volume_mm3)

    def test_replaces_stale_inconsistent_winding_volume(
        self, db_session, stored, make_derivative, make_metadata
    ):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces[0] = mesh.faces[0][::-1]
        artifact = stored("inconsistent.stl", mesh.export(file_type="stl"))
        make_metadata(artifact, volume_mm3=500)
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=3)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        outcome = producers.derive_mesh(artifact.id)

        db_session.expire_all()
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert outcome.kinds[DerivativeKind.METADATA] == DerivativeState.READY
        assert meta.volume_mm3 is None
        assert read_volume(meta) == VolumeUnavailable(
            VolumeUnavailableCause.INCONSISTENT_WINDING
        )

    def test_refused_geometry_is_terminal_with_an_embedded_preview(
        self, db_session, stored, monkeypatch
    ):
        import io

        from PIL import Image

        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        image = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(image, format="PNG")
        data = three_mf(
            build=tuple((1, None) for _ in range(2049)),
            extras={"Metadata/thumbnail.png": image.getvalue()},
        )
        artifact = stored("repeated.3mf", data)

        result = producers.derive_mesh(artifact.id)

        assert result.kinds[DerivativeKind.METADATA] == DerivativeState.FAILED
        rows = _rows(db_session, artifact.id)
        row = rows[DerivativeKind.METADATA]
        assert row.failure_reason == "resource_limit"
        assert row.next_attempt_at is None
        assert row.attempts == settings.derivative_max_attempts
        assert row.recipe_version == kinds.MESH_GEOMETRY_RECIPE
        assert result.kinds[DerivativeKind.THUMBNAIL] == DerivativeState.READY
        thumbnail = rows[DerivativeKind.THUMBNAIL]
        assert thumbnail.recipe_version == kinds.MESH_THUMBNAIL_RECIPE
        assert json.loads(thumbnail.output_json)["strategy"] == "embedded"
        assert thumbnail.storage_key is not None
        assert get_backend().exists(thumbnail.storage_key)
        original = get_backend().read_bytes(artifact.path)
        assert original == data
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            assert archive.read("Metadata/thumbnail.png") == image.getvalue()

    def test_embedded_preview_survives_refused_geometry(
        self, db_session, stored, monkeypatch
    ):
        import io

        from PIL import Image

        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 100)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        image = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(image, format="PNG")
        artifact = stored(
            "repeated.3mf",
            three_mf(
                build=tuple((1, None) for _ in range(40)),
                extras={"Metadata/thumbnail.png": image.getvalue()},
            ),
        )

        result = producers.derive_mesh(artifact.id)

        assert result.kinds[DerivativeKind.THUMBNAIL] == DerivativeState.READY
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert row.storage_key is not None
        assert get_backend().exists(row.storage_key)

    def test_malformed_mesh_does_not_publish_successful_unknown_metadata(
        self, db_session, stored
    ):
        artifact = stored("invalid.3mf", b"not a zip")
        producers.derive_mesh(artifact.id)
        row = _rows(db_session, artifact.id)[DerivativeKind.METADATA]
        assert row.state == DerivativeState.FAILED
        assert row.next_attempt_at is None

    def test_the_thumbnail_represents_its_model(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        producers.derive_mesh(artifact.id)

        model = db_session.get(Model, artifact.model_id)
        assert model is not None
        assert model.thumbnail_file_id == artifact.id

    def test_tells_the_models_viewers_what_changed(self, stored, announced) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        producers.derive_mesh(artifact.id)

        assert {(n["channel"], n["kind"], n["state"]) for n in announced} == {
            (f"model:{artifact.model_id}", DerivativeKind.METADATA, "ready"),
            (f"model:{artifact.model_id}", DerivativeKind.THUMBNAIL, "ready"),
        }

    def test_derives_only_what_is_still_owed(
        self, db_session: Session, stored, make_derivative
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        make_derivative(artifact, DerivativeKind.METADATA)

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {DerivativeKind.THUMBNAIL: "ready"}
        assert (
            db_session.exec(
                select(Metadata).where(Metadata.file_id == artifact.id)
            ).first()
            is None
        )

    def test_a_fully_derived_artifact_is_left_alone(
        self, stored, make_derivative, announced
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        assert producers.derive_mesh(artifact.id).kinds == {}
        assert announced == []

    def test_a_trashed_artifact_is_not_derived(
        self, db_session: Session, stored
    ) -> None:
        from app.core.time import utcnow

        artifact = stored("cube.stl", content.binary_stl(), deleted_at=utcnow())

        assert producers.derive_mesh(artifact.id).kinds == {}
        assert _rows(db_session, artifact.id) == {}

    def test_unreadable_bytes_fail_transiently(
        self, db_session: Session, stored, remove_blob_key
    ) -> None:
        # The object may come back (a storage outage); a retry can succeed.
        artifact = stored("cube.stl", content.binary_stl())
        remove_blob_key(artifact.path)

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "failed",
            DerivativeKind.THUMBNAIL: "failed",
        }
        rows = _rows(db_session, artifact.id)
        assert all(row.failure_reason == "invalid_source" for row in rows.values())
        assert all(row.next_attempt_at is not None for row in rows.values())

    # The binding is process-wide: monkeypatch restores whatever the process
    # had bound, so later tests still get the similarity extension.
    def test_hands_geometry_fingerprints_to_similarity(
        self, stored, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())
        similarity = FingerprintSink()
        monkeypatch.setattr(extensions, "_derivatives", similarity)

        producers.derive_mesh(artifact.id)

        assert similarity.received == [artifact.id]

    def test_a_similarity_failure_does_not_fail_the_geometry(
        self, stored, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fingerprints are evidence similarity can re-derive; the geometry the
        # load produced is still good.
        artifact = stored("cube.stl", content.binary_stl())
        monkeypatch.setattr(extensions, "_derivatives", FingerprintSink(broken=True))

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "ready",
            DerivativeKind.THUMBNAIL: "ready",
        }

    def test_a_render_that_cannot_work_is_terminal(
        self, db_session: Session, stored
    ) -> None:
        # Bytes that parse to no geometry will never render; retrying only
        # burns render capacity.
        artifact = stored("broken.stl", b"solid broken\nendsolid broken\n")

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds[DerivativeKind.THUMBNAIL] == "failed"
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert row.attempts == settings.derivative_max_attempts
        assert row.next_attempt_at is None

    def test_a_worker_over_its_memory_budget_fails_both_kinds_terminally(
        self, db_session: Session, stored, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#259: the file that exhausts memory is recorded, and this process lives.

        A budget no interpreter can meet stands in for a mesh the size caps
        mis-sized. Retrying the same bytes would repeat the kill, so both kinds
        are terminal at once instead of backing off into an hourly crash loop.
        """
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)
        artifact = stored("cube.stl", content.binary_stl())

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "failed",
            DerivativeKind.THUMBNAIL: "failed",
        }
        for row in _rows(db_session, artifact.id).values():
            assert row.failure_reason == "resource_limit"
            assert row.attempts == settings.derivative_max_attempts
            assert row.next_attempt_at is None

    def test_a_worker_that_times_out_is_retried_later(
        self, db_session: Session, stored, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Slowness may be the machine, not the file, so it keeps its backoff.
        monkeypatch.setitem(_overlay, "mesh_worker_timeout_seconds", 0.2)
        artifact = stored("cube.stl", content.binary_stl())

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds[DerivativeKind.THUMBNAIL] == "failed"
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert row.failure_reason == "timeout"
        assert row.attempts < settings.derivative_max_attempts
        assert row.next_attempt_at is not None

    def test_a_3mf_that_repeats_one_part_beyond_the_budget_is_derived_safely(
        self, db_session: Session, stored, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The #259 shape through the whole pipeline: stored, derived, recorded."""
        monkeypatch.setenv("VAULT_MESH_MAX_RENDER_TRIANGLES", "1000")
        monkeypatch.setenv("VAULT_MESH_MEMORY_BUDGET_FRACTION", "0")
        placements = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 5} 0 0") for i in range(400))
        artifact = stored("plate.3mf", three_mf(build=placements))

        outcome = producers.derive_mesh(artifact.id)

        assert outcome.kinds[DerivativeKind.THUMBNAIL] == "failed"
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert row.failure_reason == "resource_limit"
        assert row.next_attempt_at is None

    def test_a_storage_failure_while_publishing_is_transient(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        artifact = stored("cube.stl", content.binary_stl())

        def collision(*_args, **_kwargs):
            raise ThumbnailPublicationError("thumbnail_key_collision")

        monkeypatch.setattr(producers, "publish_thumbnail", collision)

        with pytest.raises(
            ThumbnailPublicationError, match="^thumbnail_key_collision$"
        ):
            producers.derive_mesh(artifact.id)

        rows = _rows(db_session, artifact.id)
        assert rows[DerivativeKind.METADATA].state is DerivativeState.READY
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert metadata.triangle_count == 12
        row = rows[DerivativeKind.THUMBNAIL]
        assert row.state is DerivativeState.FAILED
        assert (row.failure_reason, row.next_attempt_at is not None) == (
            "storage",
            True,
        )


class TestDeriveGcode:
    def test_derives_every_gcode_kind_from_one_read(
        self, db_session: Session, stored
    ) -> None:
        data = (FIXTURES_DIR / "real_prusa_mk4_spatula.gcode").read_bytes()
        artifact = stored("spatula.gcode", data)

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "ready",
            DerivativeKind.THUMBNAIL: "ready",
        }
        meta = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert meta.slicer_name
        rows = _rows(db_session, artifact.id)
        assert (
            json.loads(rows[DerivativeKind.THUMBNAIL].output_json)["strategy"]
            == "embedded"
        )

    def test_gcode_without_an_embedded_image_skips_its_thumbnail(
        self, db_session: Session, stored
    ) -> None:
        artifact = stored("plain.gcode", content.gcode(marker="plain"))

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "ready",
            DerivativeKind.THUMBNAIL: "skipped",
        }
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert row.failure_reason == "no_embedded_thumbnail"

    def test_records_the_material_each_tool_needs(
        self, db_session: Session, stored
    ) -> None:
        from app.db.models import ArtifactMaterialRequirement

        data = (FIXTURES_DIR / "real_prusa_mk4_spatula.gcode").read_bytes()
        artifact = stored("spatula.gcode", data)

        producers.derive_gcode(artifact.id)

        requirements = db_session.exec(
            select(ArtifactMaterialRequirement).where(
                ArtifactMaterialRequirement.file_id == artifact.id
            )
        ).all()
        assert [req.tool_index for req in requirements] == [0]

    def test_malformed_material_entries_are_dropped(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        from app.db.models import ArtifactMaterialRequirement
        from app.modules.media import gcode_parser

        monkeypatch.setattr(
            gcode_parser,
            "parse",
            lambda _path: {
                "material_requirements": [
                    "PLA",
                    {"tool_index": 1, "material_type": "  "},
                    {"tool_index": 2, "material_type": " PETG ", "color_hex": "#fff"},
                ]
            },
        )
        artifact = stored("multi.gcode", content.gcode(marker="multi"))

        producers.derive_gcode(artifact.id)

        (requirement,) = db_session.exec(
            select(ArtifactMaterialRequirement).where(
                ArtifactMaterialRequirement.file_id == artifact.id
            )
        ).all()
        assert (requirement.tool_index, requirement.material_type) == (2, "PETG")

    def test_derives_only_what_is_still_owed(
        self, db_session: Session, stored, make_derivative
    ) -> None:
        artifact = stored("plain.gcode", content.gcode(marker="owed"))
        make_derivative(artifact, DerivativeKind.METADATA)

        assert producers.derive_gcode(artifact.id).kinds == {
            DerivativeKind.THUMBNAIL: "skipped"
        }
        assert (
            db_session.exec(
                select(Metadata).where(Metadata.file_id == artifact.id)
            ).first()
            is None
        )

    def test_a_fully_derived_artifact_is_left_alone(
        self, stored, make_derivative
    ) -> None:
        artifact = stored("plain.gcode", content.gcode(marker="done"))
        make_derivative(artifact, DerivativeKind.METADATA)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        assert producers.derive_gcode(artifact.id).kinds == {}

    def test_a_trashed_artifact_is_not_derived(self, stored) -> None:
        from app.core.time import utcnow

        artifact = stored(
            "plain.gcode", content.gcode(marker="trashed"), deleted_at=utcnow()
        )

        assert producers.derive_gcode(artifact.id).kinds == {}

    def test_an_embedded_image_that_does_not_decode_is_terminal(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        from app.modules.media import thumbnail

        monkeypatch.setattr(thumbnail, "extract", lambda _path: b"not an image")
        artifact = stored("plain.gcode", content.gcode(marker="garbled"))

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds[DerivativeKind.THUMBNAIL] == "failed"
        row = _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL]
        assert (row.failure_reason, row.next_attempt_at) == ("invalid_source", None)

    def test_a_profile_detection_failure_does_not_fail_the_metadata(
        self, db_session: Session, stored, monkeypatch
    ) -> None:
        from app.modules.printing import profile_detection

        def broken(*_args, **_kwargs):
            raise RuntimeError("profile table missing")

        monkeypatch.setattr(profile_detection, "upsert_detected_profiles", broken)
        artifact = stored("plain.gcode", content.gcode(marker="profiles"))

        assert (
            producers.derive_gcode(artifact.id).kinds[DerivativeKind.METADATA]
            == "ready"
        )

    def test_unreadable_bytes_fail_transiently(
        self, db_session: Session, stored, remove_blob_key
    ) -> None:
        artifact = stored("gone.gcode", content.gcode(marker="gone"))
        remove_blob_key(artifact.path)

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {
            DerivativeKind.METADATA: "failed",
            DerivativeKind.THUMBNAIL: "failed",
        }


class TestDeriveToolpath:
    @pytest.fixture
    def bgcode(self, stored):
        return stored(
            "plate.bgcode",
            b"GCDE\x01\x00\x00\x00\x01\x00" + b"\x00" * 16,
            file_type=FileType.GCODE,
        )

    # The converter subprocess itself is covered in media/test_toolpath.py;
    # here the boundary is stood in for so only the producer is under test.

    def test_publishes_a_converted_toolpath(
        self, db_session: Session, bgcode, monkeypatch
    ) -> None:
        def converted(_file, directory: Path) -> Path:
            output = directory / "input.gcode"
            output.write_bytes(b"G1 X1\n")
            return output

        monkeypatch.setattr(toolpath, "convert", converted)

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {DerivativeKind.TOOLPATH: "ready"}
        row = _rows(db_session, bgcode.id)[DerivativeKind.TOOLPATH]
        assert row.storage_key is not None
        assert get_backend().read_bytes(row.storage_key) == b"G1 X1\n"

    @pytest.mark.parametrize(
        "kind", [ErrorKind.UNPROCESSABLE, ErrorKind.TOO_LARGE, ErrorKind.NOT_FOUND]
    )
    def test_a_container_that_cannot_convert_is_terminal(
        self, db_session: Session, bgcode, monkeypatch, kind: ErrorKind
    ) -> None:
        def refused(*_args):
            raise OperationError("toolpath_invalid_bgcode", kind=kind)

        monkeypatch.setattr(toolpath, "convert", refused)

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {DerivativeKind.TOOLPATH: "failed"}
        row = _rows(db_session, bgcode.id)[DerivativeKind.TOOLPATH]
        assert (row.failure_reason, row.next_attempt_at) == (
            "toolpath_invalid_bgcode",
            None,
        )

    def test_a_vanished_source_is_transient(
        self, db_session: Session, bgcode, remove_blob_key
    ) -> None:
        remove_blob_key(bgcode.path)

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {DerivativeKind.TOOLPATH: "failed"}
        row = _rows(db_session, bgcode.id)[DerivativeKind.TOOLPATH]
        assert row.failure_reason == "file_blob_unavailable"
        assert row.next_attempt_at is not None

    def test_unreadable_content_is_transient(
        self, db_session: Session, bgcode, monkeypatch
    ) -> None:
        from app.modules.storage.artifact_content import ArtifactContentError

        def unreadable(*_args):
            raise ArtifactContentError("backend_unavailable")

        monkeypatch.setattr(toolpath, "convert", unreadable)

        assert producers.derive_toolpath(bgcode.id).kinds == {
            DerivativeKind.TOOLPATH: "failed"
        }
        row = _rows(db_session, bgcode.id)[DerivativeKind.TOOLPATH]
        assert (row.failure_reason, row.next_attempt_at is not None) == (
            "invalid_source",
            True,
        )

    def test_a_trashed_artifact_is_not_derived(self, stored) -> None:
        from app.core.time import utcnow

        artifact = stored(
            "gone.bgcode", b"GCDE\x01\x00\x00\x00\x01\x00", deleted_at=utcnow()
        )

        assert producers.derive_toolpath(artifact.id).kinds == {}

    def test_a_missing_converter_is_transient(
        self, db_session: Session, bgcode
    ) -> None:
        # Installing the converter fixes it; the derivative should come back.
        _overlay["bgcode_executable"] = "/nonexistent/printstash-bgcode"

        outcome = producers.derive_toolpath(bgcode.id)

        assert outcome.kinds == {DerivativeKind.TOOLPATH: "failed"}
        row = _rows(db_session, bgcode.id)[DerivativeKind.TOOLPATH]
        assert row.failure_reason == "toolpath_converter_unavailable"
        assert row.next_attempt_at is not None

    def test_a_toolpath_already_derived_is_left_alone(
        self, bgcode, make_derivative
    ) -> None:
        make_derivative(bgcode, DerivativeKind.TOOLPATH)

        assert producers.derive_toolpath(bgcode.id).kinds == {}


class TestOutcome:
    def test_reports_its_kinds_in_a_stable_order(self) -> None:
        outcome = producers.Outcome(
            {DerivativeKind.THUMBNAIL: "ready", DerivativeKind.METADATA: "failed"}
        )

        assert list(outcome.as_result()["derivatives"]) == [
            DerivativeKind.METADATA,
            DerivativeKind.THUMBNAIL,
        ]


class TestLivePolicy:
    def test_reenable_processes_missing_outputs_through_the_source(
        self, db_session, stored, work_engine
    ):
        from app.db.models import JobKind
        from app.modules.derivatives import policy
        from app.modules.work.submission import nudge

        policy.update(db_session, {policy.SettingName.MESH: False})
        artifact = stored("resume.stl", content.binary_stl())
        nudge(JobKind.DERIVATIVES_MESH)
        work_engine.drain()
        assert _rows(db_session, artifact.id) == {}
        policy.update(db_session, {policy.SettingName.MESH: True})
        work_engine.drain()
        assert {row.state for row in _rows(db_session, artifact.id).values()} == {
            DerivativeState.READY
        }

    @pytest.mark.parametrize("disabled", ["gcode", "toolpath"])
    @pytest.mark.bgcode
    def test_binary_groups_produce_independently(
        self, db_session, stored, work_engine, disabled, bgcode_binary
    ):
        from app.db.models import JobKind
        from app.modules.derivatives import policy
        from app.modules.work.submission import nudge

        _overlay["bgcode_executable"] = str(bgcode_binary)
        name = (
            policy.SettingName.GCODE
            if disabled == "gcode"
            else policy.SettingName.TOOLPATH
        )
        policy.update(db_session, {name: False})
        artifact = stored(
            "independent.bgcode",
            (FIXTURES_DIR / "bgcode/prusaslicer.bgcode").read_bytes(),
        )
        for definition in [JobKind.DERIVATIVES_GCODE, JobKind.DERIVATIVES_TOOLPATH]:
            nudge(definition)
        work_engine.drain()
        rows = _rows(db_session, artifact.id)
        if disabled == "gcode":
            assert set(rows) == {DerivativeKind.TOOLPATH}
            assert rows[DerivativeKind.TOOLPATH].state is DerivativeState.READY
        else:
            assert set(rows) == {DerivativeKind.METADATA, DerivativeKind.THUMBNAIL}
            assert rows[DerivativeKind.METADATA].state is DerivativeState.READY


class TestPublishedOutputs:
    def test_disabling_preserves_published_outputs(self, db_session, stored):
        from app.modules.derivatives import policy

        artifact = stored("retained.stl", content.binary_stl())
        producers.derive_mesh(artifact.id)
        db_session.refresh(artifact)
        key = artifact.thumbnail_path
        assert key is not None
        before = get_backend().read_bytes(key)
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        policy.update(db_session, {policy.SettingName.MESH: False})
        assert get_backend().read_bytes(key) == before
        db_session.refresh(metadata)
        assert metadata.triangle_count == 12


class TestMeshCancellation:
    @pytest.mark.parametrize("retry", [False, True], ids=["cancel", "immediate-retry"])
    def test_withdrawal_stops_in_flight_native_work(
        self,
        db_session,
        stored,
        make_job,
        make_user,
        work_engine,
        tmp_path,
        monkeypatch,
        retry,
    ):
        original = content.binary_stl()
        artifact = stored("waiting.stl", original)
        owner = make_user()
        job = make_job(
            kind=JobKind.DERIVATIVES_MESH,
            subject=f"file/{artifact.id}",
            owner=owner,
        )
        pids = tmp_path / "pids"
        ready = tmp_path / "temporary"
        withdrawn = runner._withdrawn
        acted = False
        cancelled_at = None

        def cancel_when_started(*args):
            nonlocal acted, cancelled_at
            if ready.exists() and not acted:
                acted = True
                cancelled_at = time.monotonic()
                service.cancel(job.id, actor=owner)
                if retry:
                    service.retry(job.id, actor=owner)
            return withdrawn(*args)

        with monkeypatch.context() as patch:
            _overlay.update({"mesh_worker_timeout_seconds": 15})
            patch.setattr(runner, "_withdrawn", cancel_when_started)
            patch.setattr(
                mesh_isolation,
                "worker_command",
                lambda _module, _args, budget: command(
                    "tests.fakes.mesh_bootstrap_probe",
                    ["tree_wait", str(pids), str(ready)],
                    budget,
                ),
            )
            submit(job.id)
            work_engine.run_one()
            finished = time.monotonic()

        status = jobs.get(job.id)
        assert status is not None
        assert status.state is (JobState.QUEUED if retry else JobState.CANCELLED)
        assert acted
        assert cancelled_at is not None
        assert finished - cancelled_at < 3
        for pid in json.loads(pids.read_text()):
            assert not Path(f"/proc/{pid}").exists()
        assert not Path(ready.read_text()).exists()
        assert db_session.exec(select(CapacityReservation)).all() == []
        db_session.expire_all()
        rows = db_session.exec(
            select(ArtifactDerivative).where(ArtifactDerivative.file_id == artifact.id)
        ).all()
        if retry:
            assert rows == []
        else:
            assert {row.kind: row.state for row in rows} == {
                DerivativeKind.METADATA: DerivativeState.CANCELLED,
                DerivativeKind.THUMBNAIL: DerivativeState.CANCELLED,
            }
        assert get_backend().read_bytes(artifact.path) == original

        _overlay.update({"mesh_worker_timeout_seconds": 300})
        healthy = stored("following.stl", content.binary_stl())
        assert (
            producers.derive_mesh(healthy.id).kinds[DerivativeKind.METADATA]
            is DerivativeState.READY
        )


class TestAttemptPublication:
    def test_cancelled_thumbnail_keeps_unpublished_bytes_receipted(
        self, db_session, stored
    ):
        from app.core.time import utcnow
        from app.db.models import OwnedStorageObject, StorageObjectState
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored("cancel.gcode", content.gcode())
        file, attempts = producers._begin(artifact.id, JobKind.DERIVATIVES_GCODE)
        records.cancel(
            db_session, artifact, group(JobKind.DERIVATIVES_GCODE).kinds, now=utcnow()
        )
        db_session.commit()

        with pytest.raises(records.AttemptSuperseded):
            producers._publish_thumbnail(
                file,
                content.png(),
                attempt=attempts[DerivativeKind.THUMBNAIL],
                normalize=False,
                strategy="embedded",
                complete=True,
            )

        db_session.expire_all()
        assert db_session.get(File, artifact.id).thumbnail_path is None
        assert db_session.get(Model, artifact.model_id).thumbnail_path is None
        receipt = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.object_kind == "thumbnail"
            )
        ).one()
        assert receipt.state is StorageObjectState.PENDING
        assert get_backend().exists(receipt.key)
        assert receipt.token is not None
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL].state
            is DerivativeState.CANCELLED
        )

    def test_cancelled_metadata_does_not_publish_slicer_facts(
        self, db_session, stored, monkeypatch
    ):
        from contextlib import contextmanager

        from app.core.time import utcnow
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored("cancel.gcode", content.gcode())
        backend = get_backend()
        original = backend.local_path

        @contextmanager
        def cancelled_read(key):
            with original(key) as path:
                yield path
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_GCODE).kinds,
                now=utcnow(),
            )
            db_session.commit()

        monkeypatch.setattr(backend, "local_path", cancelled_read)

        assert producers.derive_gcode(artifact.id).kinds == {}

        db_session.expire_all()
        assert (
            db_session.exec(
                select(Metadata).where(Metadata.file_id == artifact.id)
            ).first()
            is None
        )
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.METADATA].state
            is DerivativeState.CANCELLED
        )

    def test_cancelled_mesh_metadata_preserves_published_volume(
        self,
        db_session,
        stored,
        make_metadata,
        make_derivative,
        monkeypatch,
        stale_mesh_measurement_reply,
        emit_mesh_outputs,
    ):
        from app.core.time import utcnow
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored("cancelled-volume.stl", content.binary_stl())
        published = VolumeMeasured(6e-9)
        metadata = make_metadata(artifact, volume=published)
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=8)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        def cancelled_measurement(request, *, on_output):
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_MESH).kinds,
                now=utcnow(),
            )
            db_session.commit()
            return emit_mesh_outputs(request, stale_mesh_measurement_reply, on_output)

        monkeypatch.setattr(mesh_isolation, "generate", cancelled_measurement)

        outcome = producers.derive_mesh(artifact.id)

        db_session.refresh(metadata)
        assert outcome.kinds == {}
        assert read_volume(metadata) == published
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.METADATA].state
            is DerivativeState.CANCELLED
        )

    @pytest.mark.parametrize(
        "replacement",
        [
            VolumeMeasured(1e-9),
            VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT),
        ],
        ids=["small-measured-volume", "unavailable-volume"],
    )
    def test_superseded_mesh_metadata_preserves_replacement_volume(
        self,
        db_session,
        stored,
        make_metadata,
        make_derivative,
        monkeypatch,
        stale_mesh_measurement_reply,
        emit_mesh_outputs,
        replacement,
    ):
        from app.core.time import utcnow
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored("superseded-volume.stl", content.binary_stl())
        metadata = make_metadata(artifact, volume=VolumeMeasured(500.0))
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=8)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        def superseded_measurement(request, *, on_output):
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_MESH).kinds,
                now=utcnow(),
            )
            db_session.commit()
            fresh = records.begin(
                db_session,
                artifact,
                DerivativeKind.METADATA,
                kinds.MESH_GEOMETRY_RECIPE,
                now=utcnow(),
            )
            attempt = records.attempt(db_session, artifact, fresh)
            records.mark_ready(db_session, attempt, now=utcnow())
            apply_volume(metadata, replacement)
            db_session.add(metadata)
            db_session.commit()
            return emit_mesh_outputs(request, stale_mesh_measurement_reply, on_output)

        monkeypatch.setattr(mesh_isolation, "generate", superseded_measurement)

        outcome = producers.derive_mesh(artifact.id)

        db_session.refresh(metadata)
        assert outcome.kinds == {}
        assert read_volume(metadata) == replacement
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.METADATA].state
            is DerivativeState.READY
        )

    def test_replaced_mesh_source_preserves_published_volume(
        self,
        db_session,
        stored,
        make_metadata,
        make_derivative,
        monkeypatch,
        stale_mesh_measurement_reply,
        emit_mesh_outputs,
    ):
        artifact = stored("replaced-source.stl", content.binary_stl())
        published = VolumeMeasured(6e-9)
        metadata = make_metadata(artifact, volume=published)
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=8)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)

        def replaced_measurement(request, *, on_output):
            artifact.sha256 = "f" * 64
            db_session.add(artifact)
            db_session.commit()
            return emit_mesh_outputs(request, stale_mesh_measurement_reply, on_output)

        monkeypatch.setattr(mesh_isolation, "generate", replaced_measurement)

        outcome = producers.derive_mesh(artifact.id)

        db_session.refresh(metadata)
        assert outcome.kinds == {}
        assert read_volume(metadata) == published

    def test_cancelled_toolpath_retains_only_pending_ownership(
        self, db_session, stored, monkeypatch, tmp_path
    ):
        from app.core.time import utcnow
        from app.db.models import OwnedStorageObject, StorageObjectState
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored("cancel.bgcode", b"GCDE")

        def converted(_file, directory):
            output = directory / "output.gcode"
            output.write_bytes(content.gcode())
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_TOOLPATH).kinds,
                now=utcnow(),
            )
            db_session.commit()
            return output

        monkeypatch.setattr(toolpath, "convert", converted)

        assert producers.derive_toolpath(artifact.id).kinds == {}

        db_session.expire_all()
        row = _rows(db_session, artifact.id)[DerivativeKind.TOOLPATH]
        assert (row.state, row.storage_key) == (DerivativeState.CANCELLED, None)
        receipt = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.object_kind == "toolpath"
            )
        ).one()
        assert receipt.state is StorageObjectState.PENDING
        assert get_backend().exists(receipt.key)

    def test_announces_committed_metadata_when_thumbnail_is_superseded(
        self, db_session, stored, monkeypatch, announced
    ):
        from app.core.time import utcnow
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored(
            "spatula.gcode",
            (FIXTURES_DIR / "real_prusa_mk4_spatula.gcode").read_bytes(),
        )
        backend = get_backend()
        create = backend.create_bytes

        def cancel_after_bytes(data, key):
            receipt = create(data, key)
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_GCODE).kinds,
                now=utcnow(),
            )
            db_session.commit()
            return receipt

        monkeypatch.setattr(backend, "create_bytes", cancel_after_bytes)

        outcome = producers.derive_gcode(artifact.id)

        assert outcome.kinds == {DerivativeKind.METADATA: DerivativeState.READY}
        assert len(announced) == 1
        assert announced[0]["kind"] == "metadata"
        assert announced[0]["state"] == "ready"


class TestDeriveViewerStl:
    def test_cancelled_conversion_retains_only_pending_ownership(
        self, db_session, stored, monkeypatch
    ):
        from app.core.time import utcnow
        from app.db.models import OwnedStorageObject, StorageObjectState
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored(
            "cancel.obj",
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
            viewer_requested_at=utcnow(),
        )

        def converted(_path, *, file_type):
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_VIEWER_STL).kinds,
                now=utcnow(),
            )
            db_session.commit()
            return content.binary_stl()

        monkeypatch.setattr(producers.stl_isolation, "to_stl_bytes", converted)

        assert producers.derive_viewer_stl(artifact.id).kinds == {}

        row = _rows(db_session, artifact.id)[DerivativeKind.VIEWER_STL]
        assert (row.state, row.storage_key) == (DerivativeState.CANCELLED, None)
        proof = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.object_kind == "viewer_stl"
            )
        ).one()
        assert proof.state is StorageObjectState.PENDING
        assert get_backend().read_bytes(proof.key) == content.binary_stl()

    def test_superseded_conversion_preserves_the_new_viewer_bytes(
        self, db_session, stored, monkeypatch
    ):
        from app.core.time import utcnow
        from app.db.models import OwnedStorageObject, StorageObjectState
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored(
            "replace.obj",
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
            viewer_requested_at=utcnow(),
        )
        published_key = None
        fresh_bytes = b"new viewer generation"

        def converted(_path, *, file_type):
            nonlocal published_key
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_VIEWER_STL).kinds,
                now=utcnow(),
            )
            db_session.commit()
            records.reset(
                db_session, artifact, group(JobKind.DERIVATIVES_VIEWER_STL).kinds
            )
            db_session.commit()
            with monkeypatch.context() as patch:
                patch.setattr(
                    producers.stl_isolation,
                    "to_stl_bytes",
                    lambda *_args, **_kwargs: fresh_bytes,
                )
                from concurrent.futures import ThreadPoolExecutor
                from contextvars import copy_context

                # A replacement attempt runs in another executor, with independent
                # resource scopes and sessions, while this one still lives.
                with ThreadPoolExecutor(1) as executor:
                    replacement = executor.submit(
                        copy_context().run, producers.derive_viewer_stl, artifact.id
                    )
                    assert replacement.result(timeout=10).kinds == {
                        DerivativeKind.VIEWER_STL: DerivativeState.READY
                    }
            published_key = _rows(db_session, artifact.id)[
                DerivativeKind.VIEWER_STL
            ].storage_key
            db_session.rollback()
            return content.binary_stl()

        monkeypatch.setattr(producers.stl_isolation, "to_stl_bytes", converted)

        assert producers.derive_viewer_stl(artifact.id).kinds == {}

        row = _rows(db_session, artifact.id)[DerivativeKind.VIEWER_STL]
        assert (row.state, row.storage_key) == (DerivativeState.READY, published_key)
        assert get_backend().read_bytes(published_key) == fresh_bytes
        pending = db_session.exec(
            select(OwnedStorageObject).where(
                OwnedStorageObject.object_kind == "viewer_stl",
                OwnedStorageObject.state == StorageObjectState.PENDING,
            )
        ).one()
        assert pending.key != published_key
        assert get_backend().read_bytes(pending.key) == content.binary_stl()

    def test_superseded_failure_preserves_the_new_ready_viewer(
        self, db_session, stored, monkeypatch
    ):
        from app.core.time import utcnow
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group
        from app.modules.media.mesh_contracts import ThumbnailFailureReason

        artifact = stored(
            "failed.obj",
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
            viewer_requested_at=utcnow(),
        )
        fresh_bytes = b"new viewer generation"

        def failed(_path, *, file_type):
            records.cancel(
                db_session,
                artifact,
                group(JobKind.DERIVATIVES_VIEWER_STL).kinds,
                now=utcnow(),
            )
            db_session.commit()
            records.reset(
                db_session, artifact, group(JobKind.DERIVATIVES_VIEWER_STL).kinds
            )
            db_session.commit()
            with monkeypatch.context() as patch:
                patch.setattr(
                    producers.stl_isolation,
                    "to_stl_bytes",
                    lambda *_args, **_kwargs: fresh_bytes,
                )
                from concurrent.futures import ThreadPoolExecutor
                from contextvars import copy_context

                # A replacement attempt runs in another executor, with independent
                # resource scopes and sessions, while this one still lives.
                with ThreadPoolExecutor(1) as executor:
                    replacement = executor.submit(
                        copy_context().run, producers.derive_viewer_stl, artifact.id
                    )
                    assert replacement.result(timeout=10).kinds == {
                        DerivativeKind.VIEWER_STL: DerivativeState.READY
                    }
            db_session.rollback()
            raise mesh_isolation.MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)

        monkeypatch.setattr(producers.stl_isolation, "to_stl_bytes", failed)

        assert producers.derive_viewer_stl(artifact.id).kinds == {}

        row = _rows(db_session, artifact.id)[DerivativeKind.VIEWER_STL]
        assert row.state is DerivativeState.READY
        assert row.failure_reason is None
        assert get_backend().read_bytes(row.storage_key) == fresh_bytes


class TestSourcePreparationAdmission:
    @pytest.mark.parametrize("viewer", [False, True], ids=["mesh", "viewer"])
    def test_oversized_source_records_resource_limit_before_native_work(
        self, db_session, stored, monkeypatch, viewer
    ):
        from app.core.time import utcnow
        from app.modules.media import source_preparation
        from app.runtime.native_admission import Resources

        artifact = stored(
            "cube.obj" if viewer else "cube.stl",
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n" if viewer else content.binary_stl(),
            viewer_requested_at=utcnow(),
        )
        monkeypatch.setattr(source_preparation, "capacity", lambda: Resources(1, 1))

        def unexpected(*_args, **_kwargs):
            pytest.fail("oversized source reached native processing")

        monkeypatch.setattr(mesh_isolation, "generate", unexpected)
        monkeypatch.setattr(producers.stl_isolation, "to_stl_bytes", unexpected)
        if viewer:
            result = producers.derive_viewer_stl(artifact.id)
            kinds = {DerivativeKind.VIEWER_STL}
        else:
            result = producers.derive_mesh(artifact.id)
            kinds = {DerivativeKind.METADATA, DerivativeKind.THUMBNAIL}
        assert result.kinds == {kind: DerivativeState.FAILED for kind in kinds}
        rows = _rows(db_session, artifact.id)
        for kind in kinds:
            assert rows[kind].failure_reason == "resource_limit"
            assert rows[kind].next_attempt_at is None


@pytest.fixture
def staged_mesh_outputs():
    from app.modules.media.mesh_protocol import GeometryOutput, ThumbnailOutput

    geometry = GeometryOutput(
        geometry={
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "triangle_count": 4,
            "volume_mm3": 1000.0,
        },
        outcome=GeometryReady(),
        volume=VolumeMeasured(1000.0),
        coverage=MeshCoverage(
            SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.NOT_PRODUCED
        ),
        duration_ms=5,
        peak_rss_bytes=None,
    )
    thumbnail = ThumbnailOutput(
        image=content.png(),
        strategy=ThumbnailStrategy.FULL,
        coverage=MeshCoverage(
            SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.COMPLETE
        ),
        failure_reason=None,
        duration_ms=10,
        peak_rss_bytes=None,
    )
    return geometry, thumbnail


class TestStagedMeshPublication:
    @pytest.mark.parametrize(
        "later_failure",
        [
            ThumbnailFailureReason.TIMEOUT,
            ThumbnailFailureReason.RESOURCE_LIMIT,
            ThumbnailFailureReason.WORKER_FAILED,
        ],
    )
    def test_keeps_committed_basics_after_later_worker_failure(
        self,
        db_session,
        stored,
        announced,
        monkeypatch,
        staged_mesh_outputs,
        later_failure,
    ):
        from app.db.session import get_session_factory

        source = content.binary_stl()
        artifact = stored("staged.stl", source)
        observed = []

        def worker(_request, *, on_output):
            for frame in staged_mesh_outputs:
                on_output(frame)
                with get_session_factory().scoped_session() as reader:
                    rows = _rows(reader, artifact.id)
                    observed.append({kind: row.state for kind, row in rows.items()})
                    meta = reader.exec(
                        select(Metadata).where(Metadata.file_id == artifact.id)
                    ).one()
                    assert read_volume(meta) == VolumeMeasured(1000.0)
                assert len(announced) == len(observed)
            assert get_backend().exists(
                _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL].storage_key
            )
            raise mesh_isolation.MeshWorkerError(later_failure)

        monkeypatch.setattr(mesh_isolation, "generate", worker)
        outcome = producers.derive_mesh(artifact.id)

        assert observed[0][DerivativeKind.METADATA] is DerivativeState.READY
        assert observed[0][DerivativeKind.THUMBNAIL] is DerivativeState.RUNNING
        assert (
            observed[1]
            == outcome.kinds
            == {
                DerivativeKind.METADATA: DerivativeState.READY,
                DerivativeKind.THUMBNAIL: DerivativeState.READY,
            }
        )
        assert len(announced) == 2
        assert db_session.exec(select(GeometryFingerprint)).all() == []
        assert get_backend().read_bytes(artifact.path) == source

    def test_metadata_commit_contains_pending_fingerprint_intent(
        self,
        db_session,
        stored,
        make_system_config,
        monkeypatch,
        staged_mesh_outputs,
    ):
        from app.db.models import MeshFingerprintContinuation
        from app.db.session import get_session_factory
        from app.modules.media.fingerprints import ALGORITHM_VERSION

        artifact = stored("pending-fingerprint.stl", content.binary_stl())
        make_system_config(
            similarity_settings_json=json.dumps(
                {"enabled": True, "fingerprint_on_ingest": True}
            )
        )
        monkeypatch.setattr(extensions, "_derivatives", similarity_ingestion)

        def worker(request, *, on_output):
            assert request.include_fingerprint
            on_output(staged_mesh_outputs[0])
            with get_session_factory().scoped_session() as reader:
                pending = reader.exec(select(MeshFingerprintContinuation)).one()
                assert pending.file_id == artifact.id
                assert pending.source_sha256 == artifact.sha256
                assert pending.algorithm_version == ALGORITHM_VERSION
                assert pending.triangle_cap == request.triangle_cap
                assert (
                    _rows(reader, artifact.id)[DerivativeKind.METADATA].state
                    is DerivativeState.READY
                )
            raise mesh_isolation.MeshWorkerError(ThumbnailFailureReason.TIMEOUT)

        monkeypatch.setattr(mesh_isolation, "generate", worker)
        producers.derive_mesh(artifact.id)
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.METADATA].state
            is DerivativeState.READY
        )
        assert (
            db_session.exec(select(MeshFingerprintContinuation)).one().file_id
            == artifact.id
        )
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_keeps_metadata_after_render_timeout(
        self,
        db_session,
        stored,
        announced,
        monkeypatch,
        staged_mesh_outputs,
    ):
        artifact = stored("render-timeout.stl", content.binary_stl())

        def worker(_request, *, on_output):
            on_output(staged_mesh_outputs[0])
            assert announced[0]["kind"] == "metadata"
            raise mesh_isolation.MeshWorkerError(ThumbnailFailureReason.TIMEOUT)

        monkeypatch.setattr(mesh_isolation, "generate", worker)
        outcome = producers.derive_mesh(artifact.id)

        rows = _rows(db_session, artifact.id)
        assert outcome.kinds == {
            DerivativeKind.METADATA: DerivativeState.READY,
            DerivativeKind.THUMBNAIL: DerivativeState.FAILED,
        }
        assert rows[DerivativeKind.METADATA].state is DerivativeState.READY
        assert rows[DerivativeKind.THUMBNAIL].failure_reason == "timeout"
        assert len([n for n in announced if n["kind"] == "metadata"]) == 1

    def test_aborts_publication_failure_without_retracting_metadata(
        self,
        db_session,
        stored,
        monkeypatch,
        staged_mesh_outputs,
    ):
        artifact = stored("storage-failure.stl", content.binary_stl())
        fault = RuntimeError("storage publication failed")
        reached_final = []

        def publication(*_args, **_kwargs):
            raise fault

        def worker(_request, *, on_output):
            on_output(staged_mesh_outputs[0])
            on_output(staged_mesh_outputs[1])
            reached_final.append(True)
            raise AssertionError("worker continued after publication failure")

        monkeypatch.setattr(producers, "publish_thumbnail", publication)
        monkeypatch.setattr(mesh_isolation, "generate", worker)
        with pytest.raises(RuntimeError) as error:
            producers.derive_mesh(artifact.id)

        assert error.value is fault
        assert reached_final == []
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.METADATA].state
            is DerivativeState.READY
        )
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    @pytest.mark.parametrize("withdrawal", ["cancel", "source"])
    def test_rejects_later_output_after_authority_changes(
        self,
        db_session,
        stored,
        announced,
        monkeypatch,
        staged_mesh_outputs,
        withdrawal,
    ):
        from app.core.time import utcnow
        from app.modules.derivatives import records
        from app.modules.derivatives.kinds import group

        artifact = stored("withdrawn.stl", content.binary_stl())
        accepted = []

        def worker(_request, *, on_output):
            on_output(staged_mesh_outputs[0])
            if withdrawal == "cancel":
                records.cancel(
                    db_session,
                    artifact,
                    group(JobKind.DERIVATIVES_MESH).kinds,
                    now=utcnow(),
                )
            else:
                db_session.refresh(artifact)
                artifact.sha256 = "b" * 64
                db_session.add(artifact)
            db_session.commit()
            on_output(staged_mesh_outputs[1])
            accepted.append(True)
            raise AssertionError("superseded native output accepted")

        monkeypatch.setattr(mesh_isolation, "generate", worker)
        outcome = producers.derive_mesh(artifact.id)

        assert accepted == []
        assert outcome.kinds == {DerivativeKind.METADATA: DerivativeState.READY}
        assert len(announced) == 1
        assert (
            _rows(db_session, artifact.id)[DerivativeKind.THUMBNAIL].state
            is not DerivativeState.READY
        )
