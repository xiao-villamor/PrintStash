"""Full builds retain physical units and B-rep evidence in bounded STEP workers."""

import numpy as np
import pytest

from app.modules.media import mesh_processing
from app.modules.media.thumbnail_engine import ThumbnailEngine, ThumbnailRequest
from tests.paths import FIXTURES_DIR


@pytest.fixture
def ocp_box(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    path = tmp_path / "box.step"
    writer = STEPControl_Writer()
    assert (
        writer.Transfer(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), STEPControl_AsIs)
        == IFSelect_RetDone
    )
    assert writer.Write(str(path)) == IFSelect_RetDone
    return path


class TestStepGeometry:
    def test_uses_shared_dense_budget_for_step(self, ocp_box, monkeypatch):
        from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0)
        native_popen = mesh_processing.subprocess.Popen
        limits = []

        def observed_worker(command, **kwargs):
            limits.append(int(kwargs["env"]["PRINTSTASH_STEP_TRIANGLE_LIMIT"]))
            return native_popen(command, **kwargs)

        monkeypatch.setattr(mesh_processing.subprocess, "Popen", observed_worker)

        mesh = mesh_processing._load_step_mesh_isolated(ocp_box, include_brep=True)

        assert mesh.metadata["brep"]["volume_mm3"] == pytest.approx(6000)
        assert limits == [MAX_ANALYSIS_FACES]

    def test_extracts_brep_through_isolated_worker(self, ocp_box):
        result = ThumbnailEngine().generate(
            ThumbnailRequest(ocp_box, include_fingerprint=True, width=64)
        )
        assert result.fingerprint_result.state == "ready"
        whole = result.fingerprint_result.records[0]
        brep = whole.values["brep"]
        assert brep["counts"] == {"faces": 6, "edges": 12, "vertices": 8, "solids": 1}
        assert brep["volume_mm3"] == pytest.approx(6000)
        assert whole.values["volume"] == pytest.approx(6000)
        assert brep["recipe"]["linear_deflection_mm"] == 0.05
        assert result.image is not None

    def test_converts_source_metres_to_mm(
        self,
    ):
        mesh = mesh_processing._load_step_mesh_isolated(
            FIXTURES_DIR / "cascadio_material.stp", include_brep=True
        )
        assert mesh.metadata["brep"]["source_units"] == ["metre"]
        assert np.max(mesh.extents) > 10
        assert mesh.metadata["brep"]["volume_mm3"] == pytest.approx(
            abs(mesh.volume), rel=1e-8
        )

    def test_preserves_step_assembly_locations(self, tmp_path):
        from OCP.BRep import BRep_Builder
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Trsf, gp_Vec
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS_Compound

        box = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
        transform = gp_Trsf()
        transform.SetTranslation(gp_Vec(50, 0, 0))
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        builder.Add(compound, box)
        builder.Add(compound, box.Moved(TopLoc_Location(transform)))
        writer = STEPControl_Writer()
        writer.Transfer(compound, STEPControl_AsIs)
        path = tmp_path / "assembly.step"
        writer.Write(str(path))

        mesh = mesh_processing._load_step_mesh_isolated(path, include_brep=True)
        assert mesh.extents.tolist() == pytest.approx([60, 20, 30])
        assert mesh.metadata["brep"]["counts"]["solids"] == 2
        assert mesh.metadata["brep"]["volume_mm3"] == pytest.approx(12000)

    def test_refuses_brep_triangle_overflow(self, ocp_box):
        from app.modules.media.step_geometry import StepGeometryError, tessellate

        with pytest.raises(StepGeometryError, match="geometry_work_limit"):
            tessellate(ocp_box, triangle_limit=1)

    def test_invalid_step_is_contained(self, tmp_path):
        path = tmp_path / "invalid.step"
        path.write_bytes(b"not a STEP model")
        result = ThumbnailEngine().generate(
            ThumbnailRequest(path, include_fingerprint=True)
        )
        assert result.fingerprint_result.state in ("failed", "unsupported")
        assert result.fingerprint_result.failure_code in (
            "invalid_step",
            "step_unavailable",
        )


class TestNativeTessellation:
    def test_exports_outward_wound_solid(self, ocp_box):
        import trimesh

        from app.modules.media.step_geometry import tessellate

        vertices, faces, brep = tessellate(ocp_box, triangle_limit=100)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces)

        assert mesh.is_watertight
        assert mesh.is_winding_consistent
        assert mesh.volume == pytest.approx(6000)
        assert brep["volume_mm3"] == pytest.approx(mesh.volume)
        assert brep["recipe"]["parallel"] is False

    @pytest.mark.parametrize("limit", [0, 2000001, True, 100.0])
    def test_rejects_invalid_face_budget(self, ocp_box, limit):
        from app.modules.media.step_geometry import StepGeometryError, tessellate

        with pytest.raises(StepGeometryError, match="geometry_work_limit"):
            tessellate(ocp_box, triangle_limit=limit)

    def test_rejects_invalid_step_document(self, tmp_path):
        from app.modules.media.step_geometry import StepGeometryError, tessellate

        path = tmp_path / "invalid.step"
        path.write_bytes(b"invalid")

        with pytest.raises(StepGeometryError, match="invalid_step"):
            tessellate(path, triangle_limit=100)

    def test_open_face_has_no_solid_volume(self, tmp_path):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from app.modules.media.step_geometry import tessellate

        face = BRepBuilderAPI_MakeFace(
            gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0, 10, 0, 20
        ).Face()
        writer = STEPControl_Writer()
        assert writer.Transfer(face, STEPControl_AsIs) == IFSelect_RetDone
        path = tmp_path / "open-face.step"
        assert writer.Write(str(path)) == IFSelect_RetDone
        vertices, triangles, brep = tessellate(path, triangle_limit=100)
        assert len(vertices) == 4
        assert len(triangles) == 2
        assert brep["counts"]["solids"] == 0
        assert brep["volume_mm3"] is None
        assert brep["volume_unavailable"] == "not_solid"

    def test_rejects_document_without_transferable_geometry(self, tmp_path):
        from app.modules.media.step_geometry import StepGeometryError, tessellate

        path = tmp_path / "empty.step"
        path.write_text(
            "ISO-10303-21;\nHEADER;\n"
            "FILE_DESCRIPTION(('empty'), '2;1');\n"
            "FILE_NAME('empty.step','2026-01-01T00:00:00',(''),(''),'','','');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
            "ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
        )

        with pytest.raises(StepGeometryError, match="invalid_step"):
            tessellate(path, triangle_limit=100)

    def test_wire_cannot_supply_surface_geometry(self, tmp_path):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
        from OCP.gp import gp_Pnt
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        from app.modules.media.step_geometry import StepGeometryError, tessellate

        wire = BRepBuilderAPI_MakePolygon(
            gp_Pnt(0, 0, 0), gp_Pnt(10, 0, 0), gp_Pnt(10, 10, 0), True
        ).Wire()
        writer = STEPControl_Writer()
        assert writer.Transfer(wire, STEPControl_AsIs) == IFSelect_RetDone
        path = tmp_path / "wire.step"
        assert writer.Write(str(path)) == IFSelect_RetDone
        with pytest.raises(StepGeometryError, match="invalid_step"):
            tessellate(path, triangle_limit=100)

    def test_equal_brep_counts_do_not_prove_equivalence(self, ocp_box, tmp_path):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
        from printstash_core.mesh.similarity.verification import verify_meshes

        from app.modules.media.step_geometry import tessellate

        writer = STEPControl_Writer()
        assert (
            writer.Transfer(BRepPrimAPI_MakeBox(5, 20, 60).Shape(), STEPControl_AsIs)
            == IFSelect_RetDone
        )
        path = tmp_path / "different.step"
        assert writer.Write(str(path)) == IFSelect_RetDone
        a, fa, brep_a = tessellate(ocp_box, triangle_limit=100)
        b, fb, brep_b = tessellate(path, triangle_limit=100)
        assert brep_a["counts"] == brep_b["counts"]
        assert brep_a["volume_mm3"] == pytest.approx(brep_b["volume_mm3"])
        evidence = verify_meshes(a, fa, b, fb, sample_points=256)
        assert evidence.exact_equivalence is False
        assert evidence.evidence_class not in ("identical_geometry", "rescaled_variant")


class TestStepWorkerContainment:
    @pytest.mark.parametrize("failure", ["timeout", "memory", "unavailable"])
    def test_contains_native_worker_failure(
        self, db_session, ocp_box, monkeypatch, failure
    ):
        import sys
        from pathlib import Path

        from printstash_core.mesh.similarity import GeometryError
        from sqlmodel import select

        from app.core.config import _overlay
        from app.db.models import CapacityReservation

        native_popen = mesh_processing.subprocess.Popen
        processes, directories = [], []

        # A real child supplies the native fault; the production watchdog owns
        # termination, error projection and temporary capacity release.
        def faulty_worker(command, **kwargs):
            directories.append(Path(command[-1]).parent)
            child = native_popen(
                [
                    sys.executable,
                    "-c",
                    "raise SystemExit(7)"
                    if failure == "unavailable"
                    else "import time; time.sleep(60)",
                ],
                **kwargs,
            )
            processes.append(child)
            return child

        monkeypatch.setattr(mesh_processing.subprocess, "Popen", faulty_worker)
        monkeypatch.setitem(
            _overlay, "mesh_step_timeout_seconds", 0.1 if failure == "timeout" else 5
        )
        if failure == "memory":
            monkeypatch.setattr(
                mesh_processing, "_step_memory_budget_bytes", lambda: 1024
            )
        code = {
            "timeout": "tessellation_timeout",
            "memory": "worker_oom",
            "unavailable": "step_unavailable",
        }[failure]
        with pytest.raises(GeometryError, match=code):
            mesh_processing._load_step_mesh_isolated(ocp_box, include_brep=True)
        assert all(process.poll() is not None for process in processes)
        assert all(not path.exists() for path in directories)
        assert db_session.exec(select(CapacityReservation)).all() == []
        assert db_session.exec(select(1)).one() == 1
