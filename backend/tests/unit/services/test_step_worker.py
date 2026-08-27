"""The disposable STEP worker publishes only bounded tessellated mesh output.

The parent process trusts its exit codes to distinguish invalid geometry from a
triangle-budget rejection, so partial output must never look successful.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import step_worker


def _triangle_mesh():
    import trimesh

    return trimesh.Trimesh(
        vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        faces=[[0, 1, 2]],
        process=False,
    )


def _triangle_count(path: Path) -> int:
    import trimesh

    loaded = trimesh.load_mesh(path, process=False)
    if isinstance(loaded, trimesh.Trimesh):
        return len(loaded.faces)
    return sum(len(geometry.faces) for geometry in loaded.geometry.values())


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    loaded: object,
    *,
    triangle_limit: int = 100,
) -> tuple[int, Path]:
    import trimesh

    source = tmp_path / "source.step"
    destination = tmp_path / "mesh.glb"
    source.write_text("ISO-10303-21;", encoding="ascii")
    monkeypatch.setenv("PRINTSTASH_STEP_TRIANGLE_LIMIT", str(triangle_limit))
    monkeypatch.setattr(
        step_worker.sys,
        "argv",
        ["step_worker", str(source), str(destination)],
    )
    monkeypatch.setattr(trimesh, "load_mesh", lambda *_args, **_kwargs: loaded)
    return step_worker.main(), destination


class TestMain:
    def test_exports_a_tessellated_mesh_as_glb(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status, destination = _invoke(monkeypatch, tmp_path, _triangle_mesh())

        assert status == 0
        assert destination.read_bytes()[:4] == b"glTF"

    def test_concatenates_mesh_geometry_from_a_scene(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trimesh

        first = _triangle_mesh()
        second = _triangle_mesh().copy()
        second.apply_translation([2.0, 0.0, 0.0])
        scene = trimesh.Scene([first, second])

        status, destination = _invoke(monkeypatch, tmp_path, scene)

        assert status == 0
        assert _triangle_count(destination) == 2

    def test_rejects_invocation_without_source_and_destination(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(step_worker.sys, "argv", ["step_worker"])

        status = step_worker.main()

        assert status == 2

    def test_rejects_a_scene_without_mesh_geometry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trimesh

        status, destination = _invoke(monkeypatch, tmp_path, trimesh.Scene())

        assert status == 4
        assert not destination.exists()

    def test_rejects_non_mesh_loader_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status, destination = _invoke(monkeypatch, tmp_path, object())

        assert status == 4
        assert not destination.exists()

    def test_rejects_geometry_above_the_triangle_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status, destination = _invoke(
            monkeypatch,
            tmp_path,
            _triangle_mesh(),
            triangle_limit=0,
        )

        assert status == 3
        assert not destination.exists()
