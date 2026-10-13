"""Real broker status and guarded parser transfer through bounded local IPC."""

import contextvars
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import trimesh
from PIL import Image
from printstash_core.mesh.rasterizer import RenderedPixels

from app.bootstrap.native_resources import configure
from app.modules.media import (
    compute_render,
    mesh_isolation,
    native_process,
    thumbnail_handoff,
)
from app.modules.media.mesh_contracts import PreparedThumbnailResult, ThumbnailRequest
from app.modules.media.thumbnail_engine import ThumbnailEngine
from app.runtime import native_runtime, preparation_runtime
from app.runtime.native_admission import Resources
from tests.fixtures.compute_renderer import qualified_renderer as qualified_renderer


class TestPreparedExecution:
    def test_prepares_geometry_while_gpu_is_busy(self, tmp_path, qualified_renderer):
        source = tmp_path / "source.stl"
        source.write_bytes(trimesh.creation.box().export(file_type="stl"))
        output = tmp_path / "packet"
        output.mkdir()
        with ThreadPoolExecutor(2) as executor:
            busy = executor.submit(
                contextvars.copy_context().run, qualified_renderer.occupy
            )
            try:
                assert qualified_renderer.owner_busy.wait(5)
                preparing = executor.submit(
                    contextvars.copy_context().run,
                    ThumbnailEngine().prepare,
                    ThumbnailRequest(
                        source, include_fingerprint=False, output_format="WEBP"
                    ),
                    output,
                    deadline=time.monotonic() + 30,
                )
                result = preparing.result(timeout=3)
                assert isinstance(result, PreparedThumbnailResult)
                assert result.metadata.geometry["triangle_count"] == 12
                assert result.metadata.geometry["volume_mm3"] == pytest.approx(1)
                assert not busy.done()
            finally:
                qualified_renderer.release_owner.set()
                qualified_renderer.release_batch.set()
                busy.result(timeout=10)

    @pytest.mark.parametrize(
        "refused", [False, True], ids=["gpu-success", "cpu-recovery"]
    )
    def test_transfers_custody_after_clean_reap(
        self, tmp_path, monkeypatch, refused, qualified_renderer, caplog
    ):
        caplog.set_level("INFO", logger="app.modules.media.thumbnail_handoff")
        caplog.set_level("INFO", logger="app.modules.media.mesh_isolation")
        configure(tmp_path)
        source = tmp_path / "source.stl"
        source.write_bytes(
            trimesh.creation.box(extents=(10, 20, 30)).export(file_type="stl")
        )
        executions = []
        real = mesh_isolation.supervise_result

        def supervise(command, **kwargs):
            reply = real(command, **kwargs)
            executions.append((command, reply))
            return reply

        monkeypatch.setattr(mesh_isolation, "supervise_result", supervise)

        def render(payload, **kwargs):
            assert len(executions) == 1
            assert native_runtime.current_permit() is None
            assert preparation_runtime.current_host_permit() is not None
            capacity = native_process.native_capacity()
            with native_runtime.admit(capacity, capacity, checkpoint=lambda: None):
                pass
            if refused:
                source.unlink()  # CPU recovery must consume the owned packet.
                return None
            rgba = bytearray(640 * 480 * 4)
            rgba[(240 * 640 + 320) * 4 : (240 * 640 + 320) * 4 + 4] = bytes(
                (100, 150, 200, 255)
            )
            return RenderedPixels(640, 480, bytes(rgba))

        monkeypatch.setattr(compute_render, "render_packet", render)
        capacity = Resources(4, 384 * 1024**2)
        plan = thumbnail_handoff.HandoffPlan(
            640, 480, Resources(1, thumbnail_handoff.resident_bytes(640, 480)), capacity
        )
        with preparation_runtime.reserve(
            Resources(1, 100 * 1024**2), Resources(4, 1024**3), checkpoint=lambda: None
        ) as permit:
            workspace = preparation_runtime.workspace(permit)
            with thumbnail_handoff.reserve(plan, workspace) as handoff:
                result = mesh_isolation.generate(
                    ThumbnailRequest(
                        source, include_fingerprint=False, output_format="WEBP"
                    ),
                    handoff=handoff,
                )
        messages = [record.getMessage() for record in caplog.records]
        completion = next(
            json.loads(message.split(" ", 1)[1])
            for message in messages
            if message.startswith("thumbnail_handoff_complete ")
        )
        supervision = next(
            json.loads(message.split(" ", 1)[1])
            for message in messages
            if message.startswith("thumbnail_handoff_supervision ")
        )
        assert completion["handoff_id"] == supervision["handoff_id"]
        assert completion["cpu_fallback"] is refused
        assert completion["codec_seconds"] >= 0
        assert completion["dispatch_seconds"] >= 0
        assert supervision["parser_execution_id"] == result.supervision.execution_id
        assert supervision["fallback_execution_id"] == (
            result.fallback_supervision.execution_id if refused else None
        )
        assert len(executions) == (2 if refused else 1)
        assert "app.modules.media.mesh_worker" in executions[0][0]
        if refused:
            assert "app.modules.media.thumbnail_cpu_worker" in executions[1][0]
        assert (result.fallback_supervision is not None) is refused
        if refused:
            assert result.fallback_supervision is not None
            assert result.supervision is not None
            assert (
                result.fallback_supervision.execution_id
                != result.supervision.execution_id
            )
            assert (
                result.peak_rss_bytes >= result.fallback_supervision.peak_tree_rss_bytes
            )
        assert result.geometry["volume_mm3"] == pytest.approx(6000)
        assert result.image is not None
        with Image.open(io.BytesIO(result.image)) as image:
            assert image.size == (640, 480)
            assert image.getbbox() is not None
