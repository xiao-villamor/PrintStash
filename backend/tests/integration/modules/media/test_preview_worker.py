"""Preview batches load complete geometry within the permit the publisher claims."""

import hashlib

import pytest
import trimesh

from app.core.config import _overlay
from app.modules.media import native_execution, preview_worker
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_isolation import MeshWorkerError
from app.modules.media.native_budget import (
    MeshSource,
    RasterCodec,
    RasterWork,
    Resources,
    estimate_sources,
)
from app.modules.media.preview_contracts import HEIGHT, WIDTH, PreviewManifest
from tests.factories.geometry import three_mf

# More placed faces than the generic 3MF weight admits under a lib3mf claim.
FACES = 327_680


@pytest.fixture(scope="module")
def dense_package(tmp_path_factory):
    path = tmp_path_factory.mktemp("preview-3mf") / "dense.3mf"
    path.write_bytes(three_mf(meshes={1: trimesh.creation.icosphere(subdivisions=7)}))
    return path


def _produce_within_claim(path, directory):
    work = RasterWork(WIDTH, HEIGHT, 1, RasterCodec.WEBP)
    claim = estimate_sources(
        Resources(1, 64 * 1024**3), (MeshSource(path, "3mf"),), work=work
    )
    with native_execution.admission(claim, claim, checkpoint=lambda: None):
        return preview_worker.produce(
            path,
            "3mf",
            hashlib.sha256(path.read_bytes()).hexdigest(),
            directory,
        )


class TestProduce:
    def test_renders_a_dense_3mf_within_its_admitted_claim(
        self, dense_package, tmp_path
    ):
        manifest = PreviewManifest.model_validate_json(
            _produce_within_claim(dense_package, tmp_path)
        )
        assert len(manifest.views) == 6
        assert (tmp_path / "views.bin").stat().st_size == manifest.size

    def test_follows_the_configured_triangle_ceiling(
        self, dense_package, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", FACES - 1)
        with pytest.raises(preview_worker.EXPECTED_FAILURES) as refused:
            _produce_within_claim(dense_package, tmp_path)
        assert preview_worker.failure_reason(refused.value) is (
            ThumbnailFailureReason.RESOURCE_LIMIT
        )

    def test_follows_the_configured_source_size(
        self, dense_package, tmp_path, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "mesh_max_load_mb", 1)
        with pytest.raises(MeshWorkerError) as refused:
            _produce_within_claim(dense_package, tmp_path)
        assert refused.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
