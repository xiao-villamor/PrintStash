"""Native kernel output matches independent reference stages."""

from __future__ import annotations

import tracemalloc

import numpy as np
import pytest
import reference_native_adapter as native_rasterizer
import reference_rasterizer as rasterizer


class TestNativeFrame:
    def test_constant_silhouette_uses_flat_color(self, native):
        frame = native_rasterizer.NativeFrame(4, 4)
        tri = np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], dtype=np.float32)
        frame.draw(
            tri,
            np.zeros_like(tri),
            lambda n: np.full_like(n, 0.5),
            np.array([100, 200, 240]),
        )
        pixels = np.frombuffer(frame.rgba(), np.uint8).reshape(4, 4, 4)
        np.testing.assert_array_equal(pixels[0, 0], [50, 100, 120, 255])

    @pytest.mark.parametrize(
        "shape", ["box", "icosphere"], ids=["mechanical", "curved"]
    )
    @pytest.mark.parametrize("chunk", [5, 64000], ids=["small-batches", "single-batch"])
    @pytest.mark.parametrize(
        "prepare", [False, True], ids=["numpy-normals", "rust-normals"]
    )
    def test_preserves_complete_preview(self, native, shape, chunk, prepare):
        import trimesh

        mesh = getattr(trimesh.creation, shape)()
        expected = rasterizer.render_mesh_thumbnail(
            mesh,
            shape,
            width=128,
            height=128,
            face_chunk_size=chunk,
            rasterise_triangles=native_rasterizer.rasterise_triangles,
        )
        actual = rasterizer.render_mesh_thumbnail(
            mesh,
            shape,
            width=128,
            height=128,
            face_chunk_size=chunk,
            frame_factory=native_rasterizer.NativeFrame,
            normal_preparer=native_rasterizer.prepare_normals if prepare else None,
        )
        assert expected is not None
        assert actual == expected


@pytest.fixture
def native():
    __import__("printstash_mesh_native")
    return native_rasterizer.rasterise_triangles


def paint(renderer, tri, normals, width=64, height=64, depth=None):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    zbuf = np.full((height, width), np.inf) if depth is None else depth.copy()
    count = renderer(
        img,
        zbuf,
        tri,
        normals,
        lambda n: (n + 1) / 2,
        np.array([255, 190, 100]),
        width,
        height,
    )
    return img, zbuf, count


class TestRasteriseTriangles:
    def test_does_not_shade_an_empty_image(self, native):
        def unexpected(normals):
            raise AssertionError("empty image reached shading")

        image = np.zeros((16, 16, 3), dtype=np.uint8)
        zbuf = np.full((16, 16), np.inf)
        count = native(
            image,
            zbuf,
            np.zeros((0, 3, 3)),
            np.zeros((0, 3, 3)),
            unexpected,
            np.ones(3),
            16,
            16,
        )
        assert count == 0
        assert not image.any()
        assert np.isinf(zbuf).all()

    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    @pytest.mark.parametrize("seed", range(10))
    def test_matches_python_visible_pixels(self, native, dtype, seed):
        rng = np.random.default_rng(seed)
        tri = rng.uniform(-32, 96, (80, 3, 3)).astype(dtype)
        normals = rng.uniform(-1, 1, tri.shape).astype(dtype)
        expected, expected_z, _ = paint(rasterizer._rasterise_triangles, tri, normals)
        actual, actual_z, _ = paint(native, tri, normals)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual_z, expected_z)

    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    def test_preserves_rounded_skinny_edges(self, native, dtype):
        top = np.nextafter(dtype(17.5), dtype(-np.inf))
        tri = np.array([[[0, top, 0], [1, top, 0], [511, -482.5, 0]]], dtype=dtype)
        normals = np.ones_like(tri)
        expected, expected_z, _ = paint(
            rasterizer._rasterise_triangles, tri, normals, 512, 32
        )
        actual, actual_z, count = paint(native, tri, normals, 512, 32)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual_z, expected_z)
        assert count < 4096

    def test_preserves_existing_equal_depth(self, native):
        tri = np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], dtype=np.float32)
        img, zbuf, _ = paint(native, tri, np.ones_like(tri), depth=np.ones((64, 64)))
        assert not img.any()
        np.testing.assert_array_equal(zbuf, 1)

    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    def test_matches_python_across_shading_batches(self, native, dtype):
        tri = np.array([[[-10, -10, 1], [600, -10, 2], [-10, 600, 3]]], dtype=dtype)
        normals = np.array([[[0, 0, 1], [1, 0, 0], [0, 1, 0]]], dtype=dtype)
        expected, expected_z, _ = paint(
            rasterizer._rasterise_triangles, tri, normals, 257, 257
        )

        actual, actual_z, _ = paint(native, tri, normals, 257, 257)

        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual_z, expected_z)

    def test_bounds_python_memory_for_a_filled_preview(self, native):
        tri = np.array(
            [[[-10, -10, 1], [2560, -10, 1], [-10, 1920, 1]]], dtype=np.float32
        )
        normals = np.array([[[0, 0, 1], [1, 0, 0], [0, 1, 0]]], dtype=np.float32)

        tracemalloc.start()
        try:
            paint(native, tri, normals, 1280, 960)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # The first adapter used over 300 MiB here. This ceiling includes its
        # NumPy temporaries and Python buffers, but not Rust allocator memory.
        assert peak < 128 * 1024**2, f"Python allocation peak: {peak / 1024**2:.1f} MiB"

    @pytest.mark.parametrize("buffer", ["image", "depth"])
    def test_later_shading_failure_preserves_framebuffer(self, native, buffer):
        image = np.zeros((257, 257, 3), dtype=np.uint8)
        zbuf = np.full((257, 257), np.inf)
        tri = np.array(
            [[[-10, -10, 1], [600, -10, 1], [-10, 600, 1]]], dtype=np.float32
        )
        calls = 0

        def broken_later(normals):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("later shading failed")
            return np.ones_like(normals)

        with pytest.raises(ValueError, match="later shading failed"):
            native(
                image, zbuf, tri, np.ones_like(tri), broken_later, np.ones(3), 257, 257
            )

        actual = {"image": image, "depth": zbuf}[buffer]
        np.testing.assert_array_equal(actual, {"image": 0, "depth": np.inf}[buffer])

    def test_accepts_noncontiguous_framebuffers(self, native):
        image = np.zeros((32, 32, 3), dtype=np.uint8)
        zbuf = np.full((32, 32), np.inf)
        tri = np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], dtype=np.float32)
        native(
            image[::2, ::2],
            zbuf[::2, ::2],
            tri,
            np.ones_like(tri),
            lambda n: np.ones_like(n),
            np.array([255, 255, 255]),
            16,
            16,
        )
        assert image[0, 0].tolist() == [255, 255, 255]
        assert zbuf[0, 0] == 1
        assert not image[1::2].any()
        assert np.isinf(zbuf[1::2]).all()

    def test_rejects_invalid_array_shapes(self, native):
        with pytest.raises(ValueError, match="invalid native rasterizer arrays"):
            paint(native, np.zeros((1, 9)), np.zeros((1, 9)))

    def test_shading_failure_preserves_framebuffers(self, native):
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        zbuf = np.full((16, 16), np.inf)
        tri = np.array([[[0, 0, 1], [4, 0, 1], [0, 4, 1]]], dtype=np.float32)

        def broken(normals):
            raise ValueError("shading failed")

        with pytest.raises(ValueError, match="shading failed"):
            native(image, zbuf, tri, np.ones_like(tri), broken, np.ones(3), 16, 16)
        assert not image.any()
        assert np.isinf(zbuf).all()


@pytest.fixture(params=[False, True], ids=["glossy", "matte"])
def phong_shader(request):
    from .test_rasterizer import box_mesh

    callbacks = []

    def capture(img, zbuf, tri, normals, shade, base_color, width, height):
        callbacks.append(shade)
        return 0

    rasterizer.render_mesh_thumbnail(
        box_mesh(),
        "box",
        width=32,
        height=32,
        matte=request.param,
        rasterise_triangles=capture,
    )
    assert callbacks
    assert isinstance(callbacks[0], rasterizer.PhongShader)
    return callbacks[0]


class TestNativePhong:
    @pytest.mark.parametrize("dtype", [np.float32, np.float64])
    @pytest.mark.parametrize("seed", range(10))
    def test_matches_interpolated_lighting(self, native, phong_shader, dtype, seed):
        rng = np.random.default_rng(seed)
        triangles = rng.uniform(-32, 96, (50, 3, 3)).astype(dtype)
        normals = rng.uniform(-1, 1, triangles.shape).astype(dtype)
        actual = np.zeros((64, 64, 3), dtype=np.uint8)
        actual_depth = np.full((64, 64), np.inf)
        expected, expected_depth = actual.copy(), actual_depth.copy()
        base = np.array([255.0, 190.0, 100.0])
        native(
            expected,
            expected_depth,
            triangles,
            normals,
            phong_shader.reference,
            base,
            64,
            64,
        )
        native(actual, actual_depth, triangles, normals, phong_shader, base, 64, 64)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual_depth, expected_depth)

    @pytest.mark.parametrize("thin_axis", [None, 0, 1, 2])
    @pytest.mark.parametrize("matte", [False, True])
    def test_preserves_canonical_preview_pixels(self, native, thin_axis, matte):
        from .test_rasterizer import box_mesh

        mesh = box_mesh()
        if thin_axis is not None:
            mesh.vertices[:, thin_axis] *= 0.01
        expected = rasterizer.render_mesh_thumbnail(
            mesh,
            "box",
            width=96,
            height=72,
            matte=matte,
            rasterise_triangles=rasterizer._rasterise_triangles,
        )
        actual = rasterizer.render_mesh_thumbnail(
            mesh,
            "box",
            width=96,
            height=72,
            matte=matte,
            face_chunk_size=5,
            rasterise_triangles=native,
        )
        assert actual is not None
        assert actual == expected

    def test_preserves_strided_framebuffers(self, native, phong_shader):
        triangle = np.array([[[0, 0, 1], [16, 0, 1], [0, 16, 1]]], dtype=np.float64)
        normals = np.ones_like(triangle)
        actual = np.zeros((32, 32, 3), dtype=np.uint8)
        depth = np.full((32, 32), np.inf)
        expected = np.zeros((16, 16, 3), dtype=np.uint8)
        expected_depth = np.full((16, 16), np.inf)
        base = np.array([255.0, 255.0, 255.0])
        native(
            expected,
            expected_depth,
            triangle,
            normals,
            phong_shader.reference,
            base,
            16,
            16,
        )
        native(
            actual[::2, ::2],
            depth[::2, ::2],
            triangle,
            normals,
            phong_shader,
            base,
            16,
            16,
        )
        np.testing.assert_array_equal(actual[::2, ::2], expected)
        np.testing.assert_array_equal(depth[::2, ::2], expected_depth)
        assert not actual[1::2].any()
        assert np.isinf(depth[1::2]).all()

    def test_preserves_framebuffers_on_invalid_lighting(self, native, phong_shader):
        from dataclasses import replace

        invalid = replace(
            phong_shader, parameters=(float("nan"), *phong_shader.parameters[1:])
        )
        triangle = np.array([[[0, 0, 1], [16, 0, 1], [0, 16, 1]]], dtype=np.float64)
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        depth = np.full((16, 16), np.inf)
        with pytest.raises(ValueError, match="lighting"):
            native(
                image,
                depth,
                triangle,
                np.ones_like(triangle),
                invalid,
                np.ones(3) * 255,
                16,
                16,
            )
        assert not image.any()
        assert np.isinf(depth).all()

    def test_bounds_shading_memory(self, native, phong_shader):
        triangle = np.array(
            [[[-10.0, -10.0, 1.0], [2560.0, -10.0, 1.0], [-10.0, 1920.0, 1.0]]]
        )
        normals = np.ones_like(triangle)
        image = np.zeros((960, 1280, 3), dtype=np.uint8)
        depth = np.full((960, 1280), np.inf)
        tracemalloc.start()
        try:
            native(
                image,
                depth,
                triangle,
                normals,
                phong_shader,
                np.ones(3) * 255,
                1280,
                960,
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        # Includes visibility/colour buffers and indexed-write temporaries;
        # Rust allocator and already allocated framebuffers are excluded.
        assert peak < 32 * 1024**2

    def test_supports_other_normal_dtypes(self, native, phong_shader):
        triangle = np.array([[[0.0, 0.0, 1.0], [16.0, 0.0, 1.0], [0.0, 16.0, 1.0]]])
        normals = np.ones_like(triangle, dtype=np.float16)
        image = np.zeros((16, 16, 3), dtype=np.uint8)
        depth = np.full((16, 16), np.inf)
        expected, expected_depth = image.copy(), depth.copy()
        base = np.ones(3) * 255
        native(
            expected,
            expected_depth,
            triangle,
            normals,
            phong_shader.reference,
            base,
            16,
            16,
        )
        native(image, depth, triangle, normals, phong_shader, base, 16, 16)
        np.testing.assert_array_equal(image, expected)
        np.testing.assert_array_equal(depth, expected_depth)
