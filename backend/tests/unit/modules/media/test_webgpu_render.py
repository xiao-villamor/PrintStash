"""Returned RGBA packets own immutable pixels beyond native readback reuse.

Only the native API supplies scripted pixels here; rendering, response framing,
normal-status handling and the output cache remain real. Physical diagnostics
separately execute the raster and postprocessing shaders.
"""

import struct
import sys

import numpy as np
import pytest
from printstash_core.mesh import rasterizer as core
from printstash_core.mesh.render_geometry import PreparedRender, PreparedRenderGeometry

from app.modules.media.gpu_compact import CompactGeometry
from app.modules.media.gpu_normals import VisualRender
from app.modules.media.gpu_postprocess import Postprocessor
from app.modules.media.gpu_visual_normals import VisualNormalPipeline
from app.modules.media.webgpu_render import Renderer
from tests.fakes.gpu_device import API, Buffer, Device


@pytest.fixture
def renderer(monkeypatch):
    monkeypatch.setitem(sys.modules, "wgpu", API)
    device = Device()
    owner = Renderer(device)
    # This native API double supplies completed readback bytes; it cannot
    # qualify shader math. Those canaries run in the physical diagnostic suite.
    owner.postprocessor = Postprocessor(device)
    owner.postprocess_attempted = True
    try:
        yield owner
    finally:
        owner.close()


@pytest.fixture
def prepared():
    faces = np.asarray([[0, 1, 2]], dtype=np.int64)
    return PreparedRender(
        np.asarray([[0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=np.float32),
        1,
        lambda size: iter([faces]),
        np.arange(3, dtype=np.int64),
        np.tile([0.0, 0.0, 1.0], (3, 1)),
    )


class TestExecutePreparedMany:
    @pytest.mark.parametrize(
        ("width", "finalized", "result_width", "result_height", "pitch"),
        [
            pytest.param(32, False, 64, 8, 256, id="contiguous-raw"),
            pytest.param(24, False, 48, 8, 256, id="padded-raw"),
            pytest.param(24, True, 24, 4, 96, id="gpu-finalized"),
        ],
    )
    def test_preserves_frame_association(
        self,
        renderer,
        prepared,
        monkeypatch,
        width,
        finalized,
        result_width,
        result_height,
        pitch,
    ):
        colors = [
            bytes((10, 20, 30, 40)),
            bytes((50, 60, 70, 80)),
            bytes((90, 100, 110, 120)),
        ]
        frames = [color * result_width * result_height for color in colors]
        padding = b"\xee" * (pitch - result_width * 4)
        mapped = b"".join(
            (color * result_width + padding) * result_height for color in colors
        )
        monkeypatch.setattr(
            Buffer,
            "read_mapped",
            lambda self, *, size=None, copy=True: memoryview(mapped)[:size],
        )
        decoded = [
            (prepared, width, 4, [None, np.eye(3)], False),
            (prepared, width, 4, [None], False),
        ]

        outputs = renderer.execute_prepared_many(
            decoded,
            ["geometry:a", "geometry:b"],
            ["a", "b"],
            raw=True,
            gpu_finalize=finalized,
        )

        assert outputs == [
            struct.pack("!III", result_width, result_height, 2) + frames[0] + frames[1],
            struct.pack("!III", result_width, result_height, 1) + frames[2],
        ]
        assert all(type(output) is bytes for output in outputs)
        assert renderer.output_bytes == sum(map(len, outputs))
        assert renderer.readback_batches == 1

    def test_owns_cached_pixels_after_unmap(self, renderer, prepared, monkeypatch):
        rgba = bytes((20, 40, 60, 80)) * 24 * 4
        mapped = bytearray(rgba)
        original_unmap = Buffer.unmap

        def unmap(buffer):
            original_unmap(buffer)
            if buffer is renderer.readback:
                mapped[:] = b"\xee" * len(mapped)

        monkeypatch.setattr(
            Buffer,
            "read_mapped",
            lambda self, *, size=None, copy=True: memoryview(mapped)[:size],
        )
        monkeypatch.setattr(Buffer, "unmap", unmap)
        args = (
            [(prepared, 24, 4, [None], False)],
            ["geometry:owned"],
            ["owned"],
        )

        first = renderer.execute_prepared_many(*args, raw=True, gpu_finalize=True)
        cached = renderer.execute_prepared_many(*args, raw=True, gpu_finalize=True)

        assert mapped == bytearray(b"\xee" * len(rgba))
        assert first == cached == [struct.pack("!III", 24, 4, 1) + rgba]
        assert type(cached[0]) is bytes
        assert renderer.output_bytes == len(cached[0])
        assert renderer.output_hits == 1
        assert renderer.submissions == renderer.readback_batches == 1

    def test_excludes_normal_status_from_returned_pixels(
        self, renderer, prepared, monkeypatch
    ):
        visual = VisualRender(
            PreparedRenderGeometry(prepared.vertices, 1, prepared.face_chunks)
        )
        renderer.packer = CompactGeometry(
            renderer.device, renderer.memory, renderer.views
        )
        renderer.packer.geometry = renderer.geometry
        renderer.packer.visual_normals = VisualNormalPipeline(renderer.device)
        frames = [
            bytes((10, 20, 30, 40)) * 24 * 4,
            bytes((50, 60, 70, 80)) * 24 * 4,
            bytes((90, 100, 110, 120)) * 24 * 4,
        ]
        mapped = b"".join(frames) + struct.pack("<II", 5, 7)
        monkeypatch.setattr(
            Buffer,
            "read_mapped",
            lambda self, *, size=None, copy=True: memoryview(mapped)[:size],
        )

        outputs = renderer.execute_prepared_many(
            [
                (visual, 24, 4, [None, np.eye(3)], False),
                (visual, 24, 4, [None], False),
            ],
            ["geometry:a", "geometry:b"],
            ["a", "b"],
            raw=True,
            gpu_finalize=True,
        )

        assert outputs == [
            struct.pack("!III", 24, 4, 2) + frames[0] + frames[1],
            struct.pack("!III", 24, 4, 1) + frames[2],
        ]
        assert renderer.geometry_local_normal_repairs == 12
        assert renderer.geometry_cpu_normal_fallbacks == 0
        assert renderer.output_bytes == sum(map(len, outputs))
        assert renderer.submissions == renderer.readback_batches == 1

    def test_preserves_cpu_postprocessing_of_padded_readback(
        self, renderer, prepared, monkeypatch
    ):
        color = bytes((80, 100, 120, 160))
        rgba = color * 48 * 8
        mapped = (color * 48 + b"\xee" * 64) * 8
        monkeypatch.setattr(
            Buffer,
            "read_mapped",
            lambda self, *, size=None, copy=True: memoryview(mapped)[:size],
        )
        expected = core.postprocess_rgba(rgba, 24, 4).rgba

        outputs = renderer.execute_prepared_many(
            [(prepared, 24, 4, [None], False)],
            ["geometry:postprocess"],
            ["postprocess"],
        )

        assert outputs == [struct.pack("!III", 24, 4, 1) + expected]
        assert type(outputs[0]) is bytes


class TestWireRendering:
    def test_executes_an_encoded_singleton(self, renderer, prepared):
        from app.modules.media.compute_geometry import encode

        payload = encode(prepared, 24, 4, [None], False)
        result = renderer.execute(payload)
        assert struct.unpack("!III", result[:12]) == (24, 4, 1)
        assert len(result) == 12 + 24 * 4 * 4
        assert renderer.submissions == 1

    def test_refuses_mixed_frame_dimensions(self, renderer, prepared):
        from app.modules.media.compute_geometry import encode

        with pytest.raises(ValueError, match="compute_batch_dimensions"):
            renderer.execute_many(
                [
                    encode(prepared, 24, 4, [None], False),
                    encode(prepared, 32, 4, [None], False),
                ]
            )
        assert renderer.submissions == 0

    def test_recovers_only_geometry_refused_by_device_normals(
        self, renderer, prepared, monkeypatch
    ):
        from app.modules.media.gpu_normals import defer

        deferred = defer(
            PreparedRenderGeometry(prepared.vertices, 1, prepared.face_chunks)
        )
        reads = []
        original = Buffer.read_mapped

        def read(buffer, *, size=None, copy=True):
            if buffer is not renderer.readback:
                return original(buffer, size=size, copy=copy)
            reads.append(size)
            result = bytearray(size)
            if len(reads) == 1:
                struct.pack_into("<I", result, size - 4, 1)
            return memoryview(result)

        monkeypatch.setattr(Buffer, "read_mapped", read)
        output = renderer.execute_prepared_many(
            [(deferred, 32, 4, [None], False)],
            ["geometry:recover"],
            ["recover"],
            raw=True,
        )
        assert struct.unpack("!III", output[0][:12]) == (64, 8, 1)
        assert renderer.geometry_cpu_normal_fallbacks == 1
        assert renderer.readback_batches == 2
        assert not renderer.unverified_normals

    def test_keeps_rasterization_after_postprocess_canary_failure(
        self, renderer, prepared, monkeypatch
    ):
        from unittest.mock import Mock

        renderer.postprocessor.close()
        renderer.postprocessor = None
        renderer.postprocess_attempted = False
        monkeypatch.setattr(
            Postprocessor, "canary", Mock(side_effect=ValueError("native mismatch"))
        )
        output = renderer.execute_prepared_many(
            [(prepared, 24, 4, [None], False)],
            ["geometry:cpu-final"],
            ["final"],
            raw=True,
            gpu_finalize=True,
        )
        assert struct.unpack("!III", output[0][:12]) == (48, 8, 1)
        assert renderer.postprocessor is None
        assert renderer.postprocess_attempted
        assert renderer.readback_batches == 1
