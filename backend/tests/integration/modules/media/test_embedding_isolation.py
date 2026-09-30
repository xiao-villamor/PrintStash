"""Embedding views rendered in a worker are the views the API process would render.

The similarity embedding pass loads and rasterises six views of every model in the
library, so it is heavy work over files the library chose rather than any request.
Running it in a disposable worker must not change a single pixel it hands the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from printstash_core.mesh.similarity import GeometryError

from app.modules.media import embedding_isolation, geometry_analysis, mesh_isolation
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.thumbnail_engine import ThumbnailFailureReason
from tests.factories.geometry import tetrahedron


@pytest.fixture
def part(tmp_path):
    path = tmp_path / "part.stl"
    path.write_bytes(tetrahedron().export(file_type="stl"))
    return path


def _views(function, path, **overrides):
    values = dict(
        file_type="stl", component_index=0, image_size=64, triangle_cap=200_000
    )
    values.update(overrides)
    return function(path, **values)


class TestEmbeddingViews:
    def test_matches_the_in_process_render(self, part):
        isolated = _views(embedding_isolation.embedding_views, part)
        direct = _views(geometry_analysis.embedding_views, part)

        assert isolated == direct
        assert len(isolated) == 6

    def test_a_geometry_failure_keeps_its_code(self, part):
        with pytest.raises(GeometryError) as raised:
            _views(embedding_isolation.embedding_views, part, image_size=8)

        assert raised.value.code == "invalid_view_budget"

    def test_the_callers_component_reaches_the_worker(self, part):
        with pytest.raises(GeometryError) as raised:
            _views(embedding_isolation.embedding_views, part, component_index=9)

        assert raised.value.code == "component_unavailable"

    def test_finds_a_source_given_as_a_path_relative_to_the_caller(
        self, part, monkeypatch
    ):
        monkeypatch.chdir(part.parent)

        views = _views(embedding_isolation.embedding_views, Path("part.stl"))

        assert len(views) == 6

    def test_a_worker_over_its_memory_budget_raises_instead_of_dying_with_it(
        self, part, monkeypatch
    ):
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: 8 * 1024**2)

        with pytest.raises(MeshWorkerError) as raised:
            _views(embedding_isolation.embedding_views, part)

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
