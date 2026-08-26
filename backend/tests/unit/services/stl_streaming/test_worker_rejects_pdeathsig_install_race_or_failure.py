"""Defends worker rejects pdeathsig install race or failure at the services stl streaming unit boundary.

A regression could accept an incomplete preview or weaken worker isolation.
"""

from __future__ import annotations

from ._stl_streaming_shared import (
    _RECORD,
    Path,
    STLStreamingLimits,
    STLStreamingResult,
    _ascii_triangle_stl,
    _binary_triangle_stl,
    _limits,
    _manifest,
    _overlay,
    _valid_png,
    _worker_cli_args,
    _worker_limits,
    json,
    pytest,
    render_stl_preview_isolated,
    signal,
    struct,
)


@pytest.mark.parametrize(
    ("ppids", "expected_parent_pid", "prctl_result"),
    [
        ((4343, 4343), 4242, 0),
        ((4242, 4343), 4242, 0),
        ((4242, 1), 4242, 0),
        ((4242, 4242), 4242, -1),
    ],
)
def test_worker_rejects_pdeathsig_install_race_or_failure(
    monkeypatch: pytest.MonkeyPatch,
    ppids: tuple[int, int],
    expected_parent_pid: int,
    prctl_result: int,
) -> None:
    import ctypes
    import resource

    from app.services import stl_preview_worker

    observed: list[tuple[object, ...]] = []

    class FakeLibc:
        def prctl(self, *args: object) -> int:
            observed.append(args)
            return prctl_result

    ppid_values = iter(ppids)
    monkeypatch.setattr(stl_preview_worker.sys, "platform", "linux")
    monkeypatch.setattr(stl_preview_worker.os, "getppid", lambda: next(ppid_values))
    monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: FakeLibc())

    with pytest.raises(stl_preview_worker._InvalidSTL):
        stl_preview_worker._apply_worker_limits(
            address_space=512 * 1024 * 1024,
            cpu_seconds=5,
            expected_parent_pid=expected_parent_pid,
        )

    if ppids[0] == expected_parent_pid:
        assert observed == [(1, signal.SIGKILL, 0, 0, 0)]
    else:
        assert observed == []


def test_worker_accepts_legitimate_pid_one_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Container PID 1 is valid when it is the verified launching parent."""
    import ctypes
    import resource

    from app.services import stl_preview_worker

    observed: list[tuple[object, ...]] = []

    class FakeLibc:
        def prctl(self, *args: object) -> int:
            observed.append(args)
            return 0

    ppid_values = iter((1, 1))
    monkeypatch.setattr(stl_preview_worker.sys, "platform", "linux")
    monkeypatch.setattr(stl_preview_worker.os, "getppid", lambda: next(ppid_values))
    monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: FakeLibc())

    stl_preview_worker._apply_worker_limits(
        address_space=512 * 1024 * 1024,
        cpu_seconds=5,
        expected_parent_pid=1,
    )

    assert observed == [(1, signal.SIGKILL, 0, 0, 0)]


def test_worker_rejects_invalid_expected_parent_pid_before_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import resource

    from app.services import stl_preview_worker

    calls: list[str] = []
    monkeypatch.setattr(stl_preview_worker.os, "getppid", lambda: 1)
    monkeypatch.setattr(
        resource,
        "setrlimit",
        lambda *_args: calls.append("setrlimit"),
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: calls.append("CDLL"))

    with pytest.raises(stl_preview_worker._InvalidSTL, match="invalid"):
        stl_preview_worker._apply_worker_limits(
            address_space=512 * 1024 * 1024,
            cpu_seconds=5,
            expected_parent_pid=0,
        )

    assert calls == []


def test_worker_parses_exact_binary_stl_in_bounded_chunks(tmp_path: Path) -> None:
    from app.services import stl_preview_worker

    path = tmp_path / "chunked.stl"
    _binary_triangle_stl(path, count=5)
    chunks: list[int] = []

    def observe(vertices: object) -> None:
        chunks.append(len(vertices))

    result = stl_preview_worker._read_binary(path, _worker_limits(), observe)

    assert chunks == [2, 2, 1]
    assert result == stl_preview_worker._PassStats(
        triangle_count=5,
        scanned_bytes=334,
        bounds_min=(0.0, 0.0, 0.0),
        bounds_max=pytest.approx((3.8, 1.8, 0.0), abs=1e-6),
    )


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(b"solid empty\nendsolid empty\n", id="empty"),
        pytest.param(b"solid x\nfacet sideways 0 0 1\n", id="facet-normal"),
        pytest.param(
            b"solid x\nfacet normal 0 0 1\nvertex 0 0 0\n", id="missing-outer"
        ),
        pytest.param(
            b"solid x\nfacet normal 0 0 1\nouter loop\nvertex nope 0 0\n",
            id="invalid-number",
        ),
        pytest.param(
            b"solid x\nfacet normal 0 0 1\nouter loop\nvertex inf 0 0\n",
            id="nonfinite-number",
        ),
        pytest.param(
            b"solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n"
            b"vertex 1 0 0\nvertex 0 1 0\nvertex 0 0 1\n",
            id="missing-endloop",
        ),
        pytest.param(
            b"solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n"
            b"vertex 1 0 0\nvertex 0 1 0\nendloop extra\n",
            id="invalid-endloop",
        ),
        pytest.param(
            b"solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n"
            b"vertex 1 0 0\nvertex 0 1 0\nendloop\nnot-endfacet\n",
            id="invalid-endfacet",
        ),
        pytest.param(b"solid x\n\xff\n", id="non-ascii"),
        pytest.param(b"solid x\nendsolid x\nsolid extra\n", id="after-endsolid"),
    ],
)
def test_worker_rejects_malformed_ascii_facet_structure(
    tmp_path: Path, contents: bytes
) -> None:
    from app.services import stl_preview_worker

    path = tmp_path / "malformed-ascii.stl"
    path.write_bytes(contents)

    with pytest.raises(stl_preview_worker._InvalidSTL):
        stl_preview_worker._read_ascii(path, _worker_limits(), lambda _chunk: None)


@pytest.mark.parametrize(
    ("limit_overrides", "expected"),
    [
        pytest.param({"max_source_bytes": 10}, "source budget", id="source-bytes"),
        pytest.param({"max_triangles": 0}, "triangle budget", id="triangles"),
        pytest.param({"max_lines": 1}, "line budget", id="lines"),
        pytest.param({"max_line_bytes": 5}, "line too long", id="line-bytes"),
    ],
)
def test_worker_enforces_ascii_source_budgets(
    tmp_path: Path, limit_overrides: dict[str, int], expected: str
) -> None:
    from app.services import stl_preview_worker

    path = tmp_path / "budgeted-ascii.stl"
    _ascii_triangle_stl(path)
    values = _worker_limits().__dict__ | limit_overrides
    limits = stl_preview_worker._Limits(**values)

    with pytest.raises(stl_preview_worker._InvalidSTL, match=expected):
        stl_preview_worker._read_ascii(path, limits, lambda _chunk: None)


def test_worker_enforces_deadline_during_a_pass() -> None:
    from app.services import stl_preview_worker

    with pytest.raises(stl_preview_worker._BudgetExceeded, match="deadline"):
        stl_preview_worker._check_deadline(_worker_limits(deadline=0.0))


def test_worker_keeps_framing_samples_bounded_and_deterministic() -> None:
    import numpy as np

    from app.services import stl_preview_worker

    centers = np.arange((stl_preview_worker._RESERVOIR_SIZE + 100) * 3).reshape(-1, 3)
    first = stl_preview_worker._FramingReservoir()
    second = stl_preview_worker._FramingReservoir()

    first.add(centers)
    second.add(centers)

    assert first.seen == stl_preview_worker._RESERVOIR_SIZE + 100
    assert len(first.values) == stl_preview_worker._RESERVOIR_SIZE
    assert first.values == second.values


def test_worker_refuses_preview_with_no_visible_triangles(tmp_path: Path) -> None:
    path = tmp_path / "degenerate.stl"
    path.write_bytes(
        b"degenerate".ljust(80, b"\0")
        + struct.pack("<I", 1)
        + _RECORD.pack(*(0.0 for _index in range(12)), 0)
    )

    assert (
        render_stl_preview_isolated(path, width=48, height=36, limits=_limits()) is None
    )


def test_worker_cli_writes_complete_manifest_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from app.services import stl_preview_worker

    source = tmp_path / "cli-success.stl"
    output = tmp_path / "cli-success.png"
    manifest = tmp_path / "cli-success.json"
    _binary_triangle_stl(source, count=3)
    monkeypatch.setattr(
        stl_preview_worker, "_apply_worker_limits", lambda *a, **k: None
    )

    status = stl_preview_worker.main(_worker_cli_args(source, output, manifest))

    assert status == 0
    payload = json.loads(manifest.read_text())
    assert payload == {
        "version": 1,
        "status": "complete",
        "width": 48,
        "height": 36,
        "triangle_count": 3,
        "parsed_triangles": 3,
        "scanned_bytes": 234,
        "raster_candidates": payload["raster_candidates"],
        "bounds_min": [0.0, 0.0, 0.0],
        "bounds_max": pytest.approx([2.8, 0.8, 0.0], abs=1e-6),
    }
    assert payload["raster_candidates"] > 0
    with Image.open(output) as image:
        assert image.size == (48, 36)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"width": 0}, id="width-zero"),
        pytest.param({"height": 2049}, id="height-over-cap"),
        pytest.param({"max_triangles": 0}, id="triangles-zero"),
        pytest.param({"max_source_bytes": (1 << 30) + 1}, id="source-over-cap"),
        pytest.param({"max_candidates": 20_000_001}, id="candidates-over-cap"),
        pytest.param({"chunk_triangles": 8193}, id="chunk-over-cap"),
        pytest.param({"max_lines": 10_000_001}, id="lines-over-cap"),
        pytest.param({"max_line_bytes": 64 * 1024 + 1}, id="line-bytes-over-cap"),
        pytest.param({"timeout_seconds": float("nan")}, id="timeout-nonfinite"),
        pytest.param({"timeout_seconds": 46}, id="timeout-over-cap"),
        pytest.param({"address_space_bytes": 0}, id="address-zero"),
        pytest.param({"cpu_seconds": 0}, id="cpu-zero"),
        pytest.param({"expected_parent_pid": 0}, id="parent-zero"),
    ],
)
def test_worker_cli_rejects_unsafe_budgets_before_applying_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    from app.services import stl_preview_worker

    source = tmp_path / "cli-invalid.stl"
    output = tmp_path / "cli-invalid.png"
    manifest = tmp_path / "cli-invalid.json"
    _binary_triangle_stl(source, count=1)
    applied: list[bool] = []
    monkeypatch.setattr(
        stl_preview_worker, "_apply_worker_limits", lambda *a, **k: applied.append(True)
    )

    status = stl_preview_worker.main(
        _worker_cli_args(source, output, manifest, **overrides)
    )

    assert status == 2
    assert applied == []
    assert not manifest.exists()


def test_worker_cli_leaves_no_result_for_malformed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import stl_preview_worker

    source = tmp_path / "cli-malformed.stl"
    output = tmp_path / "cli-malformed.png"
    manifest = tmp_path / "cli-malformed.json"
    source.write_text("solid unfinished\nfacet normal 0 0 1\n")
    monkeypatch.setattr(
        stl_preview_worker, "_apply_worker_limits", lambda *a, **k: None
    )

    status = stl_preview_worker.main(_worker_cli_args(source, output, manifest))

    assert status == 3
    assert not output.exists()
    assert not manifest.exists()


@pytest.mark.parametrize(
    ("configured", "expected_soft", "expected_hard"),
    [
        pytest.param("invalid", 45.0, 60.0, id="non-numeric"),
        pytest.param(0, 1.0, 16.0, id="below-minimum"),
        pytest.param(12, 12.0, 27.0, id="in-range"),
        pytest.param(90, 45.0, 60.0, id="above-maximum"),
    ],
)
def test_streaming_clamps_operator_timeout_to_safe_interval(
    monkeypatch: pytest.MonkeyPatch,
    configured: object,
    expected_soft: float,
    expected_hard: float,
) -> None:
    from app.services import stl_streaming

    monkeypatch.setitem(_overlay, "mesh_stream_timeout_seconds", configured)

    limits = stl_streaming._effective_limits()

    assert limits.soft_timeout_seconds == expected_soft
    assert limits.hard_timeout_seconds == expected_hard


@pytest.mark.parametrize(
    ("budget", "expected"),
    [
        pytest.param(None, 256 * 1024 * 1024, id="unavailable"),
        pytest.param(1, 32 * 1024 * 1024, id="below-floor"),
        pytest.param(64 * 1024 * 1024, 64 * 1024 * 1024, id="within-range"),
        pytest.param(1024 * 1024 * 1024, 256 * 1024 * 1024, id="above-cap"),
    ],
)
def test_streaming_clamps_worker_memory_share(
    monkeypatch: pytest.MonkeyPatch, budget: int | None, expected: int
) -> None:
    from app.services import mesh_processing, stl_streaming

    monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: budget)

    assert stl_streaming._worker_memory_budget() == expected


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"max_triangles": 0}, id="triangles-zero"),
        pytest.param({"max_source_bytes": (1 << 30) + 1}, id="source-over-cap"),
        pytest.param({"max_candidates": 20_000_001}, id="candidates-over-cap"),
        pytest.param({"chunk_triangles": 8193}, id="chunk-over-cap"),
        pytest.param({"max_lines": 10_000_001}, id="lines-over-cap"),
        pytest.param({"max_line_bytes": 64 * 1024 + 1}, id="line-bytes-over-cap"),
        pytest.param({"soft_timeout_seconds": 46}, id="soft-timeout-over-cap"),
        pytest.param({"hard_timeout_seconds": 61}, id="hard-timeout-over-cap"),
        pytest.param({"max_rss_bytes": 0}, id="rss-zero"),
        pytest.param(
            {"address_space_bytes": 512 * 1024 * 1024 + 1}, id="address-over-cap"
        ),
    ],
)
def test_streaming_rejects_budgets_that_weaken_worker_isolation(
    overrides: dict[str, object],
) -> None:
    from app.services import stl_streaming

    values = _limits().__dict__ | overrides

    assert not stl_streaming._within_worker_hard_bounds(STLStreamingLimits(**values))


def test_streaming_terminates_worker_when_process_group_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import stl_streaming

    killed: list[bool] = []

    class Process:
        pid = 42

        def kill(self) -> None:
            killed.append(True)

    monkeypatch.setattr(
        stl_streaming.os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(ProcessLookupError()),
    )

    stl_streaming._terminate_process_group(Process())

    assert killed == [True]


@pytest.mark.parametrize(
    "values",
    [
        pytest.param([], id="not-dict"),
        pytest.param(_manifest(version=2), id="wrong-version"),
        pytest.param(_manifest(width=31), id="wrong-dimensions"),
        pytest.param(_manifest(triangle_count=True), id="boolean-count"),
        pytest.param(_manifest(parsed_triangles=1), id="count-mismatch"),
        pytest.param(_manifest(bounds_min=(0.0, 0.0, 0.0)), id="tuple-bounds"),
        pytest.param(_manifest(bounds_max=[-1.0, 1.0, 1.0]), id="inverted-bounds"),
    ],
)
def test_streaming_rejects_forged_manifest_shapes(values: object) -> None:
    from app.services import stl_streaming

    assert not stl_streaming._valid_manifest(values, width=32, height=24)


def test_streaming_decodes_complete_budget_compliant_png(tmp_path: Path) -> None:
    from app.services import stl_streaming

    output = tmp_path / "valid.png"
    manifest = tmp_path / "valid.json"
    png = _valid_png()
    output.write_bytes(png)
    manifest.write_text(json.dumps(_manifest()))

    result = stl_streaming._decode_result(
        output, manifest, width=32, height=24, limits=_limits()
    )

    assert result == STLStreamingResult(
        png=png,
        bounds_min=(0.0, 0.0, 0.0),
        bounds_max=(1.0, 1.0, 1.0),
        triangle_count=2,
        parsed_triangles=2,
        scanned_bytes=184,
        raster_candidates=16,
    )


@pytest.mark.parametrize(
    ("width", "height"),
    [
        pytest.param(0, 24, id="width-zero"),
        pytest.param(32, 2049, id="height-over-cap"),
    ],
)
def test_streaming_rejects_invalid_dimensions_before_worker_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    height: int,
) -> None:
    from app.services import stl_streaming

    path = tmp_path / "invalid-size.stl"
    _binary_triangle_stl(path, count=1)
    monkeypatch.setattr(
        stl_streaming.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("worker launched")),
    )

    assert render_stl_preview_isolated(path, width=width, height=height) is None


def test_streaming_returns_none_when_worker_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import stl_streaming

    path = tmp_path / "spawn-failure.stl"
    _binary_triangle_stl(path, count=1)
    monkeypatch.setattr(
        stl_streaming.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no process slots")),
    )

    assert render_stl_preview_isolated(path, limits=_limits()) is None
