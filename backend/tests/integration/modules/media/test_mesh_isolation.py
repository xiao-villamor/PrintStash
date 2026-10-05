"""The isolated worker answers exactly as the in-process engine would.

Moving geometry work into a child changes where it runs, not what it produces:
the image, the measured geometry and the similarity fingerprint have to survive
the process boundary unchanged, or every model would quietly change on upgrade.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path

import pytest
import trimesh
from PIL import Image
from printstash_core.mesh.measurements import VolumeMeasured

from app.core.config import _overlay
from app.modules.media import mesh_isolation
from app.modules.media.fingerprints import ALGORITHM_VERSION, FingerprintResultState
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryReady,
    GeometryRefused,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
)
from app.modules.media.mesh_telemetry import WorkerExitCause
from app.modules.media.thumbnail_engine import ThumbnailEngine
from tests.factories import content
from tests.factories.geometry import three_mf
from tests.paths import BACKEND_DIR, TESTDATA_DIR


def _request(path, **overrides) -> ThumbnailRequest:
    values = dict(
        path=path,
        file_type="stl",
        include_geometry=True,
        include_thumbnail=True,
        include_fingerprint=True,
        output_format="WEBP",
        reason="derivative",
    )
    values.update(overrides)
    return ThumbnailRequest(**values)


class TestGenerate:
    def test_small_3mf_remains_available_with_half_gibibyte_native_budget(
        self, tmp_path, monkeypatch
    ):
        source = tmp_path / "small.3mf"
        source.write_bytes(three_mf())
        monkeypatch.setattr(
            mesh_isolation, "memory_budget_bytes", lambda: 512 * 1024**2
        )

        result = mesh_isolation.generate(
            _request(source, file_type="3mf", include_fingerprint=False)
        )

        assert result.geometry["triangle_count"] == 4
        assert result.image is not None

    def test_matches_the_in_process_engine(self, tmp_path):
        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())
        request = _request(path)

        isolated = mesh_isolation.generate(request)
        direct = ThumbnailEngine().generate(request)

        assert isolated.image == direct.image
        assert isolated.image is not None
        assert isolated.geometry == direct.geometry
        assert isolated.strategy == direct.strategy
        assert isolated.coverage == direct.coverage
        assert isolated.failure_reason == direct.failure_reason
        assert isolated.fingerprint_result == direct.fingerprint_result
        assert isolated.fingerprint_result is not None
        assert isolated.fingerprint_result.state is FingerprintResultState.READY

    def test_renders_real_benchy_webp_with_its_full_source_envelope(self, monkeypatch):
        source = TESTDATA_DIR / "benchy" / "3dbenchy.stl"
        original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)
        ceilings = []
        original_command = mesh_isolation.worker_command

        def command(module, arguments, budget):
            ceilings.append(budget)
            return original_command(module, arguments, budget)

        monkeypatch.setattr(mesh_isolation, "worker_command", command)
        result = mesh_isolation.generate(
            _request(source, include_fingerprint=False, output_format="WEBP")
        )

        assert ceilings == [902_824_000]
        assert result.strategy is ThumbnailStrategy.FULL
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        assert result.geometry["triangle_count"] == 225_706
        assert result.fingerprint_result is None
        assert result.failure_reason is None
        assert result.image is not None
        assert result.image.startswith(b"RIFF")
        assert result.image[8:12] == b"WEBP"
        with Image.open(io.BytesIO(result.image)) as image:
            assert image.format == "WEBP"
            assert image.size == (640, 480)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha

    def test_finds_a_source_given_as_a_path_relative_to_the_caller(
        self, tmp_path, monkeypatch
    ):
        """A relative path only means something to the process that holds it.

        Storage hands back paths relative to the API's working directory. The
        worker starts elsewhere, so an unresolved path is a missing file there,
        and a missing file reads as a mesh too big to size: a healthy model
        would be recorded as over the resource limit.
        """
        (tmp_path / "model-1").mkdir()
        (tmp_path / "model-1" / "cube.stl").write_bytes(content.binary_stl())
        monkeypatch.chdir(tmp_path)

        result = mesh_isolation.generate(
            _request(Path("model-1/cube.stl"), include_fingerprint=False)
        )

        assert result.failure_reason is None
        assert result.image is not None

    def test_renders_at_the_width_an_administrator_configured(
        self, tmp_path, monkeypatch
    ):
        """Runtime configuration lives in the API process; the worker must honour it.

        The thumbnail width is set in the admin UI and applied to an in-process
        overlay. A worker that read only its environment would render at the
        default and the derivative would be resized from the wrong source.
        """
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 320)
        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())

        result = mesh_isolation.generate(
            _request(path, include_geometry=False, include_fingerprint=False)
        )

        assert result.image is not None
        with Image.open(io.BytesIO(result.image)) as image:
            assert image.size == (320, 240)

    def test_reports_the_workers_own_peak_memory(self, tmp_path):
        """The parent's high-water mark says nothing about this file."""
        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())

        result = mesh_isolation.generate(_request(path, include_fingerprint=False))

        assert result.peak_rss_bytes is not None and result.peak_rss_bytes > 0

    def test_repeated_3mf_preserves_measurements_when_worker_refuses_preview(
        self, tmp_path, monkeypatch
    ):
        """The runtime render cap binds preview while retained measurements survive."""
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        path = tmp_path / "instanced.3mf"
        # 400 placements share a four-face resource:1600 placed faces exceed
        # the render cap, while unique source measurements remain admitted.
        placements = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 5} 0 0") for i in range(400))
        path.write_bytes(three_mf(build=placements))

        result = mesh_isolation.generate(
            _request(path, file_type="3mf", include_fingerprint=False)
        )

        assert result.image is None
        assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry == {
            "triangle_count": 1600,
            "bbox_x_mm": 2005.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "volume_mm3": 400000.0,
        }
        assert result.volume == VolumeMeasured(400000.0)
        assert result.coverage.source_scan is SourceScanState.COMPLETE
        assert isinstance(result.coverage.geometry, GeometryNotLoaded)
        assert result.coverage.preview is PreviewCoverage.NOT_PRODUCED
        assert result.supervision is not None
        assert result.supervision.exit_cause is WorkerExitCause.EXITED_ZERO

    def test_a_worker_over_its_memory_budget_raises_instead_of_dying_with_it(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)
        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())

        with pytest.raises(mesh_isolation.MeshWorkerError) as raised:
            mesh_isolation.generate(_request(path))

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT


class TestGeometryMeasurements:
    def test_preserves_native_hull_descriptor(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        path = tmp_path / "hull.stl"
        path.write_bytes(mesh.export(file_type="stl"))

        result = mesh_isolation.generate(_request(path))

        assert result.fingerprint_result.algorithm_version == ALGORITHM_VERSION
        values = result.fingerprint_result.records[0].values
        assert values["hull_ratio"] == pytest.approx(1)
        assert not any(name == "hull_ratio" for name, _ in values["unavailable"])

    @pytest.mark.parametrize(
        "strategy", [ThumbnailStrategy.STREAMING, ThumbnailStrategy.FALLBACK]
    )
    def test_preserves_small_streamed_bounds(self, tmp_path, monkeypatch, strategy):
        import numpy as np

        from app.modules.media import stl_streaming

        mesh = trimesh.creation.box(extents=[0.001, 0.002, 0.003])
        mesh.faces = np.tile(mesh.faces, (100, 1))
        _overlay["mesh_max_render_triangles"] = 1000
        path = tmp_path / "small-many-faces.stl"
        path.write_bytes(mesh.export(file_type="stl"))
        if strategy is ThumbnailStrategy.FALLBACK:
            monkeypatch.setattr(
                stl_streaming,
                "render_stl_preview_isolated",
                lambda *args, **kwargs: None,
            )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path, file_type="stl", triangle_cap=1000, width=32, height=32
            )
        )

        assert result.strategy is strategy
        assert tuple(
            result.geometry[f"bbox_{axis}_mm"] for axis in ("x", "y", "z")
        ) == pytest.approx((0.001, 0.002, 0.003), rel=1e-6, abs=0)

    @pytest.mark.parametrize("source", ["stl", "millimeter", "micron"])
    def test_preserves_small_physical_measurements(self, tmp_path, source):
        edge = 1.0 if source == "micron" else 0.001
        mesh = trimesh.creation.box(extents=[edge, edge, edge])
        path = tmp_path / ("tiny.stl" if source == "stl" else "tiny.3mf")
        path.write_bytes(
            mesh.export(file_type="stl")
            if source == "stl"
            else three_mf(meshes={1: mesh}, unit=source)
        )

        result = mesh_isolation.generate(
            ThumbnailRequest(path, include_thumbnail=False)
        )

        for axis in ("x", "y", "z"):
            assert result.geometry[f"bbox_{axis}_mm"] == pytest.approx(
                0.001, rel=1e-6, abs=0
            )
        assert result.geometry["volume_mm3"] == pytest.approx(1e-9, rel=1e-6, abs=0)

    @pytest.mark.parametrize("file_type", ["stl", "3mf"], ids=["stl", "3mf"])
    def test_refuses_volume_with_inconsistent_winding(self, tmp_path, file_type):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces[0] = mesh.faces[0][::-1]
        path = tmp_path / f"inconsistent.{file_type}"
        encoded = {
            "stl": mesh.export(file_type="stl"),
            "3mf": three_mf(meshes={1: mesh}),
        }[file_type]
        path.write_bytes(encoded)

        result = mesh_isolation.generate(
            _request(path, file_type=file_type, include_fingerprint=False)
        )

        assert result.geometry["volume_mm3"] is None

    def test_preserves_other_measurements_with_inconsistent_winding(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces[0] = mesh.faces[0][::-1]
        path = tmp_path / "inconsistent.stl"
        path.write_bytes(mesh.export(file_type="stl"))

        result = mesh_isolation.generate(_request(path, include_fingerprint=False))

        assert result.geometry["bbox_x_mm"] == 10.0
        assert result.geometry["bbox_y_mm"] == 10.0
        assert result.geometry["bbox_z_mm"] == 10.0
        assert result.geometry["triangle_count"] == 12

    @pytest.mark.parametrize(
        "transform", [None, "-1 0 0 0 1 0 0 0 1 0 0 0"], ids=["original", "reflected"]
    )
    def test_preserves_consistently_oriented_cube_volume(self, tmp_path, transform):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        path = tmp_path / "cube.3mf"
        path.write_bytes(three_mf(meshes={1: mesh}, build=((1, transform),)))

        result = mesh_isolation.generate(
            _request(path, file_type="3mf", include_fingerprint=False)
        )

        assert result.geometry["volume_mm3"] == pytest.approx(1000)

    def test_keeps_negative_metadata_volume_unknown(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.invert()
        path = tmp_path / "reversed.stl"
        path.write_bytes(mesh.export(file_type="stl"))

        result = mesh_isolation.generate(_request(path, include_fingerprint=False))

        assert result.geometry["volume_mm3"] is None

    def test_preserves_reversed_fingerprint_volume_magnitude(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.invert()
        path = tmp_path / "reversed.stl"
        path.write_bytes(mesh.export(file_type="stl"))

        result = mesh_isolation.generate(_request(path))

        assert result.fingerprint_result.records[0].values["volume"] == pytest.approx(
            1000
        )

    def test_preserves_fingerprint_winding_diagnosis(self, tmp_path):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces[0] = mesh.faces[0][::-1]
        path = tmp_path / "inconsistent.stl"
        path.write_bytes(mesh.export(file_type="stl"))

        result = mesh_isolation.generate(_request(path))

        values = result.fingerprint_result.records[0].values
        assert values["volume"] is None
        assert values["volume_reason"] == "inconsistent_winding"

    def test_open_mesh_keeps_unknown_volume_without_refusing_geometry(self, tmp_path):
        from app.modules.media.mesh_contracts import GeometryReady

        path = tmp_path / "open.obj"
        path.write_text("v 0 0 1\nv 10 0 1\nv 0 10 1\nf 1 2 3\n")
        result = mesh_isolation.generate(
            _request(path, file_type="obj", include_fingerprint=False)
        )
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["triangle_count"] == 1
        assert result.geometry["volume_mm3"] is None

    def test_closed_stl_retains_its_solid_volume(self, tmp_path):
        from app.modules.media.mesh_contracts import GeometryReady
        from tests.factories.geometry import tetrahedron

        path = tmp_path / "closed.stl"
        path.write_bytes(tetrahedron().export(file_type="stl"))
        result = mesh_isolation.generate(
            _request(path, file_type="stl", include_fingerprint=False)
        )
        assert isinstance(result.geometry_outcome, GeometryReady)
        assert result.geometry["volume_mm3"] == pytest.approx(1000.0)


class TestStepCapacityOwnership:
    def test_fingerprint_worker_does_not_access_application_database(
        self, db_session, monkeypatch
    ):
        from sqlmodel import select

        from app.db.models import CapacityReservation
        from tests.paths import FIXTURES_DIR

        monkeypatch.setitem(
            _overlay, "db_url", "sqlite:////missing-worker-db/worker.sqlite"
        )
        result = mesh_isolation.generate(
            _request(FIXTURES_DIR / "cascadio_material.stp", file_type="step")
        )
        assert result.geometry["triangle_count"] > 0
        assert result.fingerprint_result is not None
        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert db_session.exec(select(CapacityReservation)).all() == []

    def test_parent_releases_capacity_after_worker_refusal(
        self, db_session, monkeypatch
    ):
        from sqlmodel import select

        from app.db.models import CapacityReservation
        from tests.paths import FIXTURES_DIR

        admitted = []
        original = mesh_isolation.subprocess.Popen

        def spawn(*args, **kwargs):
            admitted.extend(
                row.operation_id for row in db_session.exec(select(CapacityReservation))
            )
            return original(*args, **kwargs)

        monkeypatch.setattr(mesh_isolation.subprocess, "Popen", spawn)
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)
        with pytest.raises(mesh_isolation.MeshWorkerError):
            mesh_isolation.generate(
                _request(FIXTURES_DIR / "cascadio_material.stp", file_type="step")
            )
        db_session.expire_all()
        assert db_session.exec(select(CapacityReservation)).all() == []
        assert len(admitted) == 1
        assert admitted[0].startswith("step-tessellation:")


class TestTelemetry:
    def test_metadata_only_preserves_phase_stats(self, tmp_path: Path) -> None:
        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())

        result = mesh_isolation.generate(
            _request(path, include_thumbnail=False, include_fingerprint=False)
        )

        phases = {stat.phase.value: stat for stat in result.phase_stats}
        assert phases["load"].input_bytes == path.stat().st_size
        assert phases["measurements"].triangle_count == 12
        assert phases["measurements"].elapsed_ns > 0
        assert "render" not in phases

    def test_exposes_parent_resource_cost(self, tmp_path: Path) -> None:
        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())

        result = mesh_isolation.generate(_request(path, include_fingerprint=False))

        assert result.supervision is not None
        assert result.supervision.elapsed_ns > 0
        assert result.supervision.peak_tree_rss_bytes > 0
        assert result.supervision.reply_bytes > len(result.image)
        assert result.supervision.exit_cause.value == "exited_zero"

    def test_parent_exports_metadata_only_phases(self, tmp_path: Path) -> None:
        from app.core.metrics import registry

        path = tmp_path / "cube.stl"
        path.write_bytes(content.binary_stl())
        labels = {"phase": "measurements", "outcome": "completed"}
        before = (
            registry.get_sample_value(
                "printstash_mesh_phase_duration_seconds_count", labels
            )
            or 0
        )

        mesh_isolation.generate(
            _request(path, include_thumbnail=False, include_fingerprint=False)
        )

        assert (
            registry.get_sample_value(
                "printstash_mesh_phase_duration_seconds_count", labels
            )
            == before + 1
        )


@pytest.fixture
def abandoned_caller_output(tmp_path):
    import subprocess
    import sys
    import time

    from app.modules.media.worker_bootstrap import reap_descendants
    from tests.paths import BACKEND_DIR

    directory = tmp_path / "printstash-mesh-caller"
    directory.mkdir()
    ready, pids = tmp_path / "ready", tmp_path / "pids"
    script = (
        "from pathlib import Path; "
        "from app.modules.media.mesh_isolation import supervise_result; "
        "from app.modules.media.worker_bootstrap import command, WorkerLifecycle; "
        f"supervise_result(command('tests.fakes.mesh_bootstrap_probe', "
        f"['tree_wait', {str(pids)!r}, {str(ready)!r}], 268435456), "
        "memory_budget=268435456, timeout_seconds=60, "
        f"temporary_directory=Path({str(directory)!r}), lifecycle=WorkerLifecycle.GUARDED)"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], cwd=BACKEND_DIR)

    def await_ready():
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "fault worker did not announce its caller-owned outputs"

    def abandon():
        parent.kill()
        parent.wait()
        deadline = time.monotonic() + 5
        while directory.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return directory.exists()

    try:
        await_ready()
        yield directory, abandon
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait()
        if pids.exists():
            import json

            reap_descendants(json.loads(pids.read_text())[0])


class TestSuperviseResult:
    @pytest.mark.parametrize("limit", [0, -1, True, 32 * 1024**2 + 1])
    def test_rejects_invalid_reply_budget(self, limit):
        with pytest.raises(ValueError, match="reply limit"):
            mesh_isolation.supervise_result(
                ["/not-a-worker"],
                memory_budget=1024**3,
                timeout_seconds=5,
                reply_limit=limit,
            )

    def test_enforces_caller_reply_budget(self):
        import sys

        from app.modules.media.mesh_telemetry import WorkerExitCause

        with pytest.raises(mesh_isolation.MeshWorkerError) as raised:
            mesh_isolation.supervise_result(
                [sys.executable, "-c", "import os; os.write(1,bytes(128))"],
                memory_budget=1024**3,
                timeout_seconds=5,
                reply_limit=64,
            )
        assert raised.value.supervision is not None
        assert raised.value.supervision.exit_cause is WorkerExitCause.REPLY_LIMIT

    @pytest.mark.parametrize(
        "code", [3, 4, 7], ids=["face-cap", "invalid", "unavailable"]
    )
    def test_preserves_accepted_domain_exit(self, code):
        import sys

        result = mesh_isolation.supervise_result(
            [sys.executable, "-c", f"raise SystemExit({code})"],
            memory_budget=128 * 1024**2,
            timeout_seconds=5,
            accepted_exit_codes=frozenset({0, 3, 4, 7}),
        )

        assert result.returncode == code
        assert result.stats.exit_cause.value == "exited_nonzero"

    def test_keeps_caller_output_for_decoding(self, tmp_path):
        import sys

        output = tmp_path / "result.bin"
        result = mesh_isolation.supervise_result(
            [
                sys.executable,
                "-c",
                "import os; from pathlib import Path; "
                "Path(os.environ['TMPDIR'], 'result.bin').write_bytes(b'native-output')",
            ],
            memory_budget=128 * 1024**2,
            timeout_seconds=5,
            temporary_directory=tmp_path,
        )

        assert result.payload == b""
        assert output.read_bytes() == b"native-output"
        assert tmp_path.is_dir()

    def test_refuses_unaccepted_domain_exit(self):
        import sys

        with pytest.raises(mesh_isolation.MeshWorkerError) as error:
            mesh_isolation.supervise_result(
                [sys.executable, "-c", "raise SystemExit(3)"],
                memory_budget=128 * 1024**2,
                timeout_seconds=5,
            )

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_abandonment_cleans_caller_output(self, abandoned_caller_output):
        directory, abandon = abandoned_caller_output
        assert (directory / "partial.stl").read_bytes() == b"partial native output"

        remains = abandon()

        assert remains is False

    @pytest.mark.parametrize(
        "codes",
        [
            frozenset(),
            frozenset({-9}),
            frozenset({mesh_isolation.RESOURCE_EXIT}),
            frozenset({True}),
        ],
        ids=["empty", "signal", "bootstrap-resource", "boolean"],
    )
    def test_rejects_invalid_domain_exit_policy(self, codes):
        with pytest.raises(ValueError, match="accepted worker exit codes"):
            mesh_isolation.supervise_result(
                [],
                memory_budget=128 * 1024**2,
                timeout_seconds=5,
                accepted_exit_codes=codes,
            )


class TestPreparedWorkerResult:
    def test_releases_native_permit_before_publication(self, tmp_path, monkeypatch):
        import hashlib
        import os

        from app.modules.media import stl_isolation
        from app.runtime.native_runtime import current_permit

        source = tmp_path / "cube.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        supervise = mesh_isolation.supervise_result
        permits = []

        def observed(command, **kwargs):
            permit = kwargs["permit"]
            assert current_permit() is permit
            permits.append(permit)
            assert os.fstat(permit.fileno).st_size > 0
            return supervise(command, **kwargs)

        monkeypatch.setattr(mesh_isolation, "supervise_result", observed)
        with stl_isolation.prepare_stl(
            source, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
        ) as prepared:
            assert current_permit() is None
            assert len(permits) == 1
            with pytest.raises(ValueError, match="closed"):
                _ = permits[0].fileno
            assert len(prepared.stream.read()) == prepared.size
            prepared.verify()
            assert prepared.path.exists()
        assert current_permit() is None
        assert not prepared.path.exists()

    def test_does_not_release_a_borrowed_native_permit(self, tmp_path):
        import hashlib
        import os

        from app.modules.media import stl_isolation
        from app.modules.media.native_execution import admission
        from app.runtime.native_admission import Resources
        from app.runtime.native_runtime import current_permit

        source = tmp_path / "borrowed.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        capacity = Resources(1, mesh_isolation.memory_budget_bytes())
        with admission(capacity, capacity, checkpoint=lambda: None) as parent:
            descriptor = parent.fileno
            with stl_isolation.prepare_stl(
                source, file_type="obj", expected_sha256=source_sha, workspace=tmp_path
            ) as prepared:
                assert current_permit() is parent
                assert os.fstat(descriptor).st_size > 0
                assert len(prepared.stream.read()) == prepared.size
                prepared.verify()
            assert current_permit() is parent
            assert os.fstat(descriptor).st_size > 0
        assert current_permit() is None
        with pytest.raises(ValueError, match="closed"):
            _ = parent.fileno

    def test_refuses_directory_replacement(self, tmp_path, monkeypatch):
        import hashlib
        import shutil

        from app.modules.media.native_budget import GeometryWork, MeshSource

        source = tmp_path / "cube.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        supervise = mesh_isolation.supervise_result

        def replaced(command, **kwargs):
            reply = supervise(command, **kwargs)
            directory = kwargs["temporary_directory"]
            retained = directory.with_name(directory.name + "-retained")
            directory.rename(retained)
            directory.mkdir()
            shutil.copyfile(retained / "mesh.stl", directory / "mesh.stl")
            return reply

        monkeypatch.setattr(mesh_isolation, "supervise_result", replaced)
        spec = {
            "path": str(source),
            "file_type": "obj",
            "expected_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }

        with pytest.raises(mesh_isolation.MeshWorkerError) as error:
            with mesh_isolation.prepared_worker_result(
                "app.modules.media.stl_worker",
                spec,
                workspace=tmp_path,
                sources=(MeshSource(source, "obj"),),
                work=GeometryWork(),
            ):
                pytest.fail("replacement directory yielded")

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestFreshWorkerDependencies:
    @pytest.fixture(autouse=True)
    def child_application_dependencies(self, tmp_path, monkeypatch):
        """An external import refusal affects fresh children, never the parent."""
        boundary = tmp_path / "child-import-boundary"
        boundary.mkdir()
        (boundary / "sitecustomize.py").write_text(
            "import importlib.abc\n"
            "import sys\n"
            "class ParentOnlyDependencies(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname in ('app.modules.media.mesh_observability', 'sqlalchemy'):\n"
            "            raise ImportError('parent_only_application_dependency_in_child')\n"
            "        return None\n"
            "sys.meta_path.insert(0, ParentOnlyDependencies())\n"
        )
        monkeypatch.setenv(
            "PYTHONPATH",
            os.pathsep.join(
                (str(boundary), str(BACKEND_DIR), os.environ.get("PYTHONPATH", ""))
            ),
        )
        monkeypatch.setenv("OPENBLAS_NUM_THREADS", "1")
        monkeypatch.setenv("OMP_NUM_THREADS", "1")
        monkeypatch.setitem(_overlay, "mesh_worker_timeout_seconds", 20)

    @pytest.fixture
    def startup_source(self, tmp_path, file_type):
        mesh = trimesh.creation.box(extents=[10, 20, 30])
        payload = (
            mesh.export(file_type="stl")
            if file_type == "stl"
            else three_mf(meshes={1: mesh})
        )
        source = tmp_path / f"cube.{file_type}"
        source.write_bytes(payload)
        return source

    @pytest.mark.parametrize("file_type", ["stl", "3mf"], ids=["stl", "3mf"])
    def test_preserves_mesh_outputs_without_parent_dependencies(
        self, startup_source, file_type, caplog
    ):
        original = startup_source.read_bytes()
        request = _request(
            startup_source,
            file_type=file_type,
            include_fingerprint=False,
            width=64,
            height=48,
        )
        expected = ThumbnailEngine().generate(request)
        caplog.set_level(logging.INFO, logger="app.modules.media.mesh_observability")
        caplog.clear()

        actual = mesh_isolation.generate(request)

        assert isinstance(actual.geometry_outcome, GeometryReady)
        assert actual.geometry == expected.geometry
        assert actual.geometry == {
            "triangle_count": 12,
            "bbox_x_mm": 10.0,
            "bbox_y_mm": 20.0,
            "bbox_z_mm": 30.0,
            "volume_mm3": 6000.0,
        }
        assert actual.volume == expected.volume == VolumeMeasured(6000.0)
        assert actual.image is not None
        assert actual.image == expected.image
        assert actual.coverage == expected.coverage
        assert actual.failure_reason is None
        assert startup_source.read_bytes() == original
        records = {
            prefix: [
                json.loads(record.getMessage().removeprefix(prefix + " "))
                for record in caplog.records
                if record.getMessage().startswith(prefix + " ")
            ]
            for prefix in ("mesh_admission", "mesh_supervision", "mesh_phases")
        }
        assert len(records["mesh_admission"]) == 1
        assert records["mesh_admission"][0]["outcome"] == "admitted"
        assert len(records["mesh_supervision"]) == 1
        assert records["mesh_supervision"][0]["reply_bytes"] > 0
        assert len(records["mesh_phases"]) == 1
        assert records["mesh_phases"][0]["stages"]
        assert all(
            entry["process_id"] == os.getpid()
            for entries in records.values()
            for entry in entries
        )

    @pytest.mark.parametrize("file_type", ["stl", "3mf"], ids=["stl", "3mf"])
    def test_keeps_following_work_available_after_typed_refusal(
        self, startup_source, file_type, tmp_path
    ):
        malformed = tmp_path / "malformed.stl"
        damaged = content.binary_stl(triangles=2) + b"unframed trailing bytes"
        malformed.write_bytes(damaged)
        original = startup_source.read_bytes()

        refused = mesh_isolation.generate(
            _request(malformed, include_fingerprint=False, width=64, height=48)
        )
        following = mesh_isolation.generate(
            _request(
                startup_source,
                file_type=file_type,
                include_fingerprint=False,
                width=64,
                height=48,
            )
        )

        assert refused.geometry_outcome == GeometryRefused(
            ThumbnailFailureReason.INVALID_SOURCE
        )
        assert refused.image is None
        assert refused.failure_reason is ThumbnailFailureReason.INVALID_SOURCE
        assert isinstance(following.geometry_outcome, GeometryReady)
        assert following.volume == VolumeMeasured(6000.0)
        assert following.geometry["triangle_count"] == 12
        assert following.image is not None
        assert following.failure_reason is None
        assert malformed.read_bytes() == damaged
        assert startup_source.read_bytes() == original
