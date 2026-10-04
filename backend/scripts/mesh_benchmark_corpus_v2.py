"""Versioned target-contract corpus, with explicit full/download materialization.

Default generation is small and offline. Full grids are streamed in <=64 KiB
writes. Pinned real slicer binaries are downloaded only with an explicit flag.
V1 bytes and its frozen manifest remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from scripts.mesh_benchmark_corpus import build_contract_corpus as build_v1
from scripts.mesh_corpus_v2_contracts import (
    MAX_EXTERNAL_BYTES,
    CorpusFixture,
    CorpusManifest,
    Expectation,
    ExpectedCompatibility,
    ExpectedGeometry,
    ExpectedRefusal,
    ExternalReference,
    Family,
    FileType,
    Policy,
    Profile,
    RecoveryKind,
    RecoveryScenario,
    VolumeContract,
)
from scripts.mesh_corpus_v2_externals import (
    FETCH_CHUNK_BYTES,
    external_references,
    materialize_external,
)
from scripts.mesh_corpus_v2_geometry import (
    Mesh,
    ascii_mesh,
    binary,
    box,
    combine,
    duplicate_vertices,
    nonfinite_cube,
    permute,
    sphere,
    torus,
    write_grid,
)
from scripts.mesh_corpus_v2_scenes import UNIT_SCALE, SceneCase, scene

__all__ = (
    "MAX_EXTERNAL_BYTES",
    "CorpusFixture",
    "CorpusManifest",
    "ExpectedCompatibility",
    "ExpectedGeometry",
    "ExpectedRefusal",
    "Expectation",
    "ExternalReference",
    "Family",
    "FileType",
    "Policy",
    "Profile",
    "RecoveryKind",
    "RecoveryScenario",
    "VolumeContract",
    "FETCH_CHUNK_BYTES",
    "external_references",
    "materialize_external",
    "recovery_scenarios",
    "build_contract_corpus",
    "verify_manifest",
    "main",
)


def recovery_scenarios() -> tuple[RecoveryScenario, ...]:
    return (
        RecoveryScenario(
            RecoveryKind.CACHE_CORRUPTION,
            "Corrupt persisted derivative bytes after READY",
            "Hash verification rejects bytes; retry reconstructs representation from immutable source",
        ),
        RecoveryScenario(
            RecoveryKind.DISK_FULL,
            "Fail staging/adoption writes with ENOSPC",
            "No false READY; previous committed representation remains readable",
        ),
        RecoveryScenario(
            RecoveryKind.CHILD_KILLED,
            "SIGKILL child during native work",
            "Parent reaps child tree, retains measured failure cost, releases permits",
        ),
        RecoveryScenario(
            RecoveryKind.PARENT_KILLED,
            "SIGKILL parent after a durable output commit",
            "Committed output survives; new Job epoch resumes remaining durable work",
        ),
        RecoveryScenario(
            RecoveryKind.TIMEOUT,
            "Worker exceeds configured monotonic deadline",
            "Child tree is reaped; timeout cost retained; next work obtains permits",
        ),
        RecoveryScenario(
            RecoveryKind.CANCEL_WAITING,
            "Cancel Job while admission is pending",
            "No child begins; reservation is removed",
        ),
        RecoveryScenario(
            RecoveryKind.CANCEL_RUNNING,
            "Cancel Job during native work",
            "Child tree is reaped; no late publication",
        ),
        RecoveryScenario(
            RecoveryKind.CANCEL_PUBLISHING,
            "Cancel Job before publication fence",
            "Stale attempt cannot replace current representation; earlier READY survives",
        ),
        RecoveryScenario(
            RecoveryKind.RETRY,
            "Retry failed Job with a new epoch",
            "Current source/recipe wins; old attempt cannot publish or retire new intent",
        ),
        RecoveryScenario(
            RecoveryKind.REGENERATE,
            "Regenerate concurrently with old execution",
            "Source/recipe/generation fences reject old publication; new output remains",
        ),
    )


def _geometry(
    count: int = 12,
    bbox: tuple[float, float, float] = (20, 20, 20),
    volume: float | None = 8000,
) -> ExpectedGeometry:
    return ExpectedGeometry(
        count,
        bbox,
        volume,
        VolumeContract.UNKNOWN
        if volume is None
        else VolumeContract.CERTIFIED_MAGNITUDE,
    )


def build_contract_corpus(
    root: Path, *, full: bool = False, download_external: bool = False
) -> CorpusManifest:
    """Materialize requested profiles only; large inputs and egress are opt-in."""
    root.mkdir(parents=True, exist_ok=True)
    fixtures: list[CorpusFixture] = []
    old = build_v1(root)
    for entry in old.fixtures:
        expected: Expectation
        if entry.expectation.outcome == "accept":
            expected = _geometry(
                entry.expectation.triangle_count,
                entry.expectation.bbox_mm,
                entry.expectation.volume_mm3,
            )
        else:
            expected = ExpectedRefusal(Policy(entry.expectation.rule))
        family = (
            Family.ASCII
            if entry.filename.startswith("ascii")
            else Family.BINARY
            if entry.file_type == "stl"
            else Family.CORE
        )
        fixtures.append(
            CorpusFixture(
                entry.filename,
                FileType(entry.file_type),
                entry.sha256,
                entry.input_bytes,
                entry.source_faces,
                entry.resources,
                entry.instances,
                expected,
                family,
                origin=entry.origin,
                license=entry.license,
            )
        )

    def write(
        name: str,
        content: bytes | Callable[[BinaryIO], None],
        expectation: Expectation,
        family: Family,
        *,
        faces: int | None = 12,
        resources: int | None = None,
        instances: int | None = 1,
        profile: Profile = Profile.SMALL,
    ) -> None:
        path = root / name
        with path.open("wb") as output:
            if isinstance(content, bytes):
                output.write(content)
            else:
                content(output)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(FETCH_CHUNK_BYTES):
                size += len(chunk)
                digest.update(chunk)
        fixtures.append(
            CorpusFixture(
                name,
                FileType.THREE_MF if name.endswith(".3mf") else FileType.STL,
                digest.hexdigest(),
                size,
                faces,
                resources,
                instances,
                expectation,
                family,
                profile,
            )
        )

    cube = box()
    raw = binary(cube)
    write(
        "binary-trailing.stl",
        raw + b"unexpected trailing bytes",
        ExpectedRefusal(Policy.TRAILING),
        Family.BINARY,
    )
    write(
        "binary-unreliable-normals.stl",
        binary(cube, normal=(123, -77, 42)),
        _geometry(),
        Family.BINARY,
    )
    write(
        "binary-face-attributes.stl",
        binary(cube, attribute=65535),
        _geometry(),
        Family.BINARY,
    )
    write(
        "binary-degenerate.stl",
        binary(Mesh(cube.vertices, (*cube.faces, (0, 0, 0)))),
        _geometry(13, volume=None),
        Family.BINARY,
        faces=13,
    )
    for name, value in (("nan", float("nan")), ("inf", float("inf"))):
        write(
            f"binary-{name}.stl",
            binary(nonfinite_cube(value)),
            ExpectedRefusal(Policy.NONFINITE),
            Family.BINARY,
        )
    write(
        "ascii-empty.stl",
        b"",
        ExpectedRefusal(Policy.EMPTY),
        Family.ASCII,
        faces=0,
        instances=0,
    )
    write(
        "ascii-long-line.stl",
        ascii_mesh(cube, name="long-" + "x" * 8192),
        _geometry(),
        Family.ASCII,
    )
    write(
        "ascii-multiple-solids.stl",
        ascii_mesh(cube, name="first")
        + ascii_mesh(box(offset=(40, 0, 0)), name="second"),
        _geometry(24, (60, 20, 20), 16000),
        Family.ASCII,
        faces=24,
        instances=2,
    )

    geometry_cases = (
        ("sphere-octahedron", sphere(), _geometry(8, (2, 2, 2), 4 / 3)),
        ("torus-square", torus(), _geometry(32, (4, 4, 1), 12)),
        ("open-cube", Mesh(cube.vertices, cube.faces[:-2]), _geometry(10, volume=None)),
        (
            "reversed-winding",
            Mesh(cube.vertices, tuple((c, b, a) for a, b, c in cube.faces)),
            _geometry(volume=None),
        ),
        (
            "remote-tiny-component",
            combine(cube, box(size=(0.125, 0.125, 0.125), offset=(1000, 0, 0))),
            _geometry(24, (1000.125, 20, 20), 8000 + 0.125**3),
        ),
        ("thin-solid", box(size=(20, 20, 0.125)), _geometry(12, (20, 20, 0.125), 50)),
        (
            "flat-surface",
            Mesh(
                ((0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)), ((0, 1, 2), (0, 2, 3))
            ),
            _geometry(2, (20, 20, 0), None),
        ),
        (
            "overlapping-solids",
            combine(cube, box(offset=(10, 0, 0))),
            _geometry(24, (30, 20, 20), None),
        ),
        ("duplicate-vertices", duplicate_vertices(cube), _geometry()),
        (
            "nonmanifold-edge",
            Mesh(cube.vertices, (*cube.faces, cube.faces[0])),
            _geometry(13, volume=None),
        ),
    )
    for name, mesh, expected in geometry_cases:
        write(
            f"geometry-{name}.stl",
            binary(mesh),
            expected,
            Family.GEOMETRY,
            faces=len(mesh.faces),
        )

    refused = {
        SceneCase.EMPTY: Policy.EMPTY,
        SceneCase.INVALID: Policy.INDEX,
        SceneCase.DUPLICATE_ID: Policy.DUPLICATE_ID,
        SceneCase.CYCLE: Policy.CYCLE,
        SceneCase.SINGULAR: Policy.SINGULAR,
        SceneCase.EXPONENTIAL: Policy.EXPANSION,
        SceneCase.UNKNOWN_REQUIRED: Policy.REQUIRED,
    }
    production = {
        SceneCase.INTERNAL_PART,
        SceneCase.NESTED,
        SceneCase.CYCLE,
        SceneCase.REFLECTION,
        SceneCase.SINGULAR,
        SceneCase.EXPONENTIAL,
        SceneCase.UNKNOWN_REQUIRED,
    }
    slicer = {
        SceneCase.PREVIEW_VALID,
        SceneCase.PREVIEW_BROKEN,
        SceneCase.PREVIEW_ABSENT,
        SceneCase.PLATES,
        SceneCase.FOREIGN_METADATA,
        SceneCase.AUXILIARY,
    }
    precision = {
        SceneCase.SOURCE_TRANSLATION,
        SceneCase.LARGE_SCALE,
        SceneCase.PERMUTED,
    }
    resource_counts = {
        SceneCase.ROOT_RELATIONSHIP: 2,
        SceneCase.DUPLICATE_ID: 2,
        SceneCase.UNREFERENCED: 3,
        SceneCase.INTERNAL_PART: 2,
        SceneCase.NESTED: 3,
        SceneCase.CYCLE: 3,
        SceneCase.EXPONENTIAL: 22,
        SceneCase.AUXILIARY: 2,
    }
    source_counts = {
        SceneCase.EMPTY: 0,
        SceneCase.ROOT_RELATIONSHIP: 24,
        SceneCase.DUPLICATE_ID: 24,
        SceneCase.UNREFERENCED: 36,
        SceneCase.AUXILIARY: 24,
    }
    for case in SceneCase:
        if case is SceneCase.STANDARD:
            continue
        expectation: Expectation = (
            ExpectedRefusal(refused[case]) if case in refused else _geometry()
        )
        family = (
            Family.PRODUCTION
            if case in production
            else Family.SLICER
            if case in slicer
            else Family.PRECISION
            if case in precision
            else Family.LOAD
            if case in {SceneCase.INSTANCES, SceneCase.COMPRESSED}
            else Family.CORE
        )
        instances: int | None = None if case in refused else 1
        if case is SceneCase.NESTED:
            expectation, instances = _geometry(24, (90, 20, 20), 16000), 2
        elif case is SceneCase.REFLECTION:
            expectation, instances = _geometry(24, (80, 20, 20), 16000), 2
        elif case is SceneCase.PLATES:
            expectation, instances = _geometry(24, (60, 20, 20), 16000), 2
        elif case is SceneCase.AUXILIARY:
            expectation, instances = _geometry(24, (101, 20, 20), 8001), 2
        elif case is SceneCase.LARGE_SCALE:
            expectation = _geometry(12, (1e6, 1e6, 1e6), 1e18)
        elif case is SceneCase.INSTANCES:
            expectation, instances = (
                _geometry(12 * 2048, (2047 * 40 + 20, 20, 20), 8000 * 2048),
                2048,
            )
        write(
            f"scene-{case.value}.3mf",
            scene(case),
            expectation,
            family,
            faces=source_counts.get(case, 12),
            resources=resource_counts.get(case, 1),
            instances=instances,
        )
    for unit in UNIT_SCALE:
        write(
            f"precision-equivalent-{unit}.3mf",
            scene(SceneCase.STANDARD, unit=unit),
            _geometry(),
            Family.PRECISION,
            resources=1,
        )
    write(
        "precision-micron-solid.stl",
        binary(box(size=(0.001, 0.001, 0.001))),
        _geometry(12, (0.001, 0.001, 0.001), 1e-9),
        Family.PRECISION,
    )
    write(
        "precision-permuted.stl", binary(permute(cube)), _geometry(), Family.PRECISION
    )

    if full:
        for count, width, height in (
            (5000, 50, 50),
            (20000, 100, 100),
            (200000, 500, 200),
            (1000000, 1000, 500),
            (2000000, 1000, 1000),
        ):

            def emit(
                output: BinaryIO, width: int = width, height: int = height
            ) -> None:
                write_grid(output, width=width, height=height)

            write(
                f"load-{count}.stl",
                emit,
                _geometry(count, (width, height, 0), None),
                Family.LOAD,
                faces=count,
                profile=Profile.FULL,
            )

        def emit_ascii(output: BinaryIO) -> None:
            write_grid(output, width=100, height=100, ascii_format=True)

        write(
            "ascii-large.stl",
            emit_ascii,
            _geometry(20000, (100, 100, 0), None),
            Family.ASCII,
            faces=20000,
            profile=Profile.FULL,
        )
    references = external_references()
    if download_external:
        for reference in references:
            materialize_external(root, reference)
            fixtures.append(
                CorpusFixture(
                    reference.filename,
                    FileType.THREE_MF,
                    reference.sha256,
                    reference.input_bytes,
                    None,
                    None,
                    None,
                    ExpectedCompatibility(),
                    Family.SLICER,
                    Profile.DOWNLOAD,
                    reference.url,
                    reference.licensing,
                )
            )
    return CorpusManifest(tuple(fixtures), references, recovery_scenarios())


def verify_manifest(root: Path, manifest: CorpusManifest) -> None:
    for fixture in manifest.fixtures:
        digest = hashlib.sha256()
        size = 0
        with (root / fixture.filename).open("rb") as source:
            while chunk := source.read(FETCH_CHUNK_BYTES):
                size += len(chunk)
                digest.update(chunk)
        if size != fixture.input_bytes or digest.hexdigest() != fixture.sha256:
            raise ValueError(f"fixture content differs: {fixture.filename}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--full", action="store_true", help="Generate up to 2M-face streaming grids"
    )
    parser.add_argument(
        "--download-external",
        action="store_true",
        help="Download and verify pinned real slicer projects",
    )
    args = parser.parse_args(argv)
    manifest = build_contract_corpus(
        args.output_dir, full=args.full, download_external=args.download_external
    )
    verify_manifest(args.output_dir, manifest)
    encoded = json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n"
    (args.output_dir / "manifest.json").write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
