"""Native STL passes preserve source geometry, sampling, and complete preview output."""

import struct
import time

import numpy as np
import printstash_mesh_native as native
import pytest
import reference_stl as worker


@pytest.fixture
def binary_source(tmp_path):
    triangles = (
        np.random.default_rng(481).uniform(-2, 2, (5200, 3, 3)).astype(np.float32)
    )
    records = np.zeros(
        5200,
        dtype=[
            ("normal", "<f4", (3,)),
            ("vertices", "<f4", (3, 3)),
            ("attribute", "<u2"),
        ],
    )
    records["vertices"] = triangles
    path = tmp_path / "source.stl"
    path.write_bytes(bytes(80) + struct.pack("<I", len(triangles)) + records.tobytes())
    return path, triangles


@pytest.fixture
def limits():
    return worker._Limits(
        20_000_000, 1 << 30, 20_000_000, 2048, 10_000_000, 65536, time.monotonic() + 45
    )


class TestNativeStlSource:
    def test_preserves_framing_sample(self, binary_source):
        path, triangles = binary_source
        reference = worker._FramingReservoir()
        reference.add(triangles.mean(axis=1))
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        *_, sample = source.analyze()
        np.testing.assert_array_equal(sample, reference.values)

    def test_preserves_exact_bounds(self, binary_source):
        path, triangles = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 8192, 45)
        count, scanned, lower, upper, _ = source.analyze()
        assert count == len(triangles)
        assert scanned == path.stat().st_size
        np.testing.assert_array_equal(lower, triangles.min(axis=(0, 1)))
        np.testing.assert_array_equal(upper, triangles.max(axis=(0, 1)))

    def test_preserves_preview_across_chunks(self, binary_source, limits, tmp_path):
        path, _ = binary_source
        reservoir = worker._FramingReservoir()
        first = worker._read_pass(
            path, limits, lambda vertices: reservoir.add(vertices.mean(axis=1))
        )
        source = native.NativeStlSource(
            path,
            limits.max_triangles,
            limits.max_source_bytes,
            limits.chunk_triangles,
            45,
        )
        source.analyze()
        old, new = tmp_path / "old.png", tmp_path / "new.png"
        old_count = worker._render(path, old, 16, 16, limits, first, reservoir)
        new_count = worker._render(
            path, new, 16, 16, limits, first, reservoir, native_source=source
        )
        assert new_count == old_count
        assert new.read_bytes() == old.read_bytes()

    def test_rejects_changed_source(self, binary_source):
        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        source.analyze()
        path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="changed"):
            source.analyze()
        with pytest.raises(ValueError, match="failed"):
            source.analyze()

    @pytest.mark.parametrize(
        "max_triangles,max_bytes,chunk,timeout",
        [
            (0, 1024, 1, 1),
            (1, 0, 1, 1),
            (1, 1024, 8193, 1),
            (1, 1024, 1, float("nan")),
            (1, 1024, 1, 46),
        ],
    )
    def test_rejects_invalid_limits(
        self, binary_source, max_triangles, max_bytes, chunk, timeout
    ):
        path, _ = binary_source
        with pytest.raises(ValueError, match="limits"):
            native.NativeStlSource(path, max_triangles, max_bytes, chunk, timeout)

    def test_rejects_excessive_source(self, binary_source):
        path, _ = binary_source
        with pytest.raises(ValueError, match="source budget"):
            native.NativeStlSource(path, 20_000_000, 84, 2048, 45)

    def test_rejects_excessive_triangle_count(self, binary_source):
        path, _ = binary_source
        with pytest.raises(ValueError, match="triangle budget"):
            native.NativeStlSource(path, 1, 1 << 30, 2048, 45)

    def test_rejects_nonfinite_source(self, binary_source):
        path, _ = binary_source
        data = bytearray(path.read_bytes())
        data[96:100] = struct.pack("<f", float("nan"))
        path.write_bytes(data)
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        with pytest.raises(ValueError, match="non-finite"):
            source.analyze()

    def test_discards_exhausted_render(self, binary_source):
        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        source.analyze()
        with pytest.raises(ValueError, match="candidate budget"):
            source.render_depth(
                16,
                16,
                1,
                [0.0, 0.0, 0.0],
                np.eye(3).tolist(),
                [-3.0, -3.0, -3.0],
                [3.0, 3.0, 3.0],
                [0.0, 0.0, 0.0],
                1.0,
            )
        with pytest.raises(ValueError, match="failed"):
            source.analyze()

    def test_rejects_missing_analysis(self, binary_source):
        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        with pytest.raises(ValueError, match="analyzed"):
            source.render_depth(
                16,
                16,
                100,
                [0.0, 0.0, 0.0],
                np.eye(3).tolist(),
                [-3.0, -3.0, -3.0],
                [3.0, 3.0, 3.0],
                [0.0, 0.0, 0.0],
                1.0,
            )


class TestSampleBinaryStl:
    @pytest.mark.parametrize("budget", [1, 2, 99, 5200, 100000])
    def test_preserves_stratified_samples(self, binary_source, budget):
        path, triangles = binary_source
        packed, count, parsed, lower, upper, scanned, complete = (
            native.sample_binary_stl(path, budget)
        )
        samples = min(budget, len(triangles))
        indices = [
            0
            if i == 0
            else len(triangles) - 1
            if i == samples - 1
            else (i * len(triangles) + len(triangles) // 2) // samples
            for i in range(samples)
        ]
        expected = triangles[indices]
        np.testing.assert_array_equal(
            np.frombuffer(packed, np.float32).reshape(-1, 3, 3), expected
        )
        np.testing.assert_array_equal(lower, expected.min(axis=(0, 1)))
        np.testing.assert_array_equal(upper, expected.max(axis=(0, 1)))
        assert count == len(triangles)
        assert parsed == samples
        assert scanned == 84 + 50 * samples
        assert complete == (samples == len(triangles))

    @pytest.mark.parametrize("budget", [0, 100001])
    def test_rejects_excessive_sampling(self, binary_source, budget):
        with pytest.raises(ValueError, match="budget"):
            native.sample_binary_stl(binary_source[0], budget)


class TestNativeSourceFailures:
    def test_deadline_prevents_analysis(self, binary_source):
        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 1e-12)
        with pytest.raises(ValueError, match="deadline"):
            source.analyze()

    def test_replacement_is_rejected_even_with_same_length(self, binary_source):
        import os

        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        source.analyze()
        stat = path.stat()
        replacement = path.with_suffix(".new")
        replacement.write_bytes(path.read_bytes())
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        replacement.replace(path)
        with pytest.raises(ValueError, match="changed"):
            source.analyze()

    def test_empty_culled_view_has_no_depth_result(self, binary_source):
        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        source.analyze()
        with pytest.raises(ValueError, match="no visible"):
            source.render_depth(
                16,
                16,
                100,
                [0.0, 0.0, 0.0],
                np.eye(3).tolist(),
                [10.0, 10.0, 10.0],
                [11.0, 11.0, 11.0],
                [0.0, 0.0, 0.0],
                1.0,
            )

    @pytest.mark.parametrize(
        "width,height,scale",
        [(0, 16, 1.0), (2049, 16, 1.0), (16, 0, 1.0), (16, 16, float("inf"))],
        ids=["zero-width", "excess-width", "zero-height", "nonfinite-scale"],
    )
    def test_invalid_projection_is_rejected(self, binary_source, width, height, scale):
        path, _ = binary_source
        source = native.NativeStlSource(path, 20_000_000, 1 << 30, 2048, 45)
        source.analyze()
        with pytest.raises(ValueError, match="projection"):
            source.render_depth(
                width,
                height,
                100,
                [0.0, 0.0, 0.0],
                np.eye(3).tolist(),
                [-3.0, -3.0, -3.0],
                [3.0, 3.0, 3.0],
                [0.0, 0.0, 0.0],
                scale,
            )

    def test_nonfinite_sample_is_excluded(self, binary_source):
        path, _ = binary_source
        data = bytearray(path.read_bytes())
        data[96:100] = struct.pack("<f", float("inf"))
        path.write_bytes(data)
        packed, _, parsed, *_ = native.sample_binary_stl(path, 2)
        assert parsed == 1
        assert np.isfinite(np.frombuffer(packed, dtype=np.float32)).all()

    def test_ascii_source_declines_binary_sampling(self, tmp_path):
        path = tmp_path / "ascii.stl"
        path.write_text("solid empty\nendsolid empty\n")
        assert native.sample_binary_stl(path, 100) is None

    def test_missing_sample_source_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            native.sample_binary_stl(tmp_path / "missing.stl", 100)
