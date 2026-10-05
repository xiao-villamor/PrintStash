"""Loading preserves scene placements and returns one materialized mesh. STEP conversion stays bounded in its child process; malformed sources decline without breaking ingestion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import trimesh

from app.modules.media import (
    mesh_loading,
    mesh_previews,
)
from tests.fixtures.mesh_analysis import analyze
from tests.paths import FIXTURES_DIR

from .._meshes import _real_binary_stl_cube


class TestLoadMesh:
    def test_load_mesh_returns_trimesh_for_real_stl(self, tmp_path: Path) -> None:
        p = tmp_path / "cube.stl"
        _real_binary_stl_cube(p)
        mesh = mesh_loading.load_mesh(p)
        assert mesh is not None
        assert len(mesh.faces) > 0

    def test_load_mesh_renders_real_step_fixture(self) -> None:
        path = FIXTURES_DIR / "cascadio_material.stp"

        mesh = mesh_loading.load_mesh(path)
        result = analyze(path)
        geometry, thumbnail = result.geometry, result.image

        assert mesh is not None
        assert len(mesh.faces) > 0
        assert geometry["triangle_count"] == len(mesh.faces)
        assert thumbnail is not None
        assert thumbnail.startswith(mesh_previews._PNG_MAGIC)

    def test_load_mesh_returns_none_for_unrecognised_extension(
        self, tmp_path: Path
    ) -> None:
        # trimesh cannot even pick a loader for an unknown extension, so this raises
        # inside trimesh.load_scene — exercising _load_mesh's broad except-and-log path.
        p = tmp_path / "garbage.foobar"
        p.write_bytes(b"this is not a mesh at all \x00\x01\x02")
        assert mesh_loading.load_mesh(p) is None

    def test_load_mesh_flattens_scene_with_multiple_geometries(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # `_load_mesh` keeps the scene rather than asking trimesh for a mesh, because
        # `dump()` is what bakes each graph path's transforms in. A real Scene is
        # stubbed here so the flattening runs over real trimesh geometry: reading
        # `scene.geometry` instead would return each source mesh once, untransformed.
        # Exporting and reloading the scene would flatten it before we saw it.

        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(extents=[5, 5, 5]), node_name="a")
        scene.add_geometry(
            trimesh.creation.box(extents=[3, 3, 3]).apply_translation([10, 0, 0]),
            node_name="b",
        )
        p = tmp_path / "scene.obj"
        p.write_bytes(b"placeholder")

        monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
        mesh = mesh_loading.load_mesh(p)
        assert mesh is not None
        # Concatenated geometry from both boxes.
        assert len(mesh.faces) == 24

    def test_load_mesh_scene_with_no_trimesh_geometry_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        empty_scene = trimesh.Scene()  # no geometry at all
        p = tmp_path / "empty.obj"
        p.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: empty_scene)
        assert mesh_loading.load_mesh(p) is None

    def test_load_mesh_declines_a_scene_it_cannot_flatten(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`dump()` is where the transforms are applied, and it can throw.

        A malformed component graph — a 3MF referencing a component that is not
        in the file — raises inside trimesh rather than returning an empty scene.
        Letting it escape turns a bad upload into a 500 on the preview route, so
        it becomes the same honest `None` as any other unloadable mesh.
        """

        class UnflattenableScene(trimesh.Scene):
            def dump(self, *_args: object, **_kwargs: object):
                raise ValueError("component graph references a missing object")

        p = tmp_path / "broken-graph.obj"
        p.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: UnflattenableScene())

        assert mesh_loading.load_mesh(p) is None

    def test_load_mesh_declines_a_scene_whose_parts_will_not_concatenate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Two meshes that flatten fine can still refuse to join.

        Mismatched vertex attributes across instances make `concatenate` raise,
        and the geometry is by then already loaded — so this branch is the last
        one before the caller, and the only thing standing between a mixed-format
        3MF and a 500.
        """
        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(extents=[5, 5, 5]), node_name="a")
        scene.add_geometry(trimesh.creation.box(extents=[3, 3, 3]), node_name="b")
        p = tmp_path / "unjoinable.obj"
        p.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)

        def refuse(*_args: object, **_kwargs: object):
            raise ValueError("vertex attributes differ between instances")

        monkeypatch.setattr(trimesh.util, "concatenate", refuse)

        assert mesh_loading.load_mesh(p) is None

    def test_declines_a_non_mesh_concatenation_result(self, tmp_path, monkeypatch):
        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(), node_name="a")
        scene.add_geometry(trimesh.creation.box(), node_name="b")
        path = tmp_path / "source.obj"
        path.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
        monkeypatch.setattr(trimesh.util, "concatenate", lambda *a, **k: object())

        assert mesh_loading.load_mesh(path) is None

    def test_declines_a_malformed_converted_step_artifact(self, tmp_path, monkeypatch):
        from app.modules.media import mesh_isolation

        path = tmp_path / "source.step"
        original = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
        path.write_bytes(original)

        def completed_conversion(command, **_kwargs):
            Path(command[-1]).write_bytes(b"malformed converted mesh artifact")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(mesh_isolation, "supervise_result", completed_conversion)

        assert mesh_loading.load_mesh(path) is None
        assert path.read_bytes() == original

    def test_load_mesh_scene_with_single_geometry_returns_it_directly(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        scene = trimesh.Scene()
        box = trimesh.creation.box(extents=[5, 5, 5])
        scene.add_geometry(box, node_name="a")
        p = tmp_path / "single.obj"
        p.write_bytes(b"placeholder")
        monkeypatch.setattr(trimesh, "load_scene", lambda *a, **k: scene)
        mesh = mesh_loading.load_mesh(p)
        assert mesh is not None
        assert len(mesh.faces) == 12

    def test_load_mesh_returns_none_for_unsupported_loaded_type(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        p = tmp_path / "cloud.obj"
        p.write_bytes(b"placeholder")
        # A loader may return a PointCloud (or other non-mesh geometry) for some
        # inputs; _load_mesh must decline rather than mishandle it.
        monkeypatch.setattr(
            trimesh,
            "load_scene",
            lambda *a, **k: trimesh.points.PointCloud([[0, 0, 0]]),
        )
        assert mesh_loading.load_mesh(p) is None

    def test_load_mesh_uses_typed_loader_without_processing(
        self, tmp_path: Path, monkeypatch
    ) -> None:

        expected = trimesh.creation.box(extents=[1, 1, 1])
        calls: list[tuple[tuple, dict]] = []

        def typed_loader(*args, **kwargs):
            calls.append((args, kwargs))
            return expected

        monkeypatch.setattr(trimesh, "load_scene", typed_loader)
        path = tmp_path / "typed.obj"
        path.write_bytes(b"placeholder")

        assert mesh_loading.load_mesh(path) is expected
        assert calls == [((str(path),), {"process": False})]


class TestLoadStepMeshIsolated:
    def test_preserves_supervisor_memory_refusal(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import ThumbnailFailureReason
        from app.runtime.native_runtime import current_permit

        path = tmp_path / "complex.step"
        path.write_text("ISO-10303-21;")

        def refused(*args, **kwargs):
            raise mesh_isolation.MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)

        monkeypatch.setattr(mesh_isolation, "supervise_result", refused)

        assert mesh_loading.load_step_mesh(path) is None
        assert current_permit() is None
