"""The disposable subprocess that turns a STEP file into a mesh.

This worker exists because tessellating a STEP file is unbounded work on untrusted input:
a CAD file can expand into hundreds of millions of triangles and take the whole process
down with it. Running it out-of-process means the parent can cap its memory and time and
kill it without losing anything, and it is why the worker holds **no application state**
and talks to its parent only through an exit code and a written file.

So the exit codes are the contract, and each one means something different to the parent:
`0` the mesh is written and can be used, `2` the parent invoked it wrongly, `3` the file
tessellated to more triangles than the ceiling allows, `4` the file contained no mesh at
all. Collapsing 3 and 4 would lose the difference between "too big to show" and "nothing
to show", which the UI reports differently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from app.services import step_worker
from tests.paths import FIXTURES_DIR

TRIANGLE_LIMIT_ENV = "PRINTSTASH_STEP_TRIANGLE_LIMIT"


@pytest.fixture
def run_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Invoke `main()` the way the parent process does, and hand back its exit code."""

    def run(loaded: object, *, triangle_limit: int = 1_000_000) -> tuple[int, Path]:
        source = tmp_path / "part.step"
        source.write_bytes(b"ISO-10303-21;")
        destination = tmp_path / "part.glb"
        monkeypatch.setenv(TRIANGLE_LIMIT_ENV, str(triangle_limit))
        monkeypatch.setattr(
            step_worker.sys, "argv", ["step_worker", str(source), str(destination)]
        )
        monkeypatch.setattr(trimesh, "load_mesh", lambda *_a, **_k: loaded)
        return step_worker.main(), destination

    return run


def _box() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(1.0, 1.0, 1.0))


class TestMain:
    def test_writes_the_tessellated_mesh(self, run_worker) -> None:
        code, destination = run_worker(_box())

        assert code == 0
        assert destination.exists()

    def test_flattens_a_scene_into_one_mesh(self, run_worker) -> None:
        scene = trimesh.Scene({"a": _box(), "b": _box()})

        code, destination = run_worker(scene)

        # A STEP assembly arrives as a scene; the viewer wants one mesh.
        assert code == 0
        assert destination.exists()

    def test_reports_a_file_with_no_mesh_in_it(self, run_worker) -> None:
        code, _ = run_worker(trimesh.Scene({}))

        assert code == 4

    def test_reports_something_that_is_not_a_mesh_at_all(self, run_worker) -> None:
        code, _ = run_worker(trimesh.PointCloud([[0, 0, 0], [1, 1, 1]]))

        assert code == 4

    def test_reports_a_mesh_past_the_triangle_ceiling(self, run_worker) -> None:
        code, destination = run_worker(_box(), triangle_limit=1)

        # "Too big to show" is not the same answer as "nothing to show".
        assert code == 3
        assert not destination.exists()

    def test_reports_a_parent_that_invoked_it_wrongly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(step_worker.sys, "argv", ["step_worker"])

        assert step_worker.main() == 2

    def test_refuses_a_file_that_tessellates_past_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        source = FIXTURES_DIR / "cascadio_material.stp"
        destination = tmp_path / "over-cap.glb"
        monkeypatch.setenv("PRINTSTASH_STEP_TRIANGLE_LIMIT", "1")
        monkeypatch.setattr(
            step_worker.sys, "argv", ["step_worker", str(source), str(destination)]
        )

        # Exit 3, and nothing written: a partial or over-cap mesh on disk would
        # be indistinguishable to the parent from a usable one.
        assert step_worker.main() == 3
        assert not destination.exists()
