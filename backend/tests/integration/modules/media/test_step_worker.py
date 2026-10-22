"""The native STEP result is a readable mesh plus finite millimetre evidence."""

import json

import numpy as np
import pytest

from app.modules.media import step_worker
from tests.paths import FIXTURES_DIR


class TestBrepProtocol:
    def test_writes_safe_native_result(self, tmp_path, monkeypatch):
        destination = tmp_path / "mesh.npz"
        monkeypatch.setenv("PRINTSTASH_STEP_BREP", "1")
        monkeypatch.setenv("PRINTSTASH_STEP_TRIANGLE_LIMIT", "200000")
        monkeypatch.setattr(
            step_worker.sys,
            "argv",
            [
                "step_worker",
                str(FIXTURES_DIR / "cascadio_material.stp"),
                str(destination),
            ],
        )

        assert step_worker.main() == 0

        with np.load(destination, allow_pickle=False) as mesh:
            assert mesh["vertices"].shape[1] == mesh["faces"].shape[1] == 3
            assert len(mesh["faces"]) > 0
            assert np.isfinite(mesh["vertices"]).all()
            assert np.max(np.ptp(mesh["vertices"], axis=0)) > 10
        evidence = json.loads(destination.with_suffix(".json").read_text())
        assert evidence["source_units"] == ["metre"]
        assert evidence["volume_mm3"] > 0
        assert evidence["recipe"]["linear_deflection_mm"] == pytest.approx(0.05)
        json.dumps(evidence, allow_nan=False)


class TestBoundedTessellation:
    @pytest.mark.parametrize("suffix", ["stp", "step"])
    def test_native_conversion_fits_the_small_worker_budget(
        self, tmp_path, monkeypatch, suffix
    ):
        import trimesh

        from app.modules.media.mesh_isolation import supervise
        from app.modules.media.worker_bootstrap import command

        source = tmp_path / f"native.{suffix}"
        source.write_bytes((FIXTURES_DIR / "cascadio_material.stp").read_bytes())
        destination = tmp_path / "converted.glb"
        monkeypatch.setenv("PRINTSTASH_STEP_BREP", "0")
        monkeypatch.setenv("PRINTSTASH_STEP_TRIANGLE_LIMIT", "200000")
        budget = 512 * 1024**2

        supervise(
            command(
                "app.modules.media.step_worker",
                [str(source), str(destination)],
                budget,
            ),
            memory_budget=budget,
            timeout_seconds=10,
        )

        mesh = trimesh.load_mesh(destination, process=False)
        assert len(mesh.faces) > 0
        assert np.isfinite(mesh.vertices).all()
        assert len(mesh.faces) == 12
        assert mesh.extents == pytest.approx(
            [0.0699461326, 0.0254, 0.060356412], rel=1e-6
        )
        assert set(tmp_path.iterdir()) == {source, destination}


@pytest.fixture
def ocp_cylinder(tmp_path):
    """A 10 mm radius cylinder: its facets show the tessellation's deflection."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = tmp_path / "cylinder.step"
    writer = STEPControl_Writer()
    assert (
        writer.Transfer(BRepPrimAPI_MakeCylinder(10, 20).Shape(), STEPControl_AsIs)
        == IFSelect_RetDone
    )
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path


def _sagitta_mm(mesh, radius_mm: float) -> float:
    """Largest gap between a cylinder's facets and its surface, in millimetres."""
    vertices = np.asarray(mesh.vertices)
    radial = np.hypot(vertices[:, 0], vertices[:, 1])
    rim = vertices[np.isclose(radial, radial.max(), rtol=1e-6)]
    angles = np.unique(np.round(np.arctan2(rim[:, 1], rim[:, 0]), 9))
    step = np.max(np.diff(np.concatenate([angles, [angles[0] + 2 * np.pi]])))
    return radius_mm * (1 - np.cos(step / 2))


class TestConvert:
    def test_tessellates_curved_surfaces_within_the_shared_deflection(
        self, tmp_path, ocp_cylinder
    ):
        import trimesh

        from app.modules.media.step_geometry import LINEAR_DEFLECTION_MM

        destination = tmp_path / "mesh.glb"

        status = step_worker.convert(
            ocp_cylinder, destination, 200_000, include_brep=False
        )

        assert status == 0
        sagitta = _sagitta_mm(trimesh.load_mesh(destination), radius_mm=10)
        assert sagitta <= LINEAR_DEFLECTION_MM * 1.001

    def test_tessellates_coarser_than_the_previous_hundredth_of_a_millimetre(
        self, tmp_path, ocp_cylinder
    ):
        # Preview and measurement meshes used 0.01 mm, several times the
        # triangles a 640 px preview or millimetre measurements can show.
        import trimesh

        destination = tmp_path / "mesh.glb"

        step_worker.convert(ocp_cylinder, destination, 200_000, include_brep=False)

        assert _sagitta_mm(trimesh.load_mesh(destination), radius_mm=10) > 0.01

    def test_refuses_a_mesh_above_the_triangle_ceiling(self, tmp_path, ocp_cylinder):
        assert (
            step_worker.convert(
                ocp_cylinder, tmp_path / "mesh.glb", 10, include_brep=False
            )
            == 3
        )
