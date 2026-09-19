"""Deterministic synthetic M00 inputs, plus unchanged repository format fixtures.

Run with the baseline's locked backend Python environment from backend/.
The destination must be new. This is corpus preparation, outside timed runs.
"""

import argparse
import hashlib
import importlib.metadata
import io
import json
import zipfile
from pathlib import Path

import trimesh

from tests.factories.geometry import tetrahedron, three_mf


def package_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=6)
    return buffer.getvalue()


def generate(
    root: Path, *, small_count: int = 128, large_subdivisions: int = 7
) -> dict:
    """Build fresh bounded inputs and record every source digest."""
    if not 1 <= small_count <= 128 or not 1 <= large_subdivisions <= 7:
        raise ValueError("Corpus dimensions exceed the declared work bounds")
    root.mkdir(parents=True, exist_ok=False)
    fixtures = Path(__file__).resolve().parents[1] / "tests/fixtures"
    manifest = {
        "kind": "synthetic scaling corpus plus existing slicer/STEP fixtures",
        "limitations": "Not a user-library corpus. Optional inference assets and queue recovery are separate workloads.",
        "versions": {
            name: importlib.metadata.version(name) for name in ("trimesh", "numpy")
        },
        "small_count": small_count,
        "large_subdivisions": large_subdivisions,
        "archives": [],
    }

    def write(name, entries, *, similarity=False):
        content = package_bytes(entries)
        (root / (name + ".zip")).write_bytes(content)
        manifest["archives"].append(
            {
                "name": name + ".zip",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "similarity": similarity,
                "files": [
                    {
                        "name": entry,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for entry, data in sorted(entries.items())
                ],
            }
        )

    with zipfile.ZipFile(io.BytesIO(three_mf())) as nested:
        normalized_3mf = package_bytes(
            {name: nested.read(name) for name in nested.namelist()}
        )
    write(
        "small",
        {
            "tetrahedron.stl": tetrahedron().export(file_type="stl"),
            "tetrahedron.3mf": normalized_3mf,
        },
    )
    small = {}
    for index in range(small_count):
        mesh = tetrahedron()
        mesh.apply_scale(1 + index / small_count)
        small[f"part-{index:03d}.stl"] = mesh.export(file_type="stl")
    write("many-small", small)
    large = {}
    for index in range(4):
        mesh = trimesh.creation.icosphere(
            subdivisions=large_subdivisions, radius=20 + index
        )
        mesh.apply_scale([1, 1 + index / 10, 1 + index / 5])
        large[f"large-{index}.stl"] = mesh.export(file_type="stl")
    write("large-mesh", {"large-0.stl": large["large-0.stl"]})
    write("large-archive", {**small, **large})
    write(
        "gcode",
        {
            name: (fixtures / name).read_bytes()
            for name in (
                "real_prusa_mk4_spatula.gcode",
                "real_orca_ender3_benchy.gcode",
                "bgcode/prusaslicer.bgcode",
            )
        },
    )
    write("step", {"material.stp": (fixtures / "cascadio_material.stp").read_bytes()})
    similarity = {}
    for index in range(4):
        mesh = trimesh.creation.icosphere(subdivisions=1, radius=10 + index)
        mesh.apply_scale([1, 1 + index / 10, 1 + index / 5])
        similarity[f"candidate-{index}.stl"] = mesh.export(file_type="stl")
    write("similarity", similarity, similarity=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
