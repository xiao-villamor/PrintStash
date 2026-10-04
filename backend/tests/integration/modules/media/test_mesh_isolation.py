"""The isolated worker answers exactly as the in-process engine would.

Moving geometry work into a child changes where it runs, not what it produces:
the image, the measured geometry and the similarity fingerprint have to survive
the process boundary unchanged, or every model would quietly change on upgrade.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import trimesh
from PIL import Image

from app.core.config import _overlay
from app.modules.media import mesh_isolation
from app.modules.media.fingerprints import FingerprintResultState
from app.modules.media.mesh_contracts import (
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailStrategy,
)
from app.modules.media.thumbnail_engine import ThumbnailEngine
from tests.factories import content
from tests.factories.geometry import three_mf


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
        assert isolated.complete == direct.complete
        assert isolated.failure_reason == direct.failure_reason
        assert isolated.fingerprint_result == direct.fingerprint_result
        assert isolated.fingerprint_result is not None
        assert isolated.fingerprint_result.state is FingerprintResultState.READY

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

    def test_a_3mf_that_repeats_one_part_beyond_the_budget_fails_inside_the_worker(
        self, tmp_path, monkeypatch
    ):
        """#259 end to end: a small file whose placements expand past the budget.

        The parent's runtime overrides reach the worker, so it is held to a 1,000-face
        budget. Whatever the engine decides, the parent gets an
        answer instead of a dead process.
        """
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1000)
        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        path = tmp_path / "instanced.3mf"
        # 400 placements of a 4-face part: ~25 KiB of XML the size estimate prices
        # at ~350 faces, against 1,600 once expanded.
        placements = tuple((1, f"1 0 0 0 1 0 0 0 1 {i * 5} 0 0") for i in range(400))
        path.write_bytes(three_mf(build=placements))

        result = mesh_isolation.generate(
            _request(path, file_type="3mf", include_fingerprint=False)
        )

        assert result.image is None
        assert result.failure_reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert result.geometry["triangle_count"] is None

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

        assert result.fingerprint_result.algorithm_version == "geometry-v4-sh5f4577c4"
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
