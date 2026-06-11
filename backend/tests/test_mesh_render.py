from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.services import mesh_render


def _tilt_matrix() -> np.ndarray:
    # Flat meshes are viewed face-on but tipped 25° around screen-X
    # (see mesh_render._front_rotation_for_thin_axis).
    tilt = np.radians(25.0)
    ct, st = np.cos(tilt), np.sin(tilt)
    return np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]], dtype=np.float64)


def test_flat_z_mesh_uses_front_view_like_stl_viewer() -> None:
    verts = np.array(
        [
            [-5.0, -5.0, -0.2],
            [5.0, -5.0, -0.2],
            [5.0, 5.0, 0.2],
            [-5.0, 5.0, 0.2],
        ],
        dtype=np.float64,
    )

    rotation = mesh_render._select_view_rotation(verts, np)
    view = verts @ rotation.T

    expected = _tilt_matrix() @ np.diag([1.0, 1.0, -1.0])
    np.testing.assert_allclose(rotation, expected, atol=1e-12)
    # Screen-X is untouched by the tilt: the broad face still spans the view.
    np.testing.assert_allclose(view[:, 0], verts[:, 0])


def test_cube_keeps_isometric_thumbnail_view() -> None:
    verts = np.array(
        [
            [-1.0, -1.0, -1.0],
            [1.0, -1.0, -1.0],
            [1.0, 1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
            [1.0, -1.0, 1.0],
            [1.0, 1.0, 1.0],
            [-1.0, 1.0, 1.0],
        ],
        dtype=np.float64,
    )

    rotation = mesh_render._select_view_rotation(verts, np)

    assert not np.allclose(rotation, np.diag([1.0, 1.0, -1.0]))
    np.testing.assert_allclose(
        rotation,
        np.array(
            [
                [0.70710678, 0.70710678, 0.0],
                [-0.61237244, 0.61237244, -0.5],
                [-0.35355339, 0.35355339, 0.8660254],
            ]
        ),
        rtol=1e-6,
        atol=1e-6,
    )


def test_flat_x_mesh_uses_broad_face_view() -> None:
    verts = np.array(
        [
            [-0.1, -2.0, -3.0],
            [0.1, -2.0, -3.0],
            [0.1, 2.0, 3.0],
            [-0.1, 2.0, 3.0],
        ],
        dtype=np.float64,
    )

    rotation = mesh_render._select_view_rotation(verts, np)

    expected = _tilt_matrix() @ np.array(
        [
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [-1.0, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    np.testing.assert_allclose(rotation, expected, atol=1e-12)


def test_front_facing_flat_mesh_does_not_fall_back_to_silhouette(monkeypatch) -> None:
    mesh = SimpleNamespace(
        vertices=np.array(
            [
                [-1.0, -1.0, 0.0],
                [1.0, -1.0, 0.0],
                [1.0, 1.0, 0.0],
                [-1.0, 1.0, 0.0],
            ],
            dtype=np.float64,
        ),
        faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64),
    )
    warnings: list[str] = []

    def capture_warning(message: str, *args, **kwargs) -> None:
        warnings.append(message % args if args else message)

    monkeypatch.setattr(mesh_render.logger, "warning", capture_warning)

    png = mesh_render.render_thumbnail(
        lambda _path: mesh, Path("front-facing.stl"), width=80, height=80
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert not any("no visible triangles" in message for message in warnings)
