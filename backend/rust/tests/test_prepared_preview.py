"""Observable image parity and bounded native mesh preparation failures."""

import numpy as np
import printstash_mesh_native as native
import pytest
import reference_native_adapter as native_rasterizer
import reference_rasterizer as rasterizer  # noqa: E402
import trimesh


@pytest.mark.parametrize("kind", ["box", "sphere", "thin", "degenerate"], ids=str)
@pytest.mark.parametrize(
    "chunk", [1, 17, 64000], ids=["one-face", "seventeen", "default"]
)
def test_prepared_mesh_preserves_image(kind, chunk):
    if kind == "box":
        mesh = trimesh.creation.box(extents=[20, 30, 40])
    elif kind == "sphere":
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=20)
    elif kind == "thin":
        mesh = trimesh.creation.box(extents=[20, 30, 0.1])
    else:
        mesh = trimesh.Trimesh(
            vertices=[[0, 0, 0], [1, 0, 0], [2, 0, 0]], faces=[[0, 1, 2]], process=False
        )
    expected = rasterizer.render_mesh_thumbnail(
        mesh,
        kind,
        width=80,
        height=60,
        face_chunk_size=chunk,
        frame_factory=native_rasterizer.NativeFrame,
        normal_preparer=native_rasterizer.prepare_normals,
    )
    actual = rasterizer.render_mesh_thumbnail(
        mesh,
        kind,
        width=80,
        height=60,
        face_chunk_size=chunk,
        mesh_preparer=native_rasterizer.prepare_mesh,
    )
    assert actual is not None
    assert actual == expected


@pytest.mark.parametrize(
    "vertices,faces,chunk",
    [
        (b"", np.zeros((1, 3), dtype=np.uint64).tobytes(), 1),
        (np.zeros((3, 3), dtype=np.float32).tobytes(), b"bad", 1),
        (
            np.full((3, 3), np.nan, dtype=np.float32).tobytes(),
            np.zeros((1, 3), dtype=np.uint64).tobytes(),
            1,
        ),
        (
            np.zeros((3, 3), dtype=np.float32).tobytes(),
            np.full((1, 3), 3, dtype=np.uint64).tobytes(),
            1,
        ),
        (
            np.zeros((3, 3), dtype=np.float32).tobytes(),
            np.zeros((1, 3), dtype=np.uint64).tobytes(),
            0,
        ),
    ],
    ids=["empty", "malformed-faces", "non-finite", "out-of-range-index", "zero-chunk"],
)
def test_prepared_mesh_rejects_invalid_input(vertices, faces, chunk):
    with pytest.raises(ValueError):
        native.PreparedPreview(vertices, faces, chunk)


@pytest.mark.parametrize(
    "matte,output_format",
    [(False, "PNG"), (True, "PNG"), (False, "WEBP")],
    ids=["gloss-png", "matte-png", "webp"],
)
def test_explicit_view_preserves_encoding(matte, output_format):
    mesh = trimesh.creation.icosphere(subdivisions=2)
    options = dict(
        width=80,
        height=60,
        matte=matte,
        output_format=output_format,
        view_rotation=np.eye(3),
    )
    expected = rasterizer.render_mesh_thumbnail(
        mesh,
        "view",
        frame_factory=native_rasterizer.NativeFrame,
        normal_preparer=native_rasterizer.prepare_normals,
        **options,
    )
    actual = rasterizer.render_mesh_thumbnail(
        mesh, "view", mesh_preparer=native_rasterizer.prepare_mesh, **options
    )
    assert actual is not None
    assert actual == expected


@pytest.mark.parametrize(
    "rotation",
    [np.full((3, 3), np.nan), np.zeros((3, 3)), np.ones((2, 2))],
    ids=["nonfinite", "nonorthogonal", "wrong-shape"],
)
def test_invalid_view_does_not_publish_thumbnail(rotation):
    result = rasterizer.render_mesh_thumbnail(
        trimesh.creation.box(),
        "invalid",
        width=80,
        height=60,
        view_rotation=rotation,
        mesh_preparer=native_rasterizer.prepare_mesh,
    )
    assert result is None


def test_owned_preparation_survives_source_mutation():
    mesh = trimesh.creation.box()
    vertices = np.array(mesh.vertices, dtype=np.float32)
    faces = np.array(mesh.faces, dtype=np.uint64)
    prepared = native.PreparedPreview(vertices.tobytes(), faces.tobytes(), 64)
    _, shader = rasterizer._preview_shader(False)
    args = (
        np.eye(3).tolist(),
        1.0,
        80,
        60,
        0.1,
        shader.parameters + (255.0, 255.0, 255.0),
        (100, 100, 100),
    )
    expected = prepared.render(*args)
    vertices[:] = np.nan
    faces[:] = 999
    assert prepared.render(*args) == expected
