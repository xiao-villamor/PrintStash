"""Real files produce review derivatives without a second mesh load or source edits."""

import hashlib
import weakref
from dataclasses import asdict

import numpy as np
import pytest
from printstash_core.mesh.similarity import fingerprint_mesh

from app.core.config import _overlay
from app.modules.media import mesh_loading, mesh_policy
from app.modules.media.fingerprints import SH_BASIS_DIGEST, FingerprintResultState
from app.modules.media.mesh_contracts import GeometryReady, ThumbnailRequest
from app.modules.media.thumbnail_engine import ThumbnailEngine
from tests.factories import content
from tests.factories.geometry import tetrahedron, three_mf


class TestFingerprintExtraction:
    def test_ignores_unreferenced_3mf_vertices(self, tmp_path):
        mesh = tetrahedron()
        original_path = tmp_path / "original.3mf"
        original_path.write_bytes(three_mf(meshes={1: mesh}))
        mesh.vertices = np.concatenate([mesh.vertices, [[1e6, -1e6, 1e6]]])
        padded_path = tmp_path / "padded.3mf"
        padded_path.write_bytes(three_mf(meshes={1: mesh}))

        original = ThumbnailEngine().generate(
            ThumbnailRequest(
                original_path, include_fingerprint=True, include_thumbnail=False
            )
        )
        padded = ThumbnailEngine().generate(
            ThumbnailRequest(
                padded_path, include_fingerprint=True, include_thumbnail=False
            )
        )

        assert original.fingerprint_result.state is FingerprintResultState.READY
        assert padded.fingerprint_result == original.fingerprint_result

    def test_releases_loaded_mesh_before_reclaim(self, tmp_path, monkeypatch):
        path = tmp_path / "single.stl"
        path.write_bytes(tetrahedron().export(file_type="stl"))
        original_load = mesh_loading.load_mesh
        original_reclaim = mesh_policy.reclaim_memory
        loaded = []
        released = []

        def observed_load(*args, **kwargs):
            mesh = original_load(*args, **kwargs)
            loaded.append(weakref.ref(mesh))
            return mesh

        def observed_reclaim():
            original_reclaim()
            released.append(all(reference() is None for reference in loaded))

        monkeypatch.setattr(mesh_loading, "load_mesh", observed_load)
        monkeypatch.setattr(mesh_policy, "reclaim_memory", observed_reclaim)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True, include_thumbnail=False)
        )

        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert loaded
        assert released[-1] is True

    def test_preserves_transformed_component_measurements(self, tmp_path):
        path = tmp_path / "scaled.3mf"
        path.write_bytes(three_mf(build=((1, "2 0 0 0 2 0 0 0 2 0 0 0"),)))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True, include_thumbnail=False)
        )

        whole, component = result.fingerprint_result.records
        assert whole.values["volume"] == pytest.approx(8000)
        assert component.values["volume"] == pytest.approx(1000)

    def test_preserves_single_component_descriptors(self, tmp_path):
        path = tmp_path / "single.stl"
        path.write_bytes(tetrahedron().export(file_type="stl"))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True, include_thumbnail=False)
        )

        whole, component = result.fingerprint_result.records
        assert whole.values == component.values
        assert whole.values is not component.values
        assert whole.values["recipe"] is not component.values["recipe"]

    def test_avoids_discarded_similarity_thumbnails(self, tmp_path):
        path = tmp_path / "single.stl"
        path.write_bytes(tetrahedron().export(file_type="stl"))
        progress = []

        result = ThumbnailEngine().generate(
            ThumbnailRequest(
                path,
                include_fingerprint=True,
                include_thumbnail=False,
                report=progress.append,
            )
        )

        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert result.image is None
        assert result.failure_reason is None
        assert "rendering_thumbnail" not in progress

    @pytest.mark.parametrize("format", ["stl", "stl_ascii", "obj", "3mf"])
    def test_extracts_lite_format(self, tmp_path, format):
        mesh = tetrahedron()
        suffix = "stl" if format == "stl_ascii" else format
        encoded = three_mf() if format == "3mf" else mesh.export(file_type=format)
        path = tmp_path / f"shape.{suffix}"
        path.write_bytes(encoded.encode() if isinstance(encoded, str) else encoded)
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, include_fingerprint=True)
        )

        assert result.image is not None
        assert result.fingerprint_result.state is FingerprintResultState.READY
        whole, component = result.fingerprint_result.records
        assert whole.component_index == 0
        assert component.component_index == 1
        assert whole.values["volume"] == pytest.approx(1000)
        assert len(whole.values["d2_blob"]) == len(whole.values["sh_blob"]) == 256
        assert len(whole.values["view_blob"]) == 48
        assert whole.values["recipe"]["sh_basis"] == SH_BASIS_DIGEST
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash

    @pytest.mark.parametrize("format", ["stl", "stl_ascii", "obj", "3mf"])
    def test_preserves_keys_across_file_formats(self, tmp_path, format):
        mesh = tetrahedron()
        expected = fingerprint_mesh(mesh.vertices, mesh.faces)
        assert expected.keys is not None
        suffix = "stl" if format == "stl_ascii" else format
        encoded = three_mf() if format == "3mf" else mesh.export(file_type=format)
        path = tmp_path / f"reexport.{suffix}"
        path.write_bytes(encoded.encode() if isinstance(encoded, str) else encoded)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, include_fingerprint=True)
        )

        assert result.fingerprint_result.records[0].values["keys"] == asdict(
            expected.keys
        )

    def test_reuses_loaded_mesh_for_descriptors(self, tmp_path, count_source_reads):
        # The source exceeds the admission probe so full parsing reads are
        # distinguishable from bounded size/header inspection.
        mesh = tetrahedron().subdivide().subdivide()
        path = tmp_path / "part.stl"
        path.write_bytes(mesh.export(file_type="stl"))
        source_bytes = path.stat().st_size
        reads = count_source_reads(path)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, include_fingerprint=True)
        )

        assert result.geometry_outcome == GeometryReady()
        assert result.geometry == pytest.approx(
            {
                "bbox_x_mm": 10.0,
                "bbox_y_mm": 20.0,
                "bbox_z_mm": 30.0,
                "triangle_count": 64,
                "volume_mm3": 1000.0,
            }
        )
        assert result.image is not None
        assert result.failure_reason is None
        assert result.fingerprint_result.state is FingerprintResultState.READY
        whole, component = result.fingerprint_result.records
        assert whole.values["volume"] == pytest.approx(1000.0)
        assert component.values["volume"] == pytest.approx(1000.0)
        # Descriptors and preview consume retained geometry, not another source
        # pass. P11's separate bounded header probe remains legitimate I/O.
        assert [count for count in reads if count >= source_bytes] == [source_bytes]

    def test_embedded_preview_still_computes_geometry(self, tmp_path):
        path = tmp_path / "embedded.3mf"
        preview = content.png()
        path.write_bytes(three_mf(extras={"Metadata/thumbnail.png": preview}))

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_geometry=False, include_fingerprint=True)
        )

        assert result.image == preview
        assert result.fingerprint_result.state is FingerprintResultState.READY
        assert result.fingerprint_result.records[0].values["face_count"] == 4

    def test_large_stl_remains_explicitly_partial(self, tmp_path, monkeypatch):
        path = tmp_path / "large.stl"
        path.write_bytes(tetrahedron().export(file_type="stl"))
        monkeypatch.setattr(mesh_policy, "exceeds_cap", lambda *a, **k: True)
        monkeypatch.setitem(_overlay, "mesh_stream_timeout_seconds", 5)

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, width=64, include_fingerprint=True)
        )

        assert result.fingerprint_result.state is FingerprintResultState.PARTIAL
        assert len(result.fingerprint_result.records) == 1
        values = result.fingerprint_result.records[0].values
        assert (
            values["keys"] is values["volume"] is values["euler_characteristic"] is None
        )
        assert len(values["d2_blob"]) == 256
        assert not values["recipe"]["complete_geometry"]

    def test_invalid_fingerprint_keeps_embedded_preview(self, tmp_path):
        mesh = tetrahedron()
        mesh.vertices[0, 0] = np.nan
        preview = content.png()
        path = tmp_path / "invalid.3mf"
        path.write_bytes(
            three_mf(meshes={1: mesh}, extras={"Metadata/thumbnail.png": preview})
        )

        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True)
        )

        assert result.image == preview
        assert result.fingerprint_result.state is FingerprintResultState.FAILED
        assert result.fingerprint_result.failure_code == "nonfinite_geometry"
