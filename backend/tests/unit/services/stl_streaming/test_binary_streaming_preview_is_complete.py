"""Defends binary streaming preview is complete at the services stl streaming unit boundary.

A regression could accept an incomplete preview or weaken worker isolation.
"""

from __future__ import annotations

from ._stl_streaming_shared import (
    _RECORD,
    Any,
    Path,
    STLStreamingLimits,
    STLStreamingResult,
    _ascii_triangle_stl,
    _binary_annulus_stl,
    _binary_triangle_stl,
    _limits,
    _manifest,
    _overlay,
    _valid_png,
    io,
    json,
    mesh_processing,
    pytest,
    render_stl_preview_isolated,
    signal,
    struct,
    time,
)


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
