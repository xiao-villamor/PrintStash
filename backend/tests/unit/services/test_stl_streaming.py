"""Focused coverage for the isolated large-STL preview path."""

from __future__ import annotations

import io
import json
import math
import signal
import struct
import time
import zlib
from pathlib import Path
from typing import Any

import pytest

from app.core.config import _overlay
from app.services import mesh_processing, stl_streaming
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


def _png_declaring(width: int, height: int) -> bytes:
    """A structurally valid PNG whose header claims *width* x *height* pixels.

    Pillow reads the dimensions out of IHDR and refuses the image before it
    decodes a single row, so a decompression bomb is a few hundred bytes.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\0"))
        + chunk(b"IEND", b"")
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


class TestEffectiveLimits:
    """The budgets the worker is launched with, and the ceilings they cannot pass."""

    def test_uses_the_configured_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(_overlay, "mesh_stream_timeout_seconds", 20)

        assert stl_streaming._effective_limits().soft_timeout_seconds == 20.0

    def test_clamps_a_timeout_that_is_too_generous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_stream_timeout_seconds", 9999)

        # An operator cannot configure away the isolation this whole module is
        # built on; 45s is the ceiling whatever the setting says.
        assert stl_streaming._effective_limits().soft_timeout_seconds == 45.0

    def test_clamps_a_timeout_that_is_too_small(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_stream_timeout_seconds", 0)

        assert stl_streaming._effective_limits().soft_timeout_seconds == 1.0

    def test_falls_back_when_the_setting_is_not_a_number(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(_overlay, "mesh_stream_timeout_seconds", "soon")

        # A typo'd setting must not stop previews being generated at all.
        assert stl_streaming._effective_limits().soft_timeout_seconds == 45.0

    def test_gives_the_hard_timeout_room_beyond_the_soft_one(self) -> None:
        limits = stl_streaming._effective_limits()

        # The soft timeout asks the worker to stop; the hard one kills it. They
        # cannot be the same instant or a clean shutdown never happens.
        assert limits.hard_timeout_seconds > limits.soft_timeout_seconds


class TestWorkerMemoryBudget:
    def test_uses_the_shared_per_job_share_when_it_is_smaller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            mesh_processing, "_step_memory_budget_bytes", lambda: 64 * 1024 * 1024
        )

        assert stl_streaming._worker_memory_budget() == 64 * 1024 * 1024

    def test_never_goes_below_a_floor_a_render_can_work_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: 1)

        # A budget below this cannot load Pillow, so the worker would die on
        # every file rather than on large ones.
        assert stl_streaming._worker_memory_budget() == 32 * 1024 * 1024

    def test_falls_back_when_there_is_no_shared_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mesh_processing, "_step_memory_budget_bytes", lambda: None)

        assert stl_streaming._worker_memory_budget() > 0


class TestWithinWorkerHardBounds:
    """Caller-supplied budgets can only ever be *tighter* than the built-in ones."""

    def test_accepts_the_defaults(self) -> None:
        assert stl_streaming._within_worker_hard_bounds(STLStreamingLimits()) is True

    @pytest.mark.parametrize(
        "override",
        [
            pytest.param({"max_triangles": 0}, id="triangles-zero"),
            pytest.param({"max_triangles": 10**9}, id="triangles-over-ceiling"),
            pytest.param({"max_source_bytes": 0}, id="source-zero"),
            pytest.param({"max_candidates": 0}, id="candidates-zero"),
            pytest.param({"chunk_triangles": 0}, id="chunk-zero"),
            pytest.param({"chunk_triangles": 99_999}, id="chunk-over-ceiling"),
            pytest.param({"max_lines": 0}, id="lines-zero"),
            pytest.param({"max_line_bytes": 10**6}, id="line-bytes-over-ceiling"),
            pytest.param({"soft_timeout_seconds": 0}, id="soft-timeout-zero"),
            pytest.param({"soft_timeout_seconds": 999}, id="soft-timeout-over-ceiling"),
            pytest.param({"hard_timeout_seconds": 999}, id="hard-timeout-over-ceiling"),
            pytest.param({"max_rss_bytes": 0}, id="rss-zero"),
            pytest.param({"address_space_bytes": 0}, id="address-space-zero"),
            pytest.param(
                {"address_space_bytes": 10**12}, id="address-space-over-ceiling"
            ),
        ],
    )
    def test_refuses_a_budget_that_would_weaken_the_isolation(
        self, override: dict
    ) -> None:
        limits = STLStreamingLimits(**override)

        assert stl_streaming._within_worker_hard_bounds(limits) is False


class TestValidManifest:
    """A worker's manifest is checked before its PNG is trusted."""

    BASE = {
        "version": 1,
        "status": "complete",
        "width": 64,
        "height": 48,
        "triangle_count": 12,
        "parsed_triangles": 12,
        "scanned_bytes": 700,
        "raster_candidates": 100,
        "bounds_min": [0.0, 0.0, 0.0],
        "bounds_max": [1.0, 1.0, 1.0],
    }

    def _check(self, **overrides: object) -> bool:
        return stl_streaming._valid_manifest(
            {**self.BASE, **overrides}, width=64, height=48
        )

    def test_accepts_a_complete_manifest(self) -> None:
        assert self._check() is True

    def test_refuses_something_that_is_not_an_object(self) -> None:
        assert stl_streaming._valid_manifest("not a dict", width=64, height=48) is False

    @pytest.mark.parametrize(
        "override",
        [
            pytest.param({"version": 99}, id="wrong-version"),
            pytest.param({"status": "partial"}, id="not-complete"),
            pytest.param({"width": 100}, id="wrong-width"),
            pytest.param({"height": 100}, id="wrong-height"),
        ],
    )
    def test_refuses_a_manifest_from_a_different_run(self, override: dict) -> None:
        # A stale manifest left by an earlier worker would publish the wrong
        # picture at the wrong size.
        assert self._check(**override) is False

    @pytest.mark.parametrize(
        "key",
        ["triangle_count", "parsed_triangles", "scanned_bytes", "raster_candidates"],
    )
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(0, id="zero"),
            pytest.param("1", id="string"),
            pytest.param(True, id="bool"),
        ],
    )
    def test_refuses_a_count_it_cannot_use(self, key: str, value: object) -> None:
        assert self._check(**{key: value}) is False

    def test_refuses_a_partial_parse_reported_as_complete(self) -> None:
        # `parsed < total` means the worker stopped early; publishing that PNG
        # would show half a model as if it were the whole thing.
        assert self._check(parsed_triangles=6) is False

    @pytest.mark.parametrize(
        "bounds",
        [
            pytest.param({"bounds_min": "no"}, id="not-a-list"),
            pytest.param({"bounds_min": [0.0, 0.0]}, id="too-short"),
            pytest.param({"bounds_min": [0.0, 0.0, "x"]}, id="not-a-number"),
            pytest.param({"bounds_min": [0.0, 0.0, math.inf]}, id="not-finite"),
            pytest.param({"bounds_min": [0.0, 0.0, True]}, id="bool"),
        ],
    )
    def test_refuses_bounds_it_cannot_use(self, bounds: dict) -> None:
        assert self._check(**bounds) is False

    def test_refuses_bounds_that_are_inside_out(self) -> None:
        # A minimum above its maximum frames nothing and would divide by a
        # negative extent downstream.
        assert (
            self._check(bounds_min=[2.0, 0.0, 0.0], bounds_max=[1.0, 1.0, 1.0]) is False
        )


class TestDecodeResult:
    """The worker's output is read back and re-validated before it is published."""

    def _write(self, tmp_path: Path, manifest: object, png: bytes) -> tuple[Path, Path]:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        output = tmp_path / "out.png"
        output.write_bytes(png)
        return output, manifest_path

    def _png(self, width: int = 8, height: int = 8) -> bytes:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()

    def _manifest(self, **overrides: object) -> dict[str, Any]:
        base = {
            "version": 1,
            "status": "complete",
            "width": 8,
            "height": 8,
            "triangle_count": 12,
            "parsed_triangles": 12,
            "scanned_bytes": 700,
            "raster_candidates": 100,
            "bounds_min": [0.0, 0.0, 0.0],
            "bounds_max": [1.0, 1.0, 1.0],
        }
        base.update(overrides)
        return base

    def _decode(self, tmp_path: Path, manifest: object, png: bytes):
        output, manifest_path = self._write(tmp_path, manifest, png)
        return stl_streaming._decode_result(
            output, manifest_path, width=8, height=8, limits=STLStreamingLimits()
        )

    def test_returns_the_render_when_everything_checks_out(
        self, tmp_path: Path
    ) -> None:
        result = self._decode(tmp_path, self._manifest(), self._png())

        assert result is not None
        assert result.triangle_count == 12

    def test_refuses_a_manifest_that_is_not_json(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("{not json")
        output = tmp_path / "out.png"
        output.write_bytes(self._png())

        assert (
            stl_streaming._decode_result(
                output, manifest_path, width=8, height=8, limits=STLStreamingLimits()
            )
            is None
        )

    def test_refuses_a_manifest_larger_than_the_cap(self, tmp_path: Path) -> None:
        manifest = self._manifest(padding="x" * (stl_streaming._MAX_MANIFEST_BYTES + 1))

        assert self._decode(tmp_path, manifest, self._png()) is None

    def test_refuses_a_manifest_that_is_missing(self, tmp_path: Path) -> None:
        output = tmp_path / "out.png"
        output.write_bytes(self._png())

        assert (
            stl_streaming._decode_result(
                output,
                tmp_path / "gone.json",
                width=8,
                height=8,
                limits=STLStreamingLimits(),
            )
            is None
        )

    @pytest.mark.parametrize(
        ("field", "limit_field"),
        [
            ("triangle_count", "max_triangles"),
            ("scanned_bytes", "max_source_bytes"),
            ("raster_candidates", "max_candidates"),
        ],
    )
    def test_refuses_a_run_that_exceeded_its_budget(
        self, tmp_path: Path, field: str, limit_field: str
    ) -> None:
        limits = STLStreamingLimits()
        manifest = self._manifest(**{field: getattr(limits, limit_field) + 1})

        # The worker reports what it did; if that is past the budget the parent
        # set, the output is not the one it asked for.
        assert self._decode(tmp_path, manifest, self._png()) is None

    def test_refuses_bytes_that_are_not_a_png(self, tmp_path: Path) -> None:
        assert self._decode(tmp_path, self._manifest(), b"not a png") is None

    def test_refuses_a_png_larger_than_the_cap(self, tmp_path: Path) -> None:
        oversized = b"\x89PNG\r\n\x1a\n" + b"\0" * stl_streaming._MAX_PNG_BYTES

        assert self._decode(tmp_path, self._manifest(), oversized) is None

    def test_refuses_a_png_of_the_wrong_size(self, tmp_path: Path) -> None:
        # The worker was told 8×8; anything else means it rendered something
        # other than what was asked for.
        assert self._decode(tmp_path, self._manifest(), self._png(16, 16)) is None

    def test_refuses_a_png_that_is_only_a_png_header(self, tmp_path: Path) -> None:
        assert (
            self._decode(tmp_path, self._manifest(), b"\x89PNG\r\n\x1a\ntruncated")
            is None
        )

    def test_refuses_a_decompression_bomb_without_raising(self, tmp_path: Path) -> None:
        # A few hundred bytes that declare 30000x30000 pixels. Pillow raises
        # DecompressionBombError — not an OSError — straight out of `open()`, and
        # the bare `except Exception` is the only thing keeping a hostile worker
        # output from taking the ingestion job down with it.
        bomb = _png_declaring(30_000, 30_000)

        assert self._decode(tmp_path, self._manifest(), bomb) is None


class TestRenderStlPreviewIsolated:
    """Everything checked in the parent, before a worker process is spawned.

    Each of these refusals happens before `fork`. That ordering is the point: a
    worker is a process with its own memory and CPU ceilings, and paying for one
    only to have it fail on an input the parent could have rejected is how a
    preview queue turns into a fork bomb under a directory of junk files.
    """

    @pytest.mark.parametrize(
        ("width", "height"),
        [
            pytest.param(0, 480, id="zero-width"),
            pytest.param(640, 0, id="zero-height"),
            pytest.param(stl_streaming._MAX_RENDER_DIMENSION + 1, 480, id="too-wide"),
            pytest.param(640, stl_streaming._MAX_RENDER_DIMENSION + 1, id="too-tall"),
        ],
    )
    def test_refuses_a_resolution_it_will_not_render(
        self, tmp_path: Path, width: int, height: int
    ) -> None:
        source = tmp_path / "cube.stl"
        _binary_triangle_stl(source)

        assert render_stl_preview_isolated(source, width=width, height=height) is None

    def test_refuses_a_file_that_is_not_an_stl(self, tmp_path: Path) -> None:
        source = tmp_path / "cube.3mf"
        _binary_triangle_stl(source)

        # The worker only speaks STL; the suffix is the only thing the parent has
        # to go on before it spends a process on the file.
        assert render_stl_preview_isolated(source) is None

    def test_refuses_a_budget_that_would_weaken_the_isolation(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "cube.stl"
        _binary_triangle_stl(source)

        # A caller may only ever tighten the built-in ceilings. An unbounded
        # address space would defeat the isolation the worker exists to provide.
        assert (
            render_stl_preview_isolated(
                source, limits=STLStreamingLimits(address_space_bytes=10**12)
            )
            is None
        )

    def test_refuses_a_source_that_is_no_longer_there(self, tmp_path: Path) -> None:
        # A queued preview races the library: the artifact can be purged between
        # the job being enqueued and the render starting.
        assert render_stl_preview_isolated(tmp_path / "gone.stl") is None

    def test_binary_streaming_preview_is_complete(self, tmp_path: Path) -> None:
        from PIL import Image

        path = tmp_path / "mesh.stl"
        _binary_triangle_stl(path)
        result = render_stl_preview_isolated(
            path, width=96, height=72, limits=_limits()
        )

        assert result is not None
        assert result.triangle_count == 12
        assert result.parsed_triangles == 12
        assert result.bounds_min == (0.0, 0.0, 0.0)
        assert result.bounds_max == pytest.approx((3.8, 2.8, 0.0), abs=1e-6)
        with Image.open(io.BytesIO(result.png)) as image:
            assert image.format == "PNG"
            assert image.size == (96, 72)

    def test_ascii_streaming_preview_is_complete(self, tmp_path: Path) -> None:
        path = tmp_path / "mesh.stl"
        _ascii_triangle_stl(path)
        result = render_stl_preview_isolated(
            path, width=96, height=72, limits=_limits()
        )

        assert result is not None
        assert result.triangle_count == 1
        assert result.bounds_max == (1.0, 1.0, 0.0)

    def test_ascii_streaming_accepts_complete_file_without_endsolid(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "mesh-no-endsolid.stl"
        _ascii_triangle_stl(path)
        path.write_bytes(path.read_bytes().replace(b"endsolid streaming\n", b""))

        result = render_stl_preview_isolated(
            path, width=96, height=72, limits=_limits()
        )

        assert result is not None
        assert result.triangle_count == 1

    def test_streaming_renderer_rasterizes_at_requested_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
            stl_preview_worker._render(path, output, 320, 240, limits, first, reservoir)
            > 0
        )
        assert observed
        assert all(
            image_shape == (240, 320, 3)
            and zbuffer_shape == (240, 320)
            and raster_width == 320
            and raster_height == 240
            for image_shape, zbuffer_shape, raster_width, raster_height in observed
        )

    def test_a_streaming_preview_is_byte_identical_on_a_second_run(
        self,
        tmp_path: Path,
    ) -> None:
        import numpy as np
        from PIL import Image

        path = tmp_path / "deterministic.stl"
        _binary_triangle_stl(path, count=12)
        first = render_stl_preview_isolated(
            path, width=160, height=120, limits=_limits()
        )
        second = render_stl_preview_isolated(
            path, width=160, height=120, limits=_limits()
        )

        assert first is not None and second is not None
        assert first.png == second.png
        pixels = np.asarray(Image.open(io.BytesIO(first.png)).convert("RGBA"))
        assert pixels[0, 0, 3] == 0
        assert int((pixels[:, :, 3] > 200).sum()) > 0

    def test_streaming_preview_preserves_true_annulus_hole(
        self, tmp_path: Path
    ) -> None:
        import numpy as np
        from PIL import Image

        path = tmp_path / "annulus.stl"
        _binary_annulus_stl(path)
        result = render_stl_preview_isolated(
            path, width=160, height=120, limits=_limits()
        )

        assert result is not None
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        alpha = pixels[:, :, 3]
        center = alpha[alpha.shape[0] // 2, alpha.shape[1] // 2]
        assert center < 32
        assert float((alpha > 200).mean()) > 0.10

    def test_translated_mesh_is_centered_in_preview(self, tmp_path: Path) -> None:
        import numpy as np
        from PIL import Image

        path = tmp_path / "translated.stl"
        _binary_triangle_stl(path, offset=(10_000.0, -20_000.0, 300.0))
        result = render_stl_preview_isolated(
            path, width=160, height=120, limits=_limits()
        )

        assert result is not None
        pixels = np.asarray(Image.open(io.BytesIO(result.png)).convert("RGBA"))
        ys, xs = np.where(pixels[:, :, 3] > 20)
        assert 0.25 < float(xs.mean() / pixels.shape[1]) < 0.75
        assert 0.25 < float(ys.mean() / pixels.shape[0]) < 0.75

    def test_forced_over_cap_ascii_uses_streaming_renderer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    @pytest.mark.parametrize(
        "contents",
        [
            b"solid broken\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n",
            b"solid broken\nfacet normal nan 0 1\nouter loop\n",
        ],
    )
    def test_malformed_ascii_never_produces_a_preview(
        self, tmp_path: Path, contents: bytes
    ) -> None:
        path = tmp_path / "broken.stl"
        path.write_bytes(contents)

        assert render_stl_preview_isolated(path, limits=_limits()) is None

    @pytest.mark.parametrize("case", ["truncated", "nonfinite"])
    def test_binary_streaming_rejects_truncated_or_nonfinite_source(
        self, tmp_path: Path, case: str
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

    def test_streaming_rejects_source_budget_before_starting_worker(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "too-large.stl"
        _binary_triangle_stl(path)
        limits = STLStreamingLimits(max_source_bytes=10)

        assert render_stl_preview_isolated(path, limits=limits) is None

    def test_streaming_rejects_candidate_budget_instead_of_publishing_partial_output(
        self,
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

        assert (
            render_stl_preview_isolated(path, width=96, height=72, limits=limits)
            is None
        )

    @pytest.mark.parametrize("case", ["timeout", "rss", "crash"])
    def test_worker_failure_is_killed_or_reaped_without_publishing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        self,
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
        self,
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


class TestTerminateProcessGroup:
    """Killing a worker means killing everything it started, not just the worker."""

    class _FakeProcess:
        def __init__(self, pid: int = 4242) -> None:
            self.pid = pid
            self.killed = False

        def kill(self) -> None:
            self.killed = True

    def test_kills_the_whole_process_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(stl_streaming.os, "getpgid", lambda _pid: 99)
        monkeypatch.setattr(
            stl_streaming.os, "killpg", lambda pgid, sig: killed.append((pgid, sig))
        )
        process = self._FakeProcess()

        stl_streaming._terminate_process_group(process)

        # A worker that spawned a child would otherwise leave it holding the
        # memory this whole design exists to bound.
        assert killed == [(99, signal.SIGKILL)]
        assert process.killed is False

    def test_falls_back_to_the_process_when_it_has_no_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_group(_pid: int) -> int:
            raise ProcessLookupError

        monkeypatch.setattr(stl_streaming.os, "getpgid", no_group)
        process = self._FakeProcess()

        stl_streaming._terminate_process_group(process)

        assert process.killed is True

    def test_falls_back_when_the_group_kill_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refused(_pgid: int, _sig: int) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(stl_streaming.os, "getpgid", lambda _pid: 99)
        monkeypatch.setattr(stl_streaming.os, "killpg", refused)
        process = self._FakeProcess()

        stl_streaming._terminate_process_group(process)

        assert process.killed is True

    def test_survives_a_process_that_is_already_gone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Gone(self._FakeProcess):
            def kill(self) -> None:
                raise ProcessLookupError

        monkeypatch.setattr(stl_streaming.os, "getpgid", lambda _pid: 99)
        monkeypatch.setattr(
            stl_streaming.os,
            "killpg",
            lambda _pgid, _sig: (_ for _ in ()).throw(ProcessLookupError),
        )

        # Reaping a worker that already exited must not raise into ingestion.
        stl_streaming._terminate_process_group(_Gone())


class TestReadRssBytes:
    """Peak memory is read from /proc where it exists, and simply unknown elsewhere."""

    def test_reports_the_resident_size(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status = tmp_path / "status"
        status.write_text("Name:\tworker\nVmRSS:\t   2048 kB\n")
        monkeypatch.setattr(stl_streaming, "Path", lambda _p: status)

        assert stl_streaming._read_rss_bytes(1) == 2048 * 1024

    def test_says_it_does_not_know_on_a_platform_without_proc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Missing:
            def read_text(self) -> str:
                raise OSError("no /proc here")

        monkeypatch.setattr(stl_streaming, "Path", lambda _p: _Missing())

        # macOS has no /proc; a missing measurement is not a failed render.
        assert stl_streaming._read_rss_bytes(1) is None

    def test_says_it_does_not_know_when_the_line_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status = tmp_path / "status"
        status.write_text("Name:\tworker\n")
        monkeypatch.setattr(stl_streaming, "Path", lambda _p: status)

        assert stl_streaming._read_rss_bytes(1) is None


class TestMeshProcessing:
    def test_over_cap_mesh_processing_uses_streaming_before_sampling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "over-cap.stl"
        _binary_triangle_stl(path)
        monkeypatch.setitem(_overlay, "mesh_max_render_triangles", 1)
        geometry, thumbnail = mesh_processing.analyze_mesh(path, width=96, height=72)

        assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
        assert thumbnail.complete is True
        assert geometry["triangle_count"] == 12


class TestLimits:
    def test_worker_rejects_invalid_expected_parent_pid_before_limits(
        self,
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


class TestRender:
    def test_normal_stl_render_exception_uses_streaming_before_sampling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("renderer crash")
            ),
        )

        _geometry, thumbnail = mesh_processing.analyze_mesh(path, width=96, height=72)

        assert isinstance(thumbnail, mesh_processing.FallbackThumbnail)
        assert thumbnail.complete is True


class TestDecode:
    @pytest.mark.parametrize(
        "case", ["invalid", "wrong_size", "manifest_only", "png_only"]
    )
    def test_decode_rejects_invalid_or_unpaired_preview(
        self, tmp_path: Path, case: str
    ) -> None:
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


class TestManifest:
    @pytest.mark.parametrize("case", ["missing", "partial", "oversized", "malformed"])
    def test_decode_rejects_missing_or_incomplete_manifest(
        self, tmp_path: Path, case: str
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

    @pytest.mark.parametrize("case", ["nonfinite_bounds", "over_budget_count"])
    def test_decode_rejects_forged_manifest_values(
        self, tmp_path: Path, case: str
    ) -> None:
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
