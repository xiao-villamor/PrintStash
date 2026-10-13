"""Pairwise verification in a worker answers exactly as it does in process.

Verification loads two meshes and compares them, so it is the heaviest single
step of a similarity run. Running it in a disposable worker must not change the
evidence it produces, the geometry failures it reports, or which inputs reach it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.core.config import _overlay
from app.modules.media import geometry_analysis, mesh_isolation, verification_isolation
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_facts import FingerprintFailureCode
from app.modules.media.mesh_isolation import MeshWorkerError
from tests.factories.geometry import tetrahedron


@pytest.fixture
def pair(tmp_path):
    first, second = tmp_path / "a.stl", tmp_path / "b.stl"
    first.write_bytes(tetrahedron().export(file_type="stl"))
    moved = tetrahedron()
    moved.apply_translation([35, -8, 2])
    second.write_bytes(moved.export(file_type="stl"))
    return first, second


def _verify(function, first, second, **overrides):
    values = dict(first_type="stl", second_type="stl", sample_points=256)
    values.update(overrides)
    return function(first, second, **values)


class TestVerifyPaths:
    def test_matches_the_in_process_verification(self, pair):
        isolated = _verify(verification_isolation.verify_paths, *pair)
        direct = _verify(geometry_analysis.verify_paths, *pair)

        assert isolated == direct
        assert isolated.evidence_class is not None

    def test_a_geometry_failure_keeps_its_code(self, tmp_path):
        broken = tmp_path / "broken.stl"
        broken.write_bytes(b"not a mesh")

        with pytest.raises(GeometryError) as raised:
            _verify(verification_isolation.verify_paths, broken, broken)

        assert raised.value.code == FingerprintFailureCode.INVALID_SOURCE.value

    def test_the_callers_arguments_reach_the_worker(self, pair):
        """A cap below the analysis minimum is refused by the code that owns it."""
        with pytest.raises(GeometryError) as raised:
            _verify(verification_isolation.verify_paths, *pair, triangle_cap=50)

        assert raised.value.code == "invalid_triangle_cap"

    def test_finds_sources_given_as_paths_relative_to_the_caller(
        self, pair, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)

        isolated = _verify(
            verification_isolation.verify_paths, Path("a.stl"), Path("b.stl")
        )

        assert isolated.evidence_class is not None

    def test_the_worker_honours_the_parents_runtime_overrides(
        self, tmp_path, monkeypatch
    ):
        """A limit the parent lowered at runtime must bind the worker too.

        An OBJ over the triangle budget is refused outright (an STL would be
        sampled instead), and the unchanged run is the control that shows the
        override, not the file, made the difference.
        """
        part = tmp_path / "part.obj"
        part.write_text(tetrahedron().export(file_type="obj"))
        objects = dict(first_type="obj", second_type="obj")
        verification_isolation.verify_paths(
            part, part, sample_points=256, **objects
        )  # inside the budget: no error

        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)

        with pytest.raises(GeometryError) as raised:
            verification_isolation.verify_paths(
                part, part, sample_points=256, **objects
            )

        assert raised.value.code == "geometry_work_limit"

    def test_a_worker_over_its_memory_budget_raises_instead_of_dying_with_it(
        self, pair, monkeypatch
    ):
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)

        with pytest.raises(MeshWorkerError) as raised:
            _verify(verification_isolation.verify_paths, *pair)

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
