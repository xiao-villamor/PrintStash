"""Generate a frozen, small geometry contract corpus without parser dependencies.

Expectations describe target capabilities, never observed implementation success.
Run ``python -m scripts.mesh_benchmark_corpus --output-dir /tmp/mesh-corpus``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import zipfile
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal


class FileType(StrEnum):
    STL = "stl"
    THREE_MF = "3mf"


class RefusalRule(StrEnum):
    TRUNCATED_FACET = "truncated_facet"
    COUNT_MISMATCH = "count_mismatch"
    INCOMPLETE_ASCII_FACET = "incomplete_ascii_facet"
    COMPONENT_CYCLE = "component_cycle"
    REQUIRED_EXTENSION = "unsupported_required_extension"


@dataclass(frozen=True)
class ExpectedGeometry:
    triangle_count: int
    bbox_mm: tuple[float, float, float]
    volume_mm3: float
    relative_tolerance: float = 1e-6
    absolute_tolerance: float = 0.0
    outcome: Literal["accept"] = field(default="accept", init=False)


@dataclass(frozen=True)
class ExpectedRefusal:
    rule: RefusalRule
    outcome: Literal["refuse"] = field(default="refuse", init=False)


@dataclass(frozen=True)
class CorpusFixture:
    filename: str
    file_type: FileType
    sha256: str
    input_bytes: int
    source_faces: int
    resources: int | None
    instances: int | None
    expectation: ExpectedGeometry | ExpectedRefusal
    origin: str = "generated:scripts.mesh_benchmark_corpus/v1"
    license: str = "AGPL-3.0"


@dataclass(frozen=True)
class CorpusManifest:
    fixtures: tuple[CorpusFixture, ...]
    schema_version: int = 1
    corpus_id: str = "mesh-contract-v1"
    expectation_scope: str = "target_contract_not_observed_compliance"


_VERTICES = (
    (0, 0, 0),
    (1, 0, 0),
    (1, 1, 0),
    (0, 1, 0),
    (0, 0, 1),
    (1, 0, 1),
    (1, 1, 1),
    (0, 1, 1),
)
_FACES = (
    (0, 2, 1),
    (0, 3, 2),
    (4, 5, 6),
    (4, 6, 7),
    (0, 1, 5),
    (0, 5, 4),
    (3, 7, 6),
    (3, 6, 2),
    (0, 4, 7),
    (0, 7, 3),
    (1, 2, 6),
    (1, 6, 5),
)


def _binary_cube() -> bytes:
    header = b"PrintStash analytic cube v1".ljust(80, b"\x00")
    facets = b"".join(
        struct.pack(
            "<12fH",
            0,
            0,
            0,
            *(coordinate * 20 for index in face for coordinate in _VERTICES[index]),
            0,
        )
        for face in _FACES
    )
    return header + struct.pack("<I", 12) + facets


def _ascii_cube() -> bytes:
    lines = ["solid cube"]
    for face in _FACES:
        lines.extend(["  facet normal 0 0 0", "\touter loop"])
        for index in face:
            lines.append(
                "\t  vertex "
                + " ".join(f"{coordinate * 20:.6e}" for coordinate in _VERTICES[index])
            )
        lines.extend(["\tendloop", "  endfacet"])
    return ("\r\n".join([*lines, "endsolid cube"]) + "\r\n").encode()


def _three_mf(
    *,
    unit: str = "millimeter",
    size: int = 20,
    unused_vertex: bool = False,
    multiple_build: bool = False,
    reflected: bool = False,
    cycle: bool = False,
    unsupported_required: bool = False,
) -> bytes:
    vertices = "".join(
        f'<vertex x="{x * size}" y="{y * size}" z="{z * size}"/>'
        for x, y, z in _VERTICES
    )
    if unused_vertex:
        vertices += '<vertex x="1000000000" y="1000000000" z="1000000000"/>'
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in _FACES)
    resources = f'<object id="1" type="model"><mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object>'
    build = '<item objectid="1"/>'
    if multiple_build:
        build += '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 40 0 0"/>'
    if reflected:
        build = '<item objectid="1" transform="-1 0 0 0 1 0 0 0 1 0 0 0"/>'
    if cycle:
        resources += '<object id="2" type="model"><components><component objectid="2"/></components></object>'
        build = '<item objectid="2"/>'
    extra = (
        ' xmlns:unknown="https://example.invalid/unsupported" requiredextensions="unknown"'
        if unsupported_required
        else ""
    )
    model = f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="{unit}"{extra}><resources>{resources}</resources><build>{build}</build></model>'
    members = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        "3D/3dmodel.model": model,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in members.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content.encode())
    return output.getvalue()


def build_contract_corpus(root: Path) -> CorpusManifest:
    """Write synthetic fixtures; all physical expectations come from cube algebra."""
    root.mkdir(parents=True, exist_ok=True)
    fixtures: list[CorpusFixture] = []
    cube = ExpectedGeometry(12, (20, 20, 20), 8000)

    def write(
        name: str,
        data: bytes,
        expectation: ExpectedGeometry | ExpectedRefusal,
        *,
        faces: int = 12,
        resources: int | None = None,
        instances: int | None = 1,
    ) -> None:
        (root / name).write_bytes(data)
        fixtures.append(
            CorpusFixture(
                name,
                FileType.THREE_MF if name.endswith(".3mf") else FileType.STL,
                hashlib.sha256(data).hexdigest(),
                len(data),
                faces,
                resources,
                instances,
                expectation,
            )
        )

    binary = _binary_cube()
    write("cube-binary.stl", binary, cube)
    write(
        "binary-solid.stl", b"solid binary cube".ljust(80, b"\x00") + binary[80:], cube
    )
    write(
        "binary-truncated.stl",
        binary[:-10],
        ExpectedRefusal(RefusalRule.TRUNCATED_FACET),
        faces=11,
    )
    write(
        "binary-count-mismatch.stl",
        binary[:80] + struct.pack("<I", 13) + binary[84:],
        ExpectedRefusal(RefusalRule.COUNT_MISMATCH),
    )
    write("ascii-whitespace.stl", _ascii_cube(), cube)
    write(
        "ascii-incomplete.stl",
        b"solid incomplete\nfacet normal 0 0 0\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nendloop\nendfacet\nendsolid incomplete\n",
        ExpectedRefusal(RefusalRule.INCOMPLETE_ASCII_FACET),
        faces=0,
    )
    write("cube-mm.3mf", _three_mf(), cube, resources=1)
    write(
        "cube-inch.3mf",
        _three_mf(unit="inch", size=1),
        ExpectedGeometry(12, (25.4, 25.4, 25.4), 25.4**3),
        resources=1,
    )
    write(
        "cube-micron.3mf",
        _three_mf(unit="micron", size=1),
        ExpectedGeometry(12, (0.001, 0.001, 0.001), 1e-9),
        resources=1,
    )
    write(
        "multiple-build.3mf",
        _three_mf(multiple_build=True),
        ExpectedGeometry(24, (60, 20, 20), 16000),
        resources=1,
        instances=2,
    )
    write("unused-vertex.3mf", _three_mf(unused_vertex=True), cube, resources=1)
    write("reflected-build.3mf", _three_mf(reflected=True), cube, resources=1)
    write(
        "cyclic-components.3mf",
        _three_mf(cycle=True),
        ExpectedRefusal(RefusalRule.COMPONENT_CYCLE),
        resources=2,
        instances=None,
    )
    write(
        "unsupported-required.3mf",
        _three_mf(unsupported_required=True),
        ExpectedRefusal(RefusalRule.REQUIRED_EXTENSION),
        resources=1,
    )
    return CorpusManifest(tuple(fixtures))


def verify_manifest(root: Path, manifest: CorpusManifest) -> None:
    for fixture in manifest.fixtures:
        data = (root / fixture.filename).read_bytes()
        if (
            len(data) != fixture.input_bytes
            or hashlib.sha256(data).hexdigest() != fixture.sha256
        ):
            raise ValueError(f"fixture content differs: {fixture.filename}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_contract_corpus(args.output_dir)
    verify_manifest(args.output_dir, manifest)
    encoded = json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n"
    (args.output_dir / "manifest.json").write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
