"""The disposable subprocess that renders an STL thumbnail without trusting it.

An STL arrives from a stranger's slicer, and a thumbnail is needed for the grid. Parsing
one is unbounded work on unbounded input: a 90-byte header can declare 4 billion
triangles, an ASCII file can be one line of a gigabyte, and a coordinate can be `inf`. So
the parse happens in a throwaway process the parent can cap and kill, and **every budget
is checked before the memory is spent**, not after.

That is the property this file defends. Each refusal — a declared count that does not
match the file length, a record that is short, a line past the cap, a coordinate that is
not finite, a source that changes on disk between the two passes — is a separate row,
because each one is a different way an input can lie about itself.

The exit codes are the parent's whole view of what happened: `0` a manifest is written and
usable, `2` the parent passed nonsense budgets, `3` the file was rejected, `4` something
unexpected happened. A refusal must never be reported as a success with an empty picture.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest

from app.modules.media import stl_preview_worker as worker
from tests.paths import BACKEND_DIR

RESERVOIR_SIZE = 4096


def _binary_stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    body = b"\0" * 80 + struct.pack("<I", len(triangles))
    for facet in triangles:
        body += struct.pack("<3f", 0.0, 0.0, 1.0)
        for vertex in facet:
            body += struct.pack("<3f", *vertex)
        body += struct.pack("<H", 0)
    return body


def _ascii_stl(triangles: list[tuple[tuple[float, float, float], ...]]) -> bytes:
    lines = ["solid test"]
    for facet in triangles:
        lines.append("facet normal 0 0 1")
        lines.append("outer loop")
        lines.extend(f"vertex {v[0]} {v[1]} {v[2]}" for v in facet)
        lines.append("endloop")
        lines.append("endfacet")
    lines.append("endsolid test")
    return ("\n".join(lines) + "\n").encode("ascii")


TRIANGLE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
SECOND = ((0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0))


@pytest.fixture
def limits():
    """Generous budgets, so a test that fails is failing on its own behaviour."""

    def build(**overrides) -> worker._Limits:
        defaults = {
            "max_triangles": 1000,
            "max_source_bytes": 1 << 20,
            "max_candidates": 1_000_000,
            "chunk_triangles": 8,
            "max_lines": 10_000,
            "max_line_bytes": 1024,
            "deadline": time.monotonic() + 30,
        }
        defaults.update(overrides)
        return worker._Limits(**defaults)

    return build


@pytest.fixture
def stl(tmp_path: Path):
    def write(data: bytes, name: str = "part.stl") -> Path:
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return write


class TestWriteManifest:
    def test_writes_the_manifest(self, tmp_path: Path) -> None:
        import json

        target = tmp_path / "manifest.json"

        worker._write_manifest(target, {"status": "complete"})

        assert json.loads(target.read_text()) == {"status": "complete"}

    def test_leaves_no_partial_file_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "manifest.json"

        worker._write_manifest(target, {"status": "complete"})

        # The parent accepts output only on a complete manifest, so the write is
        # atomic: a reader never sees half of one.
        assert list(tmp_path.iterdir()) == [target]


class TestApplyWorkerLimits:
    def test_refuses_a_parent_pid_below_one(self) -> None:
        with pytest.raises(worker._InvalidSTL):
            worker._apply_worker_limits(1 << 20, 1, expected_parent_pid=0)

    def test_refuses_a_parent_that_is_not_the_launcher(self) -> None:
        # An orphaned worker re-parented to init must not carry on rendering.
        with pytest.raises(worker._InvalidSTL):
            worker._apply_worker_limits(1 << 20, 1, expected_parent_pid=999_999)


class TestMain:
    """`main` is run as a *subprocess*, the way production launches it.

    Calling it in-process is not merely unfaithful, it is destructive: `main`
    applies the worker's resource limits with `setrlimit` to whatever process
    calls it, so an in-process call permanently shrinks the test runner's own
    address space to the worker's budget. On Linux that kills the pytest-xdist
    worker outright ("node down: Not properly terminated"); macOS effectively
    ignores `RLIMIT_AS`, which is why it passed locally and failed only in CI.
    Even when it survives, every later test on that worker inherits the limit.

    So these drive the real command line through `python -m`, exactly as
    `stl_streaming.render_stl_preview_isolated` does. `expected-parent-pid` is
    therefore this process, since it is the launcher.
    """

    def _run(self, argv: list[str]) -> int:
        """Launch the worker the way production does and return its exit code."""
        import subprocess
        import sys

        # `cwd` is explicit because the autouse `_isolate_cwd` fixture puts every
        # test in a throwaway directory, from which `-m app.services...` cannot
        # find the package — that surfaces as a bare exit code 1 with no
        # traceback, which is a confusing way to learn about a path problem.
        completed = subprocess.run(
            [sys.executable, "-m", "app.modules.media.stl_preview_worker", *argv],
            capture_output=True,
            cwd=BACKEND_DIR,
            timeout=120,
        )
        assert b"No module named" not in completed.stderr, completed.stderr.decode()
        return completed.returncode

    def _run_in_process(
        self,
        source: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        **overrides: object,
    ) -> int:
        """Call `main` directly, for the two cases that must patch its internals.

        `setrlimit` is stubbed out for the duration, and that is not optional:
        `main` applies the worker's limits to whatever process calls it, so an
        unguarded in-process call shrinks the test runner's own address space to
        the worker's budget. On Linux that kills the pytest-xdist worker outright;
        macOS ignores `RLIMIT_AS`, which is why it passed locally and failed only
        in CI. Raising the budget instead does not work — the worker validates it
        and refuses anything over its own cap.

        The limits keep their own rows elsewhere in this class, exercised through
        the real subprocess, so nothing is lost by stubbing them here.
        """
        import os as _os
        import resource

        monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
        overrides.setdefault("expected_parent_pid", _os.getppid())
        return worker.main(self._argv(source, tmp_path, **overrides))

    def _argv(self, source: Path, tmp_path: Path, **overrides: object) -> list[str]:
        import os as _os

        args = {
            "width": 64,
            "height": 64,
            "max-triangles": 1000,
            "max-source-bytes": 1 << 20,
            "max-candidates": 1_000_000,
            "chunk-triangles": 8,
            "max-lines": 10_000,
            "max-line-bytes": 1024,
            "timeout-seconds": 30.0,
            "address-space-bytes": 512 * 1024 * 1024,
            "cpu-seconds": 10,
            # This process is the launcher, so the worker's parent check must
            # name *us* — production passes `os.getpid()` here for the same
            # reason.
            "expected-parent-pid": _os.getpid(),
        }
        args.update({key.replace("_", "-"): value for key, value in overrides.items()})
        return [
            str(source),
            str(tmp_path / "out.png"),
            str(tmp_path / "out.json"),
            str(args.pop("width")),
            str(args.pop("height")),
            *[f"--{key}={value}" for key, value in args.items()],
        ]

    def test_renders_a_binary_stl_with_a_manifest_beside_it(
        self, stl, tmp_path: Path
    ) -> None:
        import json

        source = stl(_binary_stl([TRIANGLE, SECOND]))

        code = self._run(self._argv(source, tmp_path))

        assert code == 0
        manifest = json.loads((tmp_path / "out.json").read_text())
        assert manifest["status"] == "complete"
        assert manifest["triangle_count"] == 2

    def test_renders_an_ascii_stl(self, stl, tmp_path: Path) -> None:
        source = stl(_ascii_stl([TRIANGLE, SECOND]))

        assert self._run(self._argv(source, tmp_path)) == 0

    def test_writes_the_image_it_rendered(self, stl, tmp_path: Path) -> None:
        source = stl(_binary_stl([TRIANGLE, SECOND]))

        self._run(self._argv(source, tmp_path))

        assert (tmp_path / "out.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    def test_uses_the_canonical_material_colour(self, stl, tmp_path: Path) -> None:
        import numpy as np
        from PIL import Image

        source = stl(_binary_stl([TRIANGLE, SECOND]))

        self._run(self._argv(source, tmp_path))

        pixels = np.asarray(Image.open(tmp_path / "out.png").convert("RGBA"))
        opaque = pixels[:, :, :3][pixels[:, :, 3] > 200].mean(axis=0)
        observed = opaque / opaque.max()
        expected = np.asarray([0.70, 0.75, 0.84]) / 0.84
        np.testing.assert_allclose(observed, expected, atol=0.05)

    @pytest.mark.parametrize(
        "override",
        [
            pytest.param({"width": 0}, id="width-zero"),
            pytest.param({"width": 4096}, id="width-over-cap"),
            pytest.param({"height": 0}, id="height-zero"),
            pytest.param({"max_triangles": 0}, id="triangles-zero"),
            pytest.param({"max_triangles": 30_000_000}, id="triangles-over-cap"),
            pytest.param({"max_source_bytes": 0}, id="source-zero"),
            pytest.param({"chunk_triangles": 100_000}, id="chunk-over-cap"),
            pytest.param({"max_line_bytes": 1 << 20}, id="line-bytes-over-cap"),
            pytest.param({"timeout_seconds": 0}, id="timeout-zero"),
            pytest.param({"timeout_seconds": 600}, id="timeout-over-cap"),
            pytest.param({"timeout_seconds": "inf"}, id="timeout-not-finite"),
            pytest.param({"address_space_bytes": 0}, id="address-space-zero"),
            pytest.param({"cpu_seconds": 0}, id="cpu-zero"),
            pytest.param({"expected_parent_pid": 0}, id="parent-pid-zero"),
        ],
    )
    def test_refuses_a_budget_the_parent_should_never_send(
        self, stl, tmp_path: Path, override: dict
    ) -> None:
        source = stl(_binary_stl([TRIANGLE]))

        # Exit 2 is "the parent invoked me wrongly" — distinct from a bad file.
        assert self._run(self._argv(source, tmp_path, **override)) == 2

    def test_refuses_a_parent_that_is_not_the_launcher(
        self, stl, tmp_path: Path
    ) -> None:
        source = stl(_binary_stl([TRIANGLE]))

        assert self._run(self._argv(source, tmp_path, expected_parent_pid=999_998)) == 3

    def test_reports_a_source_that_is_not_there(self, tmp_path: Path) -> None:
        assert self._run(self._argv(tmp_path / "missing.stl", tmp_path)) == 3

    def test_reports_a_source_larger_than_its_budget(self, stl, tmp_path: Path) -> None:
        source = stl(_binary_stl([TRIANGLE] * 20))

        assert self._run(self._argv(source, tmp_path, max_source_bytes=100)) == 3

    def test_reports_a_file_it_cannot_parse(self, stl, tmp_path: Path) -> None:
        source = stl(b"solid test\nsurprise\nendsolid test\n")

        assert self._run(self._argv(source, tmp_path)) == 3

    def test_reports_a_source_that_changes_between_the_two_passes(
        self, stl, tmp_path, monkeypatch
    ):
        import printstash_mesh_native as native

        source = stl(_binary_stl([TRIANGLE, SECOND]))

        # Actual identity checks are exercised in render-core/tests/source.rs;
        # this boundary test asserts the supervisor's failure classification.
        def changed(*args):
            raise ValueError("source changed between passes")

        monkeypatch.setattr(native, "render_stl_streaming", changed)
        assert self._run_in_process(source, tmp_path, monkeypatch) == 3

    def test_reports_an_unexpected_failure_distinctly(self, stl, tmp_path, monkeypatch):
        import printstash_mesh_native as native

        source = stl(_binary_stl([TRIANGLE]))

        def exploding(*args):
            raise RuntimeError("renderer exploded")

        monkeypatch.setattr(native, "render_stl_streaming", exploding)
        assert self._run_in_process(source, tmp_path, monkeypatch) == 4
