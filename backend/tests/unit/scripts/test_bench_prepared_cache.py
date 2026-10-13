"""Disposable array entries preserve geometry while refusing untrusted cache bytes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
import trimesh

from tests.factories.geometry import tetrahedron


@pytest.fixture
def cache_case(tmp_path):
    from scripts.bench_prepared_cache import CacheIdentity

    original = tetrahedron()
    vertices = np.vstack((original.vertices, original.vertices[0]))
    faces = original.faces.copy()
    faces[0, 0] = 4
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    identity = CacheIdentity(
        source_sha256="a" * 64,
        parser_version="mesh-load-v1",
        representation_version="arrays-f64-i64-v1",
        parameters=("file_type=stl", "process=False"),
    )
    return tmp_path / "cache", identity, mesh


class TestWriteCache:
    def test_reconstructs_exact_mesh_arrays(self, cache_case):
        from scripts.bench_prepared_cache import read_cache, write_cache

        directory, identity, mesh = cache_case
        vertices, faces = mesh.vertices.copy(), mesh.faces.copy()
        expected = hashlib.sha256(mesh.export(file_type="stl")).hexdigest()

        written = write_cache(directory, identity, mesh)
        restored = read_cache(directory, identity)

        assert written > mesh.vertices.nbytes + mesh.faces.nbytes
        np.testing.assert_array_equal(restored.vertices, vertices)
        np.testing.assert_array_equal(restored.faces, faces)
        assert len(restored.vertices) == 5
        assert hashlib.sha256(restored.export(file_type="stl")).hexdigest() == expected
        np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(mesh.faces, faces)

    def test_refuses_write_beyond_byte_budget(self, cache_case, monkeypatch):
        from scripts.bench_prepared_cache import InvalidPreparedCache, write_cache

        directory, identity, mesh = cache_case

        def unexpected_write(*_args, **_kwargs):
            pytest.fail("over-budget entry reached array serialization")

        monkeypatch.setattr(np.lib.format, "write_array", unexpected_write)

        with pytest.raises(InvalidPreparedCache):
            write_cache(directory, identity, mesh, max_bytes=1)

        assert not directory.exists() or list(directory.iterdir()) == []

    def test_cleans_owned_staging_after_failed_write(self, cache_case, monkeypatch):
        from scripts.bench_prepared_cache import write_cache

        directory, identity, mesh = cache_case
        save = np.lib.format.write_array
        writes = 0
        failure = OSError("second array write failed")

        def fail_second_array(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise failure
            return save(*args, **kwargs)

        monkeypatch.setattr(np.lib.format, "write_array", fail_second_array)

        with pytest.raises(OSError) as raised:
            write_cache(directory, identity, mesh)

        assert raised.value is failure
        assert writes == 2
        assert not directory.exists()
        assert list(directory.parent.iterdir()) == []

    def test_preserves_an_existing_entry(self, cache_case):
        from scripts.bench_prepared_cache import write_cache

        directory, identity, mesh = cache_case
        write_cache(directory, identity, mesh)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        changed = mesh.copy()
        changed.apply_translation([1, 2, 3])

        with pytest.raises(FileExistsError):
            write_cache(directory, identity, changed)

        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


class TestReadCache:
    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"source_sha256": "b" * 64}, id="source"),
            pytest.param({"parser_version": "mesh-load-v2"}, id="parser"),
            pytest.param({"representation_version": "arrays-v2"}, id="representation"),
            pytest.param(
                {"parameters": ("file_type=obj", "process=False")}, id="parameters"
            ),
        ],
    )
    def test_refuses_mismatched_identity(self, cache_case, changes):
        from scripts.bench_prepared_cache import (
            InvalidPreparedCache,
            read_cache,
            write_cache,
        )

        directory, identity, mesh = cache_case
        write_cache(directory, identity, mesh)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}

        with pytest.raises(InvalidPreparedCache):
            read_cache(directory, replace(identity, **changes))

        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before

    def test_refuses_corrupted_payload(self, cache_case):
        from scripts.bench_prepared_cache import (
            InvalidPreparedCache,
            read_cache,
            write_cache,
        )

        directory, identity, mesh = cache_case
        write_cache(directory, identity, mesh)
        path = directory / "vertices.npy"
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))

        with pytest.raises(InvalidPreparedCache):
            read_cache(directory, identity)

    def test_refuses_oversized_manifest(self, cache_case):
        from scripts.bench_prepared_cache import (
            InvalidPreparedCache,
            read_cache,
            write_cache,
        )

        directory, identity, mesh = cache_case
        write_cache(directory, identity, mesh)
        manifest = directory / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" " * (1024 * 1024 + 1))

        with pytest.raises(InvalidPreparedCache):
            read_cache(directory, identity)

    def test_refuses_read_beyond_byte_budget(self, cache_case, monkeypatch):
        from scripts.bench_prepared_cache import (
            InvalidPreparedCache,
            read_cache,
            write_cache,
        )

        directory, identity, mesh = cache_case
        write_cache(directory, identity, mesh)

        def unexpected_load(*_args, **_kwargs):
            pytest.fail("over-budget entry reached NumPy loading")

        monkeypatch.setattr(np, "load", unexpected_load)

        with pytest.raises(InvalidPreparedCache):
            read_cache(directory, identity, max_bytes=1)

    @pytest.mark.parametrize(
        ("name", "invalid"),
        [
            pytest.param(
                "vertices", np.zeros((5, 2), dtype=np.float64), id="vertex-shape"
            ),
            pytest.param("faces", np.zeros((4, 4), dtype=np.int64), id="face-shape"),
            pytest.param(
                "vertices", np.array([[object()] * 3], dtype=object), id="object-dtype"
            ),
            pytest.param(
                "faces", np.array([[0, 1, 99]], dtype=np.int64), id="dangling-index"
            ),
            pytest.param(
                "vertices", np.array([[np.nan, 0, 0]], dtype=np.float64), id="nonfinite"
            ),
        ],
    )
    def test_refuses_unsafe_arrays(self, cache_case, name, invalid):
        from scripts.bench_prepared_cache import (
            InvalidPreparedCache,
            read_cache,
            write_cache,
        )

        directory, identity, mesh = cache_case
        write_cache(directory, identity, mesh)
        path = directory / f"{name}.npy"
        np.save(path, invalid, allow_pickle=True)
        payload = path.read_bytes()
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["arrays"][name] = {
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "shape": list(invalid.shape),
            "dtype": str(invalid.dtype),
        }
        manifest_path.write_text(json.dumps(manifest))

        with pytest.raises(InvalidPreparedCache):
            read_cache(directory, identity)
