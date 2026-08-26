"""Focused coverage for the isolated large-STL preview path."""

from __future__ import annotations

import io
import json
import math
import signal
import struct
import time
from pathlib import Path
from typing import Any

import pytest

from app.core.config import _overlay
from app.services import mesh_processing
from app.services.stl_streaming import (
    STLStreamingLimits,
    STLStreamingResult,
    render_stl_preview_isolated,
)

_RECORD = struct.Struct("<12fH")


def _binary_triangle_stl(
    path: Path, count: int = 12, offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
) -> None:
    triangles = []
    for index in range(count):
        x = float(index % 4) + offset[0]
        y = float(index // 4) + offset[1]
        z = offset[2]
        triangles.append(
            _RECORD.pack(
                0.0,
                0.0,
                1.0,
                x,
                y,
                z,
                x + 0.8,
                y,
                z,
                x,
                y + 0.8,
                z,
                0,
            )
        )
    path.write_bytes(
        b"streaming-test".ljust(80, b"\0")
        + struct.pack("<I", count)
        + b"".join(triangles)
    )


def _ascii_triangle_stl(path: Path) -> None:
    path.write_text(
        """solid streaming
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid streaming
"""
    )


def _binary_annulus_stl(path: Path, segments: int = 48) -> None:
    """Write a thin ring so the streaming worker's center stays transparent."""
    outer, inner = 10.0, 4.0
    record = struct.Struct("<12fH")
    triangles: list[bytes] = []

    def point(radius: float, index: int) -> tuple[float, float, float]:
        angle = 2.0 * math.pi * index / segments
        return radius * math.cos(angle), radius * math.sin(angle), 0.0

    for index in range(segments):
        next_index = (index + 1) % segments
        outer0, outer1 = point(outer, index), point(outer, next_index)
        inner0, inner1 = point(inner, index), point(inner, next_index)
        triangles.extend(
            [
                record.pack(0.0, 0.0, 1.0, *outer0, *outer1, *inner1, 0),
                record.pack(0.0, 0.0, 1.0, *outer0, *inner1, *inner0, 0),
            ]
        )
    path.write_bytes(
        b"streaming-annulus".ljust(80, b"\0")
        + struct.pack("<I", len(triangles))
        + b"".join(triangles)
    )


def _limits() -> STLStreamingLimits:
    return STLStreamingLimits(
        max_triangles=1_000,
        max_source_bytes=1_000_000,
        max_candidates=1_000_000,
        soft_timeout_seconds=5,
        hard_timeout_seconds=10,
        max_rss_bytes=256 * 1024 * 1024,
        address_space_bytes=512 * 1024 * 1024,
    )


def _valid_png(width: int = 32, height: int = 24) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (width, height), (90, 140, 210, 255)).save(output, format="PNG")
    return output.getvalue()


def _manifest(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "version": 1,
        "status": "complete",
        "width": 32,
        "height": 24,
        "triangle_count": 2,
        "parsed_triangles": 2,
        "scanned_bytes": 184,
        "raster_candidates": 16,
        "bounds_min": [0.0, 0.0, 0.0],
        "bounds_max": [1.0, 1.0, 1.0],
    }
    result.update(overrides)
    return result


def test_binary_streaming_preview_is_complete(tmp_path: Path) -> None:
    from PIL import Image

    path = tmp_path / "mesh.stl"
    _binary_triangle_stl(path)
    result = render_stl_preview_isolated(path, width=96, height=72, limits=_limits())

    assert result is not None
    assert result.triangle_count == 12
    assert result.parsed_triangles == 12
    assert result.bounds_min == (0.0, 0.0, 0.0)
    assert result.bounds_max == pytest.approx((3.8, 2.8, 0.0), abs=1e-6)
    with Image.open(io.BytesIO(result.png)) as image:
        assert image.format == "PNG"
        assert image.size == (96, 72)


def test_streaming_renderer_rasterizes_at_requested_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 320x240 result must not be rendered on a half-size work canvas."""
    from app.services import stl_preview_worker

    path = tmp_path / "direct-resolution.stl"
    _binary_triangle_stl(path)
    reservoir = stl_preview_worker._FramingReservoir()

    def collect(vertices: object) -> None:
        import numpy as np

        values = np.asarray(vertices)
        reservoir.add(values.mean(axis=1))

    limits = stl_preview_worker._Limits(
        max_triangles=1_000,
        max_source_bytes=1_000_000,
        max_candidates=1_000_000,
        chunk_triangles=128,
        max_lines=10_000,
        max_line_bytes=64 * 1024,
        deadline=time.monotonic() + 5,
    )
    first = stl_preview_worker._read_pass(path, limits, collect)
    observed: list[tuple[tuple[int, ...], tuple[int, ...], int, int]] = []

    def fake_rasterise(
        image: object,
        zbuffer: object,
        _triangles: object,
        _normals: object,
        _shade: object,
        _base_color: object,
        raster_width: int,
        raster_height: int,
        *,
        budget: Any = None,
    ) -> int:
        import numpy as np

        observed.append(
            (
                np.asarray(image).shape,
                np.asarray(zbuffer).shape,
                raster_width,
                raster_height,
            )
        )
        np.asarray(image)[0, 0] = 200
        np.asarray(zbuffer)[0, 0] = 0.0
        if budget is not None:
            budget.used += 1
        return 1

    from app.services import mesh_render

    monkeypatch.setattr(mesh_render, "_rasterise_triangles", fake_rasterise)
    output = tmp_path / "direct-resolution.png"
    assert (
        stl_preview_worker._render(path, output, 320, 240, limits, first, reservoir) > 0
    )
    assert observed
    assert all(
        image_shape == (240, 320, 3)
        and zbuffer_shape == (240, 320)
        and raster_width == 320
        and raster_height == 240
        for image_shape, zbuffer_shape, raster_width, raster_height in observed
    )


def test_streaming_preview_is_deterministic_and_keeps_background_transparent(
    tmp_path: Path,
) -> None:
    import numpy as np
    from PIL import Image

    path = tmp_path / "deterministic.stl"
    _binary_triangle_stl(path, count=12)
    first = render_stl_preview_isolated(path, width=160, height=120, limits=_limits())
    second = render_stl_preview_isolated(path, width=160, height=120, limits=_limits())

    assert first is not None and second is not None
    assert first.png == second.png
    pixels = np.asarray(Image.open(io.BytesIO(first.png)).convert("RGBA"))
    assert pixels[0, 0, 3] == 0
    assert int((pixels[:, :, 3] > 200).sum()) > 0


def test_streaming_preview_preserves_true_annulus_hole(tmp_path: Path) -> None:
    import numpy as np
    from PIL import Image

    path = tmp_path / "annulus.stl"
    _binary_annulus_stl(path)
    result = render_stl_preview_isolated(path, width=160, height=120, limits=_limits())

    assert result is not None
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    alpha = pixels[:, :, 3]
    center = alpha[alpha.shape[0] // 2, alpha.shape[1] // 2]
    assert center < 32
    assert float((alpha > 200).mean()) > 0.10


def test_ascii_streaming_preview_is_complete(tmp_path: Path) -> None:
    path = tmp_path / "mesh.stl"
    _ascii_triangle_stl(path)
    result = render_stl_preview_isolated(path, width=96, height=72, limits=_limits())

    assert result is not None
    assert result.triangle_count == 1
    assert result.bounds_max == (1.0, 1.0, 0.0)


def test_ascii_streaming_accepts_complete_file_without_endsolid(tmp_path: Path) -> None:
    path = tmp_path / "mesh-no-endsolid.stl"
    _ascii_triangle_stl(path)
    path.write_bytes(path.read_bytes().replace(b"endsolid streaming\n", b""))

    result = render_stl_preview_isolated(path, width=96, height=72, limits=_limits())

    assert result is not None
    assert result.triangle_count == 1


def test_translated_mesh_is_centered_in_preview(tmp_path: Path) -> None:
    import numpy as np
    from PIL import Image

    path = tmp_path / "translated.stl"
    _binary_triangle_stl(path, offset=(10_000.0, -20_000.0, 300.0))
    result = render_stl_preview_isolated(path, width=160, height=120, limits=_limits())

    assert result is not None
    pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
    ys, xs = np.where(pixels[:, :, 3] > 20)
    assert 0.25 < float(xs.mean() / pixels.shape[1]) < 0.75
    assert 0.25 < float(ys.mean() / pixels.shape[0]) < 0.75


@pytest.mark.parametrize(
    "contents",
    [
        b"solid broken\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n",
        b"solid broken\nfacet normal nan 0 1\nouter loop\n",
    ],
)
def test_malformed_ascii_never_produces_a_preview(
    tmp_path: Path, contents: bytes
) -> None:
    path = tmp_path / "broken.stl"
    path.write_bytes(contents)

    assert render_stl_preview_isolated(path, limits=_limits()) is None


def test_streaming_rejects_source_budget_before_starting_worker(tmp_path: Path) -> None:
    path = tmp_path / "too-large.stl"
    _binary_triangle_stl(path)
    limits = STLStreamingLimits(max_source_bytes=10)

    assert render_stl_preview_isolated(path, limits=limits) is None


def test_streaming_rejects_candidate_budget_instead_of_publishing_partial_output(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-budget.stl"
    _binary_triangle_stl(path)
    limits = STLStreamingLimits(
        max_triangles=100,
        max_source_bytes=1_000_000,
        max_candidates=1,
        max_rss_bytes=256 * 1024 * 1024,
        address_space_bytes=512 * 1024 * 1024,
    )

    assert render_stl_preview_isolated(path, width=96, height=72, limits=limits) is None


@pytest.mark.parametrize("case", ["truncated", "nonfinite"])
def test_binary_streaming_rejects_truncated_or_nonfinite_source(
    tmp_path: Path, case: str
) -> None:
    path = tmp_path / f"broken-{case}.stl"
    if case == "truncated":
        _binary_triangle_stl(path, count=1)
        path.write_bytes(path.read_bytes()[:-1])
    else:
        path.write_bytes(
            b"streaming-test".ljust(80, b"\0")
            + struct.pack("<I", 1)
            + _RECORD.pack(
                0.0,
                0.0,
                1.0,
                float("nan"),
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0,
            )
        )

    assert render_stl_preview_isolated(path, limits=_limits()) is None


def test_forced_over_cap_ascii_uses_streaming_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "over-cap-ascii.stl"
    facet = """facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
"""
    path.write_text("solid ascii\n" + facet * 2 + "endsolid ascii\n")
    monkeypatch.setattr(mesh_processing, "_exceeds_cap", lambda _path: True)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not load")),
    )

    geometry, thumbnail = mesh_processing.analyze_mesh(path, width=96, height=72)

    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is True
    assert geometry["triangle_count"] == 2


def test_over_cap_mesh_processing_uses_streaming_before_sampling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "over-cap.stl"
    _binary_triangle_stl(path)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
    geometry, thumbnail = mesh_processing.analyze_mesh(path, width=96, height=72)

    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is True
    assert geometry["triangle_count"] == 12


def test_normal_stl_render_exception_uses_streaming_before_sampling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import numpy as np

    path = tmp_path / "normal-render-fails.stl"
    _binary_triangle_stl(path)
    monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1_000)
    monkeypatch.setattr(
        mesh_processing,
        "_load_mesh",
        lambda _path: SimpleNamespace(
            vertices=np.zeros((3, 3)),
            bounds=np.array([[0.0, 0.0, 0.0], [4.0, 3.0, 0.0]]),
            faces=np.zeros((12, 3), dtype=np.int64),
            volume=0.0,
        ),
    )
    monkeypatch.setattr(
        mesh_processing.mesh_render,
        "render_mesh_thumbnail",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("renderer crash")),
    )

    _geometry, thumbnail = mesh_processing.analyze_mesh(path, width=96, height=72)

    assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
    assert thumbnail.complete is True


@pytest.mark.parametrize("case", ["missing", "partial", "oversized", "malformed"])
def test_decode_rejects_missing_or_incomplete_manifest(
    tmp_path: Path, case: str
) -> None:
    from app.services import stl_streaming

    output = tmp_path / "preview.png"
    manifest = tmp_path / "result.json"
    output.write_bytes(_valid_png())
    if case == "missing":
        pass
    elif case == "partial":
        manifest.write_text(json.dumps(_manifest(status="running")))
    elif case == "oversized":
        manifest.write_bytes(b"x" * (stl_streaming._MAX_MANIFEST_BYTES + 1))
    else:
        manifest.write_bytes(b"{not-json")

    assert (
        stl_streaming._decode_result(
            output,
            manifest,
            width=32,
            height=24,
            limits=_limits(),
        )
        is None
    )


@pytest.mark.parametrize("case", ["invalid", "wrong_size", "manifest_only", "png_only"])
def test_decode_rejects_invalid_or_unpaired_preview(tmp_path: Path, case: str) -> None:
    from app.services import stl_streaming

    output = tmp_path / "preview.png"
    manifest = tmp_path / "result.json"
    if case != "manifest_only":
        output.write_bytes(b"not-a-png" if case == "invalid" else _valid_png(1, 1))
    if case != "png_only":
        manifest.write_text(json.dumps(_manifest()))

    assert (
        stl_streaming._decode_result(
            output,
            manifest,
            width=32,
            height=24,
            limits=_limits(),
        )
        is None
    )


@pytest.mark.parametrize("case", ["nonfinite_bounds", "over_budget_count"])
def test_decode_rejects_forged_manifest_values(tmp_path: Path, case: str) -> None:
    from app.services import stl_streaming

    output = tmp_path / "preview.png"
    manifest = tmp_path / "result.json"
    output.write_bytes(_valid_png())
    if case == "nonfinite_bounds":
        values = _manifest(bounds_min=[float("nan"), 0.0, 0.0])
    else:
        values = _manifest(triangle_count=2, parsed_triangles=2)
    manifest.write_text(json.dumps(values))
    limits = (
        _limits()
        if case == "nonfinite_bounds"
        else STLStreamingLimits(
            max_triangles=1,
            max_source_bytes=1_000_000,
            max_candidates=1_000_000,
        )
    )

    assert (
        stl_streaming._decode_result(
            output,
            manifest,
            width=32,
            height=24,
            limits=limits,
        )
        is None
    )


@pytest.mark.parametrize("case", ["timeout", "rss", "crash"])
def test_worker_failure_is_killed_or_reaped_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from app.services import stl_streaming

    path = tmp_path / "worker-failure.stl"
    _binary_triangle_stl(path, count=1)
    calls: list[tuple[str, object]] = []
    limits = _limits()

    class FakeProcess:
        pid = 4242
        returncode = -9 if case == "crash" else None

        def poll(self):
            return self.returncode

        def communicate(self, **kwargs):
            calls.append(("communicate", kwargs))
            return b"", b""

    process = FakeProcess()

    def kill_group(pgid: int, sig: signal.Signals) -> None:
        calls.append(("killpg", (pgid, sig)))
        process.returncode = -9

    monkeypatch.setattr(stl_streaming.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(stl_streaming.os, "getpgid", lambda _pid: process.pid)
    monkeypatch.setattr(stl_streaming.os, "killpg", kill_group)
    if case == "rss":
        monkeypatch.setattr(
            stl_streaming,
            "_read_rss_bytes",
            lambda _pid: _limits().max_rss_bytes + 1,
        )
    elif case == "timeout":
        times = iter((0.0, 1.0))
        monkeypatch.setattr(stl_streaming.time, "monotonic", lambda: next(times))
        monkeypatch.setattr(stl_streaming.time, "sleep", lambda _seconds: None)
        limits = STLStreamingLimits(
            max_triangles=100,
            max_source_bytes=1_000_000,
            max_candidates=1_000_000,
            soft_timeout_seconds=0.1,
            hard_timeout_seconds=0.5,
        )

    result = render_stl_preview_isolated(path, limits=limits)

    assert result is None
    assert any(name == "communicate" for name, _value in calls)
    if case != "crash":
        assert any(name == "killpg" for name, _value in calls)


def test_worker_invocation_avoids_thread_unsafe_preexec_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import stl_streaming

    path = tmp_path / "invocation.stl"
    _binary_triangle_stl(path, count=1)
    calls: dict[str, object] = {}

    class FinishedProcess:
        pid = 1234
        returncode = 0

        def poll(self):
            return self.returncode

        def communicate(self, **kwargs):
            return b"", b""

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return FinishedProcess()

    monkeypatch.setattr(stl_streaming.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        stl_streaming,
        "_decode_result",
        lambda *args, **kwargs: STLStreamingResult(
            png=b"png",
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(1.0, 1.0, 0.0),
            triangle_count=1,
            parsed_triangles=1,
            scanned_bytes=134,
            raster_candidates=1,
        ),
    )
    result = render_stl_preview_isolated(path, limits=_limits())

    assert result is not None
    kwargs = calls["kwargs"]
    assert isinstance(kwargs, dict)
    assert "preexec_fn" not in kwargs
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    command = calls["command"]
    assert isinstance(command, list)
    expected_parent_index = command.index("--expected-parent-pid")
    assert command[expected_parent_index + 1] == str(stl_streaming.os.getpid())


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


def _worker_limits(deadline: float | None = None):
    from app.services import stl_preview_worker

    return stl_preview_worker._Limits(
        max_triangles=1_000,
        max_source_bytes=1_000_000,
        max_candidates=1_000_000,
        chunk_triangles=2,
        max_lines=1_000,
        max_line_bytes=1_000,
        deadline=time.monotonic() + 5 if deadline is None else deadline,
    )


def _worker_cli_args(
    source: Path,
    output: Path,
    manifest: Path,
    **overrides: object,
) -> list[str]:
    values: dict[str, object] = {
        "width": 48,
        "height": 36,
        "max_triangles": 1_000,
        "max_source_bytes": 1_000_000,
        "max_candidates": 1_000_000,
        "chunk_triangles": 128,
        "max_lines": 10_000,
        "max_line_bytes": 64 * 1024,
        "timeout_seconds": 5,
        "address_space_bytes": 512 * 1024 * 1024,
        "cpu_seconds": 7,
        "expected_parent_pid": 1,
    }
    values.update(overrides)
    return [
        str(source),
        str(output),
        str(manifest),
        str(values["width"]),
        str(values["height"]),
        "--max-triangles",
        str(values["max_triangles"]),
        "--max-source-bytes",
        str(values["max_source_bytes"]),
        "--max-candidates",
        str(values["max_candidates"]),
        "--chunk-triangles",
        str(values["chunk_triangles"]),
        "--max-lines",
        str(values["max_lines"]),
        "--max-line-bytes",
        str(values["max_line_bytes"]),
        "--timeout-seconds",
        str(values["timeout_seconds"]),
        "--address-space-bytes",
        str(values["address_space_bytes"]),
        "--cpu-seconds",
        str(values["cpu_seconds"]),
        "--expected-parent-pid",
        str(values["expected_parent_pid"]),
    ]


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
