"""OBJ loading reuses owned identity buffers without changing durable mesh outputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from app.modules.media import mesh_loading, mesh_measurements, mesh_render


class TestLoadMesh:
    def test_preserves_canonical_obj_outputs(self, tmp_path: Path) -> None:
        source = trimesh.creation.box(extents=[10, 25, 50])
        source.apply_translation([100, 50, 25])
        path = tmp_path / "part.obj"
        source.export(path, file_type="obj")
        canonical = trimesh.load_scene(path, process=False).dump()[0]

        loaded = mesh_loading.load_mesh(path)

        assert loaded is not None
        np.testing.assert_array_equal(loaded.vertices, canonical.vertices)
        np.testing.assert_array_equal(loaded.faces, canonical.faces)
        assert mesh_measurements.geometry_from_mesh(loaded) == (
            mesh_measurements.geometry_from_mesh(canonical)
        )
        assert mesh_render.render_mesh_thumbnail(loaded, path.name, 80, 60) == (
            mesh_render.render_mesh_thumbnail(canonical, path.name, 80, 60)
        )
