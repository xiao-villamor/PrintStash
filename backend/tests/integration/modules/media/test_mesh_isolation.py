"""The isolated worker answers exactly as the in-process engine would.

Moving geometry work into a child changes where it runs, not what it produces:
the image, the measured geometry and the similarity fingerprint have to survive
the process boundary unchanged, or every model would quietly change on upgrade.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import _overlay
from app.modules.media import mesh_isolation
from app.modules.media.thumbnail_engine import (
    ThumbnailEngine,
    ThumbnailFailureReason,
    ThumbnailRequest,
)
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
        assert isolated.fingerprint_result.state == "ready"

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
        assert result.fingerprint_result.state == "ready"
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
