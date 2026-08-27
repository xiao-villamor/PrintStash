"""Real mesh fixtures must load and render through the production pipeline.

A failure here means an operator's supported CAD input can no longer produce
the geometry metadata and preview needed by the library.
"""

from __future__ import annotations

from app.services import mesh_processing
from tests.paths import TEST_DATA_DIR


def test_load_mesh_renders_real_step_fixture() -> None:
    path = TEST_DATA_DIR / "cascadio_material.stp"

    mesh = mesh_processing._load_mesh(path)
    geometry, thumbnail = mesh_processing.analyze_mesh(path)

    assert mesh is not None
    assert len(mesh.faces) > 0
    assert geometry["triangle_count"] == len(mesh.faces)
    assert thumbnail is not None
    assert thumbnail.startswith(mesh_processing._PNG_MAGIC)
