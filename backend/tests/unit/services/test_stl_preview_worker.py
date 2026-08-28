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
import struct
import time
from pathlib import Path

import pytest

from app.services import stl_preview_worker as worker
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


class TestFramingReservoir:
    def test_keeps_every_centroid_while_there_is_room(self) -> None:
        reservoir = worker._FramingReservoir()

        reservoir.add([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])

        assert reservoir.values == [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
        assert reservoir.seen == 2

    def test_stops_growing_at_the_reservoir_size(self) -> None:
        reservoir = worker._FramingReservoir()

        reservoir.add([(float(i), 0.0, 0.0) for i in range(RESERVOIR_SIZE + 500)])

        # The whole point is a bounded footprint on an unbounded file.
        assert len(reservoir.values) == RESERVOIR_SIZE
        assert reservoir.seen == RESERVOIR_SIZE + 500

    def test_samples_the_same_way_every_run(self) -> None:
        centroids = [(float(i), 0.0, 0.0) for i in range(RESERVOIR_SIZE * 3)]
        first, second = worker._FramingReservoir(), worker._FramingReservoir()

        first.add(centroids)
        second.add(centroids)

        # A process-global RNG would make the same file frame differently on a
        # retry, so the sampling uses its own LCG.
        assert first.values == second.values


class TestCheckDeadline:
    def test_allows_work_before_the_deadline(self, limits) -> None:
        worker._check_deadline(limits())

    def test_refuses_work_after_the_deadline(self, limits) -> None:
        with pytest.raises(worker._BudgetExceeded):
            worker._check_deadline(limits(deadline=time.monotonic() - 1))


class TestValidValue:
    @pytest.mark.parametrize(
        "value",
        [0.0, 1.5, -1.5, 3.4028234663852886e38],
        ids=["zero", "pos", "neg", "max"],
    )
    def test_accepts_a_coordinate_a_float32_can_hold(self, value: float) -> None:
        assert worker._valid_value(value) is True

    @pytest.mark.parametrize(
        "value",
        [math.inf, -math.inf, math.nan, 1e39],
        ids=["inf", "-inf", "nan", "over-float32"],
    )
    def test_rejects_a_coordinate_a_float32_cannot_hold(self, value: float) -> None:
        assert worker._valid_value(value) is False


class TestSourceIsBinary:
    def test_recognises_a_binary_stl_by_its_exact_length(self, stl) -> None:
        path = stl(_binary_stl([TRIANGLE, SECOND]))

        assert worker._source_is_binary(path) == (2, path.stat().st_size)

    def test_rejects_a_file_too_short_to_hold_a_header(self, stl) -> None:
        assert worker._source_is_binary(stl(b"short")) is None

    def test_rejects_a_declared_count_the_file_length_contradicts(self, stl) -> None:
        data = bytearray(_binary_stl([TRIANGLE]))
        struct.pack_into("<I", data, 80, 5)

        # A 90-byte file claiming four billion triangles is the whole reason this
        # check exists.
        assert worker._source_is_binary(stl(bytes(data))) is None

    def test_rejects_a_declared_count_of_zero(self, stl) -> None:
        assert worker._source_is_binary(stl(_binary_stl([]))) is None

    def test_treats_ascii_as_not_binary(self, stl) -> None:
        assert worker._source_is_binary(stl(_ascii_stl([TRIANGLE]))) is None

    def test_reports_a_file_that_is_not_there(self, tmp_path: Path) -> None:
        assert worker._source_is_binary(tmp_path / "missing.stl") is None


class TestReadBinary:
    def test_reads_every_triangle(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE, SECOND]))

        stats = worker._read_binary(path, limits(), lambda _chunk: None)

        assert stats.triangle_count == 2

    def test_reports_the_bounding_box_it_saw(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE, SECOND]))

        stats = worker._read_binary(path, limits(), lambda _chunk: None)

        assert stats.bounds_min == (0.0, 0.0, 0.0)
        assert stats.bounds_max == (1.0, 1.0, 1.0)

    def test_hands_each_chunk_to_the_caller(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE] * 20))
        chunks: list[int] = []

        worker._read_binary(
            path, limits(chunk_triangles=8), lambda chunk: chunks.append(len(chunk))
        )

        # Bounded chunks, not the whole file, is what keeps the footprint flat.
        assert chunks == [8, 8, 4]

    def test_refuses_a_file_that_is_not_exactly_a_binary_stl(self, stl, limits) -> None:
        with pytest.raises(worker._InvalidSTL):
            worker._read_binary(stl(b"short"), limits(), lambda _chunk: None)

    def test_refuses_more_triangles_than_the_budget_allows(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE] * 5))

        with pytest.raises(worker._BudgetExceeded):
            worker._read_binary(path, limits(max_triangles=2), lambda _chunk: None)

    def test_refuses_a_source_larger_than_the_budget_allows(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE] * 5))

        with pytest.raises(worker._BudgetExceeded):
            worker._read_binary(path, limits(max_source_bytes=100), lambda _chunk: None)

    def test_refuses_a_coordinate_that_is_not_finite(self, stl, limits) -> None:
        path = stl(
            _binary_stl([((math.inf, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))])
        )

        with pytest.raises(worker._InvalidSTL):
            worker._read_binary(path, limits(), lambda _chunk: None)

    def test_refuses_to_read_past_the_deadline(self, stl, limits) -> None:
        path = stl(_binary_stl([TRIANGLE]))

        with pytest.raises(worker._BudgetExceeded):
            worker._read_binary(
                path, limits(deadline=time.monotonic() - 1), lambda _chunk: None
            )


class TestParseFloat:
    def test_reads_a_number(self) -> None:
        assert worker._parse_float("1.5") == 1.5

    def test_refuses_something_that_is_not_a_number(self) -> None:
        with pytest.raises(worker._InvalidSTL):
            worker._parse_float("not-a-number")

    def test_refuses_a_number_a_float32_cannot_hold(self) -> None:
        with pytest.raises(worker._InvalidSTL):
            worker._parse_float("inf")


class TestReadAscii:
    def test_reads_every_facet(self, stl, limits) -> None:
        path = stl(_ascii_stl([TRIANGLE, SECOND]))

        stats = worker._read_ascii(path, limits(), lambda _chunk: None)

        assert stats.triangle_count == 2

    def test_reports_the_bounding_box_it_saw(self, stl, limits) -> None:
        path = stl(_ascii_stl([TRIANGLE, SECOND]))

        stats = worker._read_ascii(path, limits(), lambda _chunk: None)

        assert stats.bounds_min == (0.0, 0.0, 0.0)
        assert stats.bounds_max == (1.0, 1.0, 1.0)

    def test_ignores_lines_that_carry_no_geometry(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(
            b"solid test\n", b"solid test\n\n# a comment\n// another\n"
        )

        stats = worker._read_ascii(stl(data), limits(), lambda _chunk: None)

        assert stats.triangle_count == 1

    def test_accepts_a_file_that_ends_without_endsolid(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"endsolid test\n", b"")

        # Several real slicers omit it; EOF at a facet boundary is unambiguous.
        stats = worker._read_ascii(stl(data), limits(), lambda _chunk: None)

        assert stats.triangle_count == 1

    def test_refuses_a_file_that_ends_mid_facet(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"endfacet\nendsolid test\n", b"")

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(), lambda _chunk: None)

    def test_refuses_a_file_with_no_facets_at_all(self, stl, limits) -> None:
        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(
                stl(b"solid test\nendsolid test\n"), limits(), lambda _chunk: None
            )

    def test_refuses_content_after_endsolid(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]) + b"facet normal 0 0 1\n"

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(), lambda _chunk: None)

    @pytest.mark.parametrize(
        ("broken", "replacement"),
        [
            pytest.param(b"facet normal 0 0 1", b"facet normal 0 0", id="short-normal"),
            pytest.param(b"outer loop", b"inner loop", id="wrong-loop"),
            pytest.param(b"vertex 0.0 0.0 0.0", b"vertex 0.0 0.0", id="short-vertex"),
            pytest.param(b"endloop", b"endlop", id="misspelt-endloop"),
            pytest.param(b"endfacet", b"endfact", id="misspelt-endfacet"),
        ],
    )
    def test_refuses_a_malformed_facet(
        self, stl, limits, broken: bytes, replacement: bytes
    ) -> None:
        data = _ascii_stl([TRIANGLE]).replace(broken, replacement, 1)

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(), lambda _chunk: None)

    def test_refuses_an_unknown_keyword(self, stl, limits) -> None:
        data = b"solid test\nsurprise\n" + _ascii_stl([TRIANGLE])

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(), lambda _chunk: None)

    def test_refuses_a_line_longer_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE])

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(max_line_bytes=8), lambda _c: None)

    def test_refuses_more_lines_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE, SECOND])

        with pytest.raises(worker._BudgetExceeded):
            worker._read_ascii(stl(data), limits(max_lines=3), lambda _c: None)

    def test_refuses_more_bytes_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE, SECOND])

        with pytest.raises(worker._BudgetExceeded):
            worker._read_ascii(stl(data), limits(max_source_bytes=20), lambda _c: None)

    def test_refuses_more_triangles_than_the_budget_allows(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE, SECOND])

        with pytest.raises(worker._BudgetExceeded):
            worker._read_ascii(stl(data), limits(max_triangles=1), lambda _c: None)

    def test_refuses_bytes_that_are_not_ascii(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"solid test", b"solid t\xffst")

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(), lambda _c: None)

    def test_refuses_a_coordinate_that_is_not_a_number(self, stl, limits) -> None:
        data = _ascii_stl([TRIANGLE]).replace(b"vertex 0.0 0.0 0.0", b"vertex a b c", 1)

        with pytest.raises(worker._InvalidSTL):
            worker._read_ascii(stl(data), limits(), lambda _c: None)


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
    def test_frames_a_mesh_around_its_sampled_centre(self, limits) -> None:
        reservoir = worker._FramingReservoir()
        reservoir.add([(0.5, 0.5, 0.5), (0.4, 0.6, 0.5)])

        center, rotation, robust_min, robust_max, _mid, extent_x, extent_y = (
            worker._frame((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), reservoir)
        )

        assert rotation.shape == (3, 3)
        assert extent_x > 0 and extent_y > 0
        assert len(center) == 3

    def test_falls_back_to_the_exact_bounds_when_nothing_was_sampled(self) -> None:
        center, _rotation, robust_min, robust_max, _mid, _x, _y = worker._frame(
            (0.0, 0.0, 0.0), (2.0, 2.0, 2.0), worker._FramingReservoir()
        )

        # An empty reservoir must still frame something rather than divide by
        # zero: the exact bounds stand in for the samples it never got.
        assert all(abs(value - 0.0) < 0.05 for value in robust_min)
        assert all(abs(value - 2.0) < 0.05 for value in robust_max)

    def test_widens_a_degenerate_axis_back_to_the_exact_bounds(self) -> None:
        reservoir = worker._FramingReservoir()
        reservoir.add([(0.5, 0.5, 0.0), (0.5, 0.5, 0.0)])

        _c, _r, robust_min, robust_max, _mid, _x, _y = worker._frame(
            (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), reservoir
        )

        # A flat sample on an axis would frame the model edge-on and render a line.
        assert robust_min[0] == 0.0
        assert robust_max[0] == 1.0


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
            [sys.executable, "-m", "app.services.stl_preview_worker", *argv],
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
        self, stl, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = stl(_binary_stl([TRIANGLE, SECOND]))
        real_read_pass = worker._read_pass

        def rewrite_after_reading(path, limits, callback):
            stats = real_read_pass(path, limits, callback)
            path.write_bytes(_binary_stl([TRIANGLE]))
            return stats

        monkeypatch.setattr(worker, "_read_pass", rewrite_after_reading)

        # Two passes over a file somebody can still edit is a TOCTOU; the second
        # pass must not render a frame computed from bytes that are gone.
        assert self._run_in_process(source, tmp_path, monkeypatch) == 3

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
