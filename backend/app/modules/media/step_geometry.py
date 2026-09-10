"""OpenCascade B-rep extraction, imported only inside a disposable worker."""

from __future__ import annotations

import math
from pathlib import Path


class StepGeometryError(ValueError):
    pass


def tessellate(source: Path, *, triangle_limit: int) -> tuple:
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    from OCP.TopAbs import (
        TopAbs_EDGE,
        TopAbs_FACE,
        TopAbs_REVERSED,
        TopAbs_SOLID,
        TopAbs_VERTEX,
    )
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    if not 1 <= triangle_limit <= 2_000_000:
        raise StepGeometryError("geometry_work_limit")
    reader = STEPControl_Reader()
    if reader.ReadFile(str(source)) != IFSelect_RetDone:
        raise StepGeometryError("invalid_step")
    units = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    reader.FileUnits(*units)
    source_units = [
        units[0].Value(index).ToCString() for index in range(1, units[0].Length() + 1)
    ]
    reader.SetSystemLengthUnit(1.0)  # OCCT uses millimetres for a system unit of 1.
    if reader.TransferRoots() == 0:
        raise StepGeometryError("invalid_step")
    shape = reader.OneShape()
    if shape.IsNull():
        raise StepGeometryError("invalid_step")
    counts = {}
    for name, kind in (
        ("faces", TopAbs_FACE),
        ("edges", TopAbs_EDGE),
        ("vertices", TopAbs_VERTEX),
        ("solids", TopAbs_SOLID),
    ):
        elements = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, kind, elements)
        counts[name] = elements.Extent()
        if counts[name] > 100_000:
            raise StepGeometryError("geometry_work_limit")
    valid = bool(BRepCheck_Analyzer(shape).IsValid())
    volume, volume_reason = None, "invalid_brep" if not valid else "not_solid"
    if valid and counts["solids"]:
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(shape, properties, True, False, False)
        measured = float(properties.Mass())
        if math.isfinite(measured) and measured > 0:
            volume, volume_reason = measured, None
    recipe = {
        "version": "ocp-7.9.3-mm-v1",
        "length_unit": "millimeter",
        "linear_deflection_mm": 0.05,
        "angular_deflection_radians": 0.35,
        "relative": False,
        "parallel": False,
    }
    mesher = BRepMesh_IncrementalMesh(shape, 0.05, False, 0.35, False)
    if not mesher.IsDone():
        raise StepGeometryError("tessellation_failed")
    vertices, triangles = [], []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            raise StepGeometryError("tessellation_incomplete")
        if (
            len(triangles) + mesh.NbTriangles() > triangle_limit
            or len(vertices) + mesh.NbNodes() > 600_000
        ):
            raise StepGeometryError("geometry_work_limit")
        offset = len(vertices)
        transform = location.Transformation()
        for index in range(1, mesh.NbNodes() + 1):
            point = mesh.Node(index).Transformed(transform)
            vertices.append((point.X(), point.Y(), point.Z()))
        for index in range(1, mesh.NbTriangles() + 1):
            triangle = [node - 1 + offset for node in mesh.Triangle(index).Get()]
            if face.Orientation() == TopAbs_REVERSED:
                triangle.reverse()
            triangles.append(triangle)
        explorer.Next()
    if not triangles:
        raise StepGeometryError("invalid_step")
    return (
        np.asarray(vertices, dtype=np.float64),
        np.asarray(triangles, dtype=np.int64),
        {
            "counts": counts,
            "valid": valid,
            "volume_mm3": volume,
            "volume_unavailable": volume_reason,
            "source_units": source_units,
            "recipe": recipe,
        },
    )
