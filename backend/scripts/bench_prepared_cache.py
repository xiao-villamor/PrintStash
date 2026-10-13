"""Disposable prepared-array cache experiment, never imported by application paths.

Compare current loading plus STL export with verified array restoration plus the
same export. This is neither observed user reuse nor SQL publication latency.
Run --output-dir /tmp/prepared-pilot --repeat 10 --cold-runs 3. No cache adoption,
GC, shared-writer, or reboot recovery policy is established by this private pilot.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import random
import resource
import shutil
import stat
import sys
import tempfile
import time
from contextlib import chdir, redirect_stdout
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Callable, cast

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from trimesh import Trimesh

MAX_CACHE_BYTES = 512 * 1024**2
MAX_MANIFEST_BYTES = 16384
_ARRAYS = {"vertices": np.dtype("float64"), "faces": np.dtype("int64")}


class InvalidPreparedCache(ValueError):
    """The experimental entry cannot represent the requested source."""


@dataclass(frozen=True)
class CacheIdentity:
    source_sha256: str
    parser_version: str
    representation_version: str
    parameters: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_sha256, str)
            or len(self.source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.source_sha256)
        ):
            raise InvalidPreparedCache("source checksum")
        if any(
            not isinstance(v, str) or not v
            for v in (self.parser_version, self.representation_version)
        ):
            raise InvalidPreparedCache("identity version")
        if not isinstance(self.parameters, tuple) or any(
            not isinstance(v, str) for v in self.parameters
        ):
            raise InvalidPreparedCache("identity parameters")


def _limit(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise InvalidPreparedCache("cache byte limit")


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            result.update(block)
    return result.hexdigest()


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise InvalidPreparedCache("manifest object")
    return cast(dict[str, object], value)


def _array_check(vertices: NDArray[np.float64], faces: NDArray[np.int64]) -> None:
    if (
        vertices.ndim != 2
        or vertices.shape[1] != 3
        or faces.ndim != 2
        or faces.shape[1] != 3
    ):
        raise InvalidPreparedCache("cache array shape")
    if not len(vertices) or not len(faces):
        raise InvalidPreparedCache("empty geometry")
    for start in range(0, len(vertices), 64000):
        if not np.isfinite(vertices[start : start + 64000]).all():
            raise InvalidPreparedCache("nonfinite vertices")
    for start in range(0, len(faces), 64000):
        part = faces[start : start + 64000]
        if np.any(part < 0) or np.any(part >= len(vertices)):
            raise InvalidPreparedCache("face index range")


def prepare_source(path: Path, file_type: str | None = None) -> Trimesh:
    """Use the canonical loader, retaining raw STL facets without indexing."""
    from trimesh import Trimesh

    from app.modules.media.mesh_loading import load_mesh

    mesh = load_mesh(path, file_type=file_type)
    if not isinstance(mesh, Trimesh):
        raise InvalidPreparedCache("source did not produce a mesh")
    return mesh


def _owned_remove(path: Path, identity: tuple[int, int]) -> None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(value.st_mode) and (value.st_dev, value.st_ino) == identity:
        shutil.rmtree(path)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_cache(
    directory: Path,
    identity: CacheIdentity,
    mesh: Trimesh,
    *,
    max_bytes: int = MAX_CACHE_BYTES,
) -> int:
    """Validate and bound complete entry bytes before allocating or writing arrays."""
    _limit(max_bytes)
    if not isinstance(identity, CacheIdentity):
        raise InvalidPreparedCache("cache identity")
    if directory.exists() or directory.is_symlink():
        raise FileExistsError(directory)
    arrays = {"vertices": mesh.vertices, "faces": mesh.faces}
    sizes: dict[str, int] = {}
    for name, value in arrays.items():
        if value.ndim != 2 or value.shape[1] != 3:
            raise InvalidPreparedCache("cache array shape")
        if value.dtype != _ARRAYS[name]:
            raise InvalidPreparedCache("cache array dtype")
        # NumPy public header writer determines exact bytes without copying data.
        from io import BytesIO

        header = BytesIO()
        np.lib.format.write_array_header_1_0(
            header,
            {"descr": value.dtype.str, "fortran_order": False, "shape": value.shape},
        )
        sizes[name] = header.tell() + value.size * value.dtype.itemsize
    manifest: dict[str, object] = {
        "identity": asdict(identity),
        "arrays": {
            name: {
                "size": sizes[name],
                "sha256": "0" * 64,
                "shape": list(value.shape),
                "dtype": str(value.dtype),
            }
            for name, value in arrays.items()
        },
    }
    estimated_manifest = json.dumps(manifest, sort_keys=True).encode()
    if (
        len(estimated_manifest) > MAX_MANIFEST_BYTES
        or sum(sizes.values()) + len(estimated_manifest) > max_bytes
    ):
        raise InvalidPreparedCache("cache byte limit")
    _array_check(mesh.vertices, mesh.faces)
    temporary = Path(tempfile.mkdtemp(prefix=".prepared-", dir=directory.parent))
    value = temporary.stat()
    owner = (value.st_dev, value.st_ino)
    published = False
    try:
        info = _object(manifest["arrays"])
        for name, array in arrays.items():
            path = temporary / (name + ".npy")
            with path.open("xb") as stream:
                np.lib.format.write_array(
                    stream,
                    np.asarray(array, order="C"),
                    version=(1, 0),
                    allow_pickle=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            if path.stat().st_size != sizes[name]:
                raise InvalidPreparedCache("cache size changed")
            _object(info[name])["sha256"] = _digest(path)
        payload = json.dumps(manifest, sort_keys=True).encode()
        with (temporary / "manifest.json").open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(temporary)
        if directory.exists() or directory.is_symlink():
            raise FileExistsError(directory)
        temporary.rename(directory)
        published = True
        _sync_directory(directory.parent)
        return sum(sizes.values()) + len(payload)
    except BaseException:
        _owned_remove(directory if published else temporary, owner)
        raise


def read_cache(
    directory: Path, identity: CacheIdentity, *, max_bytes: int = MAX_CACHE_BYTES
) -> Trimesh:
    """Bound headers and complete bytes before copying verified, valid f64/i64 arrays."""
    from trimesh import Trimesh

    _limit(max_bytes)
    if not isinstance(identity, CacheIdentity):
        raise InvalidPreparedCache("cache identity")
    try:
        manifest_path = directory / "manifest.json"
        manifest_size = manifest_path.stat().st_size
        if not 0 < manifest_size <= min(MAX_MANIFEST_BYTES, max_bytes):
            raise InvalidPreparedCache("manifest byte limit")
        manifest = _object(json.loads(manifest_path.read_bytes()))
        if set(manifest) != {"identity", "arrays"} or manifest[
            "identity"
        ] != json.loads(json.dumps(asdict(identity))):
            raise InvalidPreparedCache("cache identity")
        information = _object(manifest["arrays"])
        if set(information) != set(_ARRAYS):
            raise InvalidPreparedCache("cache array keys")
        total = manifest_size
        mapped: dict[str, NDArray[np.generic]] = {}
        for name, dtype in _ARRAYS.items():
            info = _object(information[name])
            if (
                set(info) != {"size", "sha256", "shape", "dtype"}
                or type(info["size"]) is not int
            ):
                raise InvalidPreparedCache("cache array schema")
            path = directory / (name + ".npy")
            size = path.stat().st_size
            total += size
            if size != info["size"] or total > max_bytes:
                raise InvalidPreparedCache("cache byte limit or size")
            with path.open("rb") as stream:
                if np.lib.format.read_magic(stream) != (1, 0):
                    raise InvalidPreparedCache("cache NPY version")
                shape, fortran, actual_dtype = np.lib.format.read_array_header_1_0(
                    stream, max_header_size=4096
                )
                if (
                    len(shape) != 2
                    or shape[1] != 3
                    or shape[0] <= 0
                    or fortran
                    or actual_dtype != dtype
                    or info["dtype"] != str(dtype)
                    or info["shape"] != list(shape)
                    or stream.tell() + math.prod(shape) * dtype.itemsize != size
                ):
                    raise InvalidPreparedCache("cache array schema")
            if _digest(path) != info["sha256"]:
                raise InvalidPreparedCache("cache checksum")
            mapped[name] = np.load(path, mmap_mode="r", allow_pickle=False)
        vertices = cast(NDArray[np.float64], mapped["vertices"])
        faces = cast(NDArray[np.int64], mapped["faces"])
        _array_check(vertices, faces)
        return Trimesh(
            vertices=np.array(vertices, copy=True),
            faces=np.array(faces, copy=True),
            process=False,
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        if isinstance(exc, InvalidPreparedCache):
            raise
        raise InvalidPreparedCache("invalid cache entry") from exc


def _export(mesh: Trimesh) -> dict[str, object]:
    encoded = mesh.export(file_type="stl")
    if not isinstance(encoded, bytes):
        raise InvalidPreparedCache("unexpected STL export type")
    return {"size": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _source_export(path: Path) -> dict[str, object]:
    return _export(prepare_source(path))


def _cache_export(path: Path, identity: CacheIdentity) -> dict[str, object]:
    return _export(read_cache(path, identity))


def _sample(call: Callable[[], dict[str, object]]) -> dict[str, object]:
    started = time.perf_counter_ns()
    try:
        result = call()
        return {
            "outcome": "completed",
            **result,
            "elapsed_ms": (time.perf_counter_ns() - started) / 1e6,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        }
    except Exception as exc:
        # A pilot must preserve failed attempts rather than deleting observations.
        return {
            "outcome": "failed",
            "reason": type(exc).__name__ + ": " + str(exc),
            "elapsed_ms": (time.perf_counter_ns() - started) / 1e6,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        }


def _write_trial(
    root: Path, identity: CacheIdentity, mesh: Trimesh
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="write-", dir=root) as temporary:
        size = write_cache(Path(temporary) / "entry", identity, mesh)
    return {"cache_bytes": size}


def _identity(value: object) -> CacheIdentity:
    fields = _object(value)
    if set(fields) != {
        "source_sha256",
        "parser_version",
        "representation_version",
        "parameters",
    }:
        raise InvalidPreparedCache("cache identity fields")
    parameters = fields["parameters"]
    if not isinstance(parameters, list) or any(
        not isinstance(v, str) for v in parameters
    ):
        raise InvalidPreparedCache("cache identity parameters")
    for name in ("source_sha256", "parser_version", "representation_version"):
        if not isinstance(fields[name], str):
            raise InvalidPreparedCache("cache identity types")
    return CacheIdentity(
        cast(str, fields["source_sha256"]),
        cast(str, fields["parser_version"]),
        cast(str, fields["representation_version"]),
        tuple(cast(list[str], parameters)),
    )


def _worker(argv: list[str]) -> int:
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    from app.modules.media.mesh_isolation import read_spec

    spec = read_spec(argv)
    if spec["mode"] not in {"source", "cache"}:
        raise InvalidPreparedCache("worker mode")

    def operation() -> dict[str, object]:
        path = Path(spec["source"])
        mesh = (
            prepare_source(path, spec["file_type"])
            if spec["mode"] == "source"
            else read_cache(Path(spec["cache"]), _identity(spec["identity"]))
        )
        return _export(mesh)

    payload = json.dumps(_sample(operation), allow_nan=False).encode()
    if len(payload) > 4096:
        raise InvalidPreparedCache("worker reply byte limit")
    output.write(payload)
    output.close()
    return 0


def _cold(
    path: Path, cache: Path, identity: CacheIdentity, mode: str
) -> dict[str, object]:
    from app.modules.media.mesh_isolation import MeshWorkerError, run_worker
    from app.modules.media.native_budget import GeometryWork, MeshSource

    started = time.perf_counter_ns()
    try:
        reply = run_worker(
            "scripts.bench_prepared_cache",
            {
                "source": str(path),
                "file_type": path.suffix.lstrip("."),
                "cache": str(cache),
                "identity": asdict(identity),
                "mode": mode,
                "pilot_worker": True,
            },
            sources=(MeshSource(path, path.suffix.lstrip(".")),),
            work=GeometryWork(),
        )
        if len(reply) > 4096:
            raise InvalidPreparedCache("worker reply byte limit")
        result = _object(json.loads(reply))
    except MeshWorkerError as exc:
        result = {
            "outcome": "failed",
            "reason": exc.reason.value,
            "supervision": asdict(exc.supervision)
            if exc.supervision is not None
            else None,
        }
    except Exception as exc:
        result = {"outcome": "failed", "reason": type(exc).__name__ + ": " + str(exc)}
    return {**result, "total_ms": (time.perf_counter_ns() - started) / 1e6}


def run(
    output: Path, repeat: int, cold_runs: int, cases: tuple[str, ...] = ()
) -> dict[str, object]:
    """Write raw current-stack observations, not an adoption or latency gate."""
    import trimesh

    from app.modules.derivatives.kinds import (
        MESH_GEOMETRY_RECIPE,
        MESH_THUMBNAIL_RECIPE,
        VIEWER_STL_RECIPE,
    )
    from app.modules.media.fingerprints import ALGORITHM_VERSION
    from scripts.benchmark_environment import collect_environment
    from scripts.mesh_benchmark_corpus import build_contract_corpus

    if (
        type(repeat) is not int
        or repeat <= 0
        or type(cold_runs) is not int
        or cold_runs < 0
    ):
        raise ValueError("sample counts")
    output.mkdir()
    manifest = build_contract_corpus(output / "sources")
    selected = {
        "cube-binary.stl",
        "cube-mm.3mf",
        "multiple-build.3mf",
        "reflected-build.3mf",
    }
    entries: dict[str, dict[str, object]] = {
        fixture.filename: asdict(fixture)
        for fixture in manifest.fixtures
        if fixture.filename in selected
    }
    for subdivisions in (4, 6):
        name = f"sphere_{20 * 4**subdivisions}.stl"
        if cases and name not in cases:
            continue
        mesh = trimesh.creation.icosphere(subdivisions=subdivisions)
        path = output / "sources" / f"sphere_{len(mesh.faces)}.stl"
        mesh.export(path, file_type="stl")
        entries[path.name] = {
            "filename": path.name,
            "sha256": _digest(path),
            "input_bytes": path.stat().st_size,
            "source_faces": len(mesh.faces),
            "origin": "generated:trimesh.creation.icosphere",
            "license": "AGPL-3.0",
            "file_type": "stl",
        }
    if cases:
        if any(name not in entries for name in cases):
            raise ValueError("unknown pilot case")
        entries = {name: entries[name] for name in cases}
    observations: dict[str, object] = {}
    report: dict[str, object] = {
        "schema_version": 1,
        "scope": "private_pilot_no_production_adoption",
        "repeat": repeat,
        "cold_runs": cold_runs,
        "environment": asdict(collect_environment()),
        "observed_user_reuse": False,
        "production_baseline": "same-attempt loading once; no cache hit distribution measured",
        "rss_scope": "warm interpreter high-water mark; cold child process high-water mark",
        "recipes": {
            "metadata": MESH_GEOMETRY_RECIPE,
            "thumbnail": MESH_THUMBNAIL_RECIPE,
            "viewer": VIEWER_STL_RECIPE,
            "fingerprint": ALGORITHM_VERSION,
        },
        "cost_scope": "warm load/verified restore plus identical STL export; cold includes native admission/bootstrap; excludes SQL publication",
        "cache_temperature": "uncontrolled_filesystem_warm_interpreter_fresh_cold_workers",
        "representation_scope": "flat float64 vertices/int64 faces only; no retained-scene or prepared-render cache",
        "cases": observations,
    }
    environment = _object(report["environment"])
    commit = environment["commit"]
    if not isinstance(commit, str):
        raise ValueError("benchmark commit unavailable")
    versions = _object(environment["versions"])
    parser_version = f"current-public-load-mesh/{commit}/trimesh-{versions['trimesh']}/numpy-{versions['numpy']}"
    randomizer = random.Random(21)
    for name, source in entries.items():
        path = output / "sources" / name
        identity = CacheIdentity(
            _digest(path),
            parser_version,
            "raw-f64-i64-pilot-v1",
            (path.suffix.lstrip("."), "process_false"),
        )
        mesh = prepare_source(path)
        reference = _export(mesh)
        cache = output / (name + ".cache")
        write_cache(cache, identity, mesh)
        restored = read_cache(cache, identity)
        if (
            not np.array_equal(mesh.vertices, restored.vertices)
            or not np.array_equal(mesh.faces, restored.faces)
            or _export(restored) != reference
        ):
            raise InvalidPreparedCache("reference parity")
        warm: dict[str, list[dict[str, object]]] = {
            "source": [],
            "cache": [],
            "creation": [],
        }
        operations: dict[str, Callable[[], dict[str, object]]] = {
            "source": partial(_source_export, path),
            "cache": partial(_cache_export, cache, identity),
            "creation": partial(_write_trial, output, identity, mesh),
        }
        for _ in range(repeat):
            order = list(operations)
            randomizer.shuffle(order)
            for mode in order:
                sample = _sample(operations[mode])
                if (
                    mode != "creation"
                    and sample["outcome"] == "completed"
                    and (sample["size"], sample["sha256"])
                    != (reference["size"], reference["sha256"])
                ):
                    sample = {
                        **sample,
                        "outcome": "failed",
                        "reason": "STL export parity",
                    }
                warm[mode].append(sample)
                gc.collect()  # Native topology cycles are reclaimed outside timing.
        cold: dict[str, list[dict[str, object]]] = {"source": [], "cache": []}
        for _ in range(cold_runs):
            order = list(cold)
            randomizer.shuffle(order)
            for mode in order:
                sample = _cold(path, cache, identity, mode)
                if sample["outcome"] == "completed" and (
                    sample["size"],
                    sample["sha256"],
                ) != (reference["size"], reference["sha256"]):
                    sample = {
                        **sample,
                        "outcome": "failed",
                        "reason": "STL export parity",
                    }
                cold[mode].append(sample)
        observations[name] = {
            "source": source,
            "reference": reference,
            "cache_bytes": sum(p.stat().st_size for p in cache.iterdir()),
            "warm": warm,
            "cold": cold,
        }
        (output / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False)
        )
    return report


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1].startswith("{"):
        return _worker(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--cold-runs", type=int, default=3)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    if args.repeat < 1 or args.cold_runs < 0:
        parser.error("repeat must be positive and cold-runs nonnegative")
    output_directory = args.output_dir.resolve()
    from scripts.bench_mesh_pipeline import (
        configure_private_vault,
        export_private_settings,
    )

    with tempfile.TemporaryDirectory(prefix="prepared-pilot-vault-") as temporary:
        configure_private_vault(Path(temporary))
        with chdir(temporary), redirect_stdout(sys.stderr):
            from app.core.config import settings

            export_private_settings(settings)
            report = run(
                output_directory, args.repeat, args.cold_runs, tuple(args.case)
            )
    print(json.dumps(report, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
