"""The render facade preserves the parent's numerical import boundary."""

import os
import subprocess
import sys
from pathlib import Path


class TestRenderImport:
    def test_module_import_keeps_numpy_out_of_parent(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import app.modules.media.mesh_render; assert 'numpy' not in sys.modules",
            ],
            cwd=Path(__file__).resolve().parents[4],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        assert result.returncode == 0, result.stderr


class TestVisualRender:
    def test_small_geometry_roundtrips_without_cpu_normals(self, tmp_path, monkeypatch):
        from app.modules.media import compute_geometry, mesh_render
        from app.modules.media.gpu_normals import VisualRender
        from app.modules.media.visual_loading import load_visual_mesh
        from app.runtime.compute import client

        source = tmp_path / "triangle.obj"
        source.write_bytes(b"v 0 0 0\nv 10 0 0\nv 0 10 0\nf 1 2 3")
        monkeypatch.setattr(client, "available", lambda **kwargs: True)
        prepared = mesh_render.prepare_preview_render(load_visual_mesh(source, ".obj"))

        payload = compute_geometry.encode(prepared, 512, 384, (None,), False)
        decoded = compute_geometry.decode(payload)

        assert isinstance(prepared, VisualRender)
        assert prepared._canonical is None
        assert b"gpu-visual-v2" in payload
        recovered, width, height, views, matte = decoded
        assert isinstance(recovered, VisualRender)
        assert recovered.vertices.tolist() == prepared.vertices.tolist()
        assert recovered.face_count == 1
        assert (width, height, views, matte) == (512, 384, [None], False)
