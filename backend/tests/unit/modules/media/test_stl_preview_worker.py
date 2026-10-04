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

import math
import time
from pathlib import Path

import pytest

from app.modules.media import stl_preview_worker as worker
from tests.factories.content import ascii_stl_facets as _ascii_stl
from tests.factories.content import binary_stl_facets as _binary_stl
from tests.paths import BACKEND_DIR

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


class TestCheckDeadline:
    def test_allows_work_before_the_deadline(self, limits) -> None:
        worker._check_deadline(limits())

    def test_refuses_work_after_the_deadline(self, limits) -> None:
        with pytest.raises(worker._BudgetExceeded):
            worker._check_deadline(limits(deadline=time.monotonic() - 1))


class TestReadPass:
    def test_reads_a_binary_file_as_binary(self, stl, limits) -> None:
        stats = worker._read_pass(
            stl(_binary_stl([TRIANGLE])), limits(), lambda _c: None
        )

        assert stats.triangle_count == 1

    def test_reads_anything_else_as_ascii(self, stl, limits) -> None:
        stats = worker._read_pass(
            stl(_ascii_stl([TRIANGLE])), limits(), lambda _c: None
        )

        assert stats.triangle_count == 1


class TestFrame:
    def test_frames_the_exact_source_bounds(self) -> None:
        import numpy as np

        center, rotation, _mid, extent_x, extent_y = worker._frame(
            (0.0, 0.0, 0.0), (101.0, 2.0, 2.0)
        )

        np.testing.assert_array_equal(center, (50.5, 1.0, 1.0))
        assert rotation.shape == (3, 3)
        assert extent_x > 0 and extent_y > 0

    def test_keeps_camera_coordinates_in_float64(self) -> None:
        import numpy as np

        center, rotation, projected_mid, _x, _y = worker._frame(
            (1e9, 1e9, 1e9), (1e9 + 10, 1e9 + 10, 1e9 + 10)
        )

        assert center.dtype == rotation.dtype == projected_mid.dtype == np.float64
        np.testing.assert_array_equal(center, (1e9 + 5, 1e9 + 5, 1e9 + 5))


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
        import os
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
            env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
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
        self, stl, tmp_path: Path, monkeypatch, override: dict
    ) -> None:
        source = stl(_binary_stl([TRIANGLE]))

        # Exit 2 is "the parent invoked me wrongly" — distinct from a bad file.
        assert self._run_in_process(source, tmp_path, monkeypatch, **override) == 2

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
        self, stl, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = stl(_binary_stl([TRIANGLE, SECOND]))
        real_read_pass = worker._read_pass

        def rewrite_after_reading(path, limits, callback, **kwargs):
            stats = real_read_pass(path, limits, callback, **kwargs)
            path.write_bytes(_binary_stl([TRIANGLE]))
            return stats

        monkeypatch.setattr(worker, "_read_pass", rewrite_after_reading)

        # Two passes over a file somebody can still edit is a TOCTOU; the second
        # pass must not render a frame computed from bytes that are gone.
        assert self._run_in_process(source, tmp_path, monkeypatch) == 3

    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    def test_refuses_replaced_source_with_restored_file_metadata(
        self, stl, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encoding: str
    ) -> None:
        import os

        encode = _binary_stl if encoding == "binary" else _ascii_stl
        source = stl(encode([TRIANGLE, SECOND]))
        original = source.stat()
        real_render = worker._render

        def replace_before_render(path, *args, **kwargs):
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            assert path.stat().st_size == original.st_size
            assert path.stat().st_mtime_ns == original.st_mtime_ns
            assert path.stat().st_ino != original.st_ino
            return real_render(path, *args, **kwargs)

        monkeypatch.setattr(worker, "_render", replace_before_render)

        assert self._run_in_process(source, tmp_path, monkeypatch) == 3
        assert not (tmp_path / "out.json").exists()

    @pytest.mark.parametrize("encoding", ["binary", "ascii"])
    def test_refuses_source_replaced_after_render_before_manifest(
        self, stl, tmp_path, monkeypatch, encoding
    ):
        import os

        encode = _binary_stl if encoding == "binary" else _ascii_stl
        source = stl(encode([TRIANGLE, SECOND]))
        original = source.stat()
        real_render = worker._render

        def replace_after_render(path, *args, **kwargs):
            candidates = real_render(path, *args, **kwargs)
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            assert path.stat().st_size == original.st_size
            assert path.stat().st_mtime_ns == original.st_mtime_ns
            assert path.stat().st_ino != original.st_ino
            return candidates

        monkeypatch.setattr(worker, "_render", replace_after_render)

        assert self._run_in_process(source, tmp_path, monkeypatch) == 3
        assert not (tmp_path / "out.json").exists()

    def test_reports_an_unexpected_failure_distinctly(
        self, stl, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = stl(_binary_stl([TRIANGLE]))

        def exploding(*_args: object, **_kwargs: object):
            raise RuntimeError("renderer exploded")

        monkeypatch.setattr(worker, "_render", exploding)

        # Exit 4 is "something I did not plan for", which the parent logs rather
        # than treating as a rejected file.
        assert self._run_in_process(source, tmp_path, monkeypatch) == 4

    def test_success_publishes_a_complete_manifest(self, stl, tmp_path, monkeypatch):
        import json

        source = stl(_binary_stl([TRIANGLE, SECOND]))
        assert self._run_in_process(source, tmp_path, monkeypatch) == 0
        manifest = json.loads((tmp_path / "out.json").read_text())
        assert manifest["status"] == "complete"
        assert manifest["triangle_count"] == manifest["parsed_triangles"] == 2
        assert manifest["scanned_bytes"] >= source.stat().st_size
        assert (tmp_path / "out.png").read_bytes().startswith(b"\x89PNG")
        assert not (tmp_path / "out.png.tmp").exists()


class TestNondegenerateTriangles:
    def test_retains_valid_streamed_triangle(self) -> None:
        import numpy as np

        view = np.array([[[0.0, 0.0, 0.0], [2.0, 0.0, 1.0], [0.0, 2.0, 1.0]]])

        assert worker._nondegenerate_triangles(view).tolist() == [True]

    @pytest.mark.parametrize(
        "triangle",
        [
            ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0)),
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        ],
        ids=["collinear", "repeated-vertex"],
    )
    def test_rejects_degenerate_streamed_triangle(self, triangle) -> None:
        import numpy as np

        assert worker._nondegenerate_triangles(np.asarray([triangle])).tolist() == [
            False
        ]

    def test_is_invariant_to_rigid_transform(self) -> None:
        import numpy as np

        view = np.array([[[0.0, 0.0, 0.0], [2.0, 0.0, 1.0], [0.0, 2.0, 1.0]]])
        rotation = np.asarray([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])

        assert worker._nondegenerate_triangles(
            view @ rotation.T + np.asarray([5.0, -7.0, 9.0])
        ).tolist() == [True]

    @pytest.mark.parametrize("value", [math.inf, math.nan], ids=["infinity", "nan"])
    def test_rejects_nonfinite_streamed_triangle(self, value: float) -> None:
        import numpy as np

        view = np.array([[[0.0, 0.0, 0.0], [2.0, 0.0, value], [0.0, 2.0, 1.0]]])

        assert worker._nondegenerate_triangles(view).tolist() == [False]


class TestReusableMeasurements:
    def test_reuses_exact_scan_for_the_render_pass(self, stl, tmp_path, monkeypatch):
        from app.modules.media.stl_reader import scan_stl

        source = stl(_binary_stl([TRIANGLE, SECOND]))
        measurements = scan_stl(source)
        argv = TestMain()._argv(source, tmp_path)
        assert worker.main(argv, apply_limits=False) == 0
        expected = (tmp_path / "out.png").read_bytes()
        read_pass = worker._read_pass
        calls = []

        def observe(*args, **kwargs):
            calls.append(True)
            return read_pass(*args, **kwargs)

        monkeypatch.setattr(worker, "_read_pass", observe)
        assert worker.main(argv, apply_limits=False, measurements=measurements) == 0
        assert calls == [True]
        assert (tmp_path / "out.png").read_bytes() == expected

    def test_refuses_stale_measurements_before_rendering(self, stl, tmp_path):
        from app.modules.media.stl_reader import scan_stl

        source = stl(_binary_stl([TRIANGLE, SECOND]))
        measurements = scan_stl(source)
        source.write_bytes(_binary_stl([SECOND, TRIANGLE]))

        code = worker.main(
            TestMain()._argv(source, tmp_path),
            apply_limits=False,
            measurements=measurements,
        )

        assert code == 3
        assert not (tmp_path / "out.png").exists()
        assert not (tmp_path / "out.json").exists()

    @pytest.mark.parametrize("bounds", ["nonfinite", "reversed", "short"])
    def test_refuses_malformed_measurement_bounds(self, stl, tmp_path, bounds):
        from dataclasses import replace

        from app.modules.media.stl_reader import scan_stl

        source = stl(_binary_stl([TRIANGLE, SECOND]))
        measurements = scan_stl(source)
        lower = {
            "nonfinite": (math.nan, 0.0, 0.0),
            "reversed": (2.0, 0.0, 0.0),
            "short": (0.0, 0.0),
        }[bounds]
        measurements = replace(measurements, bounds_min=lower)

        code = worker.main(
            TestMain()._argv(source, tmp_path),
            apply_limits=False,
            measurements=measurements,
        )

        assert code == 3
        assert not (tmp_path / "out.png").exists()
        assert not (tmp_path / "out.json").exists()

    @pytest.mark.parametrize("cap", ["triangles", "source_bytes"])
    def test_reused_measurements_obey_preview_limits(self, stl, tmp_path, cap):
        from app.modules.media.stl_reader import scan_stl

        source = stl(_binary_stl([TRIANGLE, SECOND]))
        measurements = scan_stl(source)
        limits = (
            {"max_triangles": 1}
            if cap == "triangles"
            else {"max_source_bytes": source.stat().st_size - 1}
        )

        code = worker.main(
            TestMain()._argv(source, tmp_path, **limits),
            apply_limits=False,
            measurements=measurements,
        )

        assert code == 3
        assert not (tmp_path / "out.png").exists()
        assert not (tmp_path / "out.json").exists()

    def test_refuses_hint_on_the_subprocess_entry_point(self, stl, tmp_path):
        from app.modules.media.stl_reader import scan_stl

        source = stl(_binary_stl([TRIANGLE, SECOND]))
        measurements = scan_stl(source)

        assert (
            worker.main(TestMain()._argv(source, tmp_path), measurements=measurements)
            == 2
        )
        assert not (tmp_path / "out.png").exists()
        assert not (tmp_path / "out.json").exists()
