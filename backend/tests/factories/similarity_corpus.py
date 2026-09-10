"""Licensed, design-separated cases; derivatives never cross their design's split."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import numpy as np
import trimesh

ROOT = (
    Path(__file__).resolve().parents[2]
    / "packages/printstash-core/tests/fixtures/similarity"
)
CASES = ROOT / "pairs.csv"


def cases(split: str | None = None) -> list[dict[str, str]]:
    with CASES.open() as stream:
        return [
            row
            for row in csv.DictReader(stream)
            if split is None or row["split"] == split
        ]


def mesh_for(base: str, variant: str) -> trimesh.Trimesh:
    if base == "chiral-tetrahedron":
        from tests.factories.geometry import tetrahedron

        mesh = tetrahedron()
    elif base.startswith("washer-"):
        resolution = int(base.split("-")[-1])
        mesh = trimesh.creation.annulus(
            r_min=8, r_max=12, height=3, sections=resolution
        )
    else:
        mesh = trimesh.load_mesh(ROOT / base)
    if variant == "original":
        return mesh
    if variant in ("stl", "stl_ascii", "obj"):
        payload = mesh.export(file_type=variant)
        return trimesh.load_mesh(
            io.BytesIO(payload.encode() if isinstance(payload, str) else payload),
            file_type="stl" if variant == "stl_ascii" else variant,
        )
    if variant == "subdivision":
        return mesh.subdivide()
    if variant == "microrepair":
        mesh = mesh.subdivide().subdivide()
        mesh.update_faces(np.arange(len(mesh.faces)) != int(np.argmin(mesh.area_faces)))
        return mesh
    if variant == "quarter":
        return trimesh.creation.annulus(
            r_min=8, r_max=12, height=3, sections=int(base.split("-")[-1]) // 4
        )
    if variant == "hole_changed":
        return trimesh.creation.annulus(
            r_min=8.6, r_max=12, height=3, sections=int(base.split("-")[-1])
        )
    if variant.startswith("scale-"):
        return mesh.apply_scale(float(variant.split("-", 1)[1]))
    if variant == "mirror":
        return mesh.apply_scale([-1, 1, 1])
    if variant == "scaled_mirror":
        return mesh.apply_scale([-2, 2, 2])
    if variant == "rigid":
        return mesh.apply_transform(
            trimesh.transformations.rotation_matrix(0.731, [0.3, 0.7, 0.2])
        ).apply_translation([10, 70, -9])
    raise ValueError(f"unknown corpus variant: {variant}")


def provenance() -> dict:
    return json.loads((ROOT / "manifest.json").read_text())
