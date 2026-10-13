"""Prepared geometry transfers only after real disposable parser cleanup."""

import hashlib
import json
import os
import struct
import subprocess
import sys
import time

import pytest
import trimesh

from app.modules.media.mesh_contracts import ThumbnailRequest
from app.modules.media.thumbnail_packet import (
    PACKET_NAME,
    PacketDescriptor,
    read_packet,
)
from tests.fixtures.compute_renderer import qualified_renderer as qualified_renderer
from tests.paths import BACKEND_DIR


def packet_bytes():
    header = json.dumps(
        {
            "vertices": 3,
            "faces": 1,
            "normals": 3,
            "normal_mode": "gpu-visual-v2",
            "width": 640,
            "height": 480,
            "views": [None],
            "matte": False,
        }
    ).encode()
    return struct.pack("!I", len(header)) + header + bytes(48)


class TestPacketCustody:
    def test_reads_a_newly_written_file(self, tmp_path):
        payload = packet_bytes()
        path = tmp_path / PACKET_NAME
        path.write_bytes(payload)
        os.utime(path, ns=(1, time.time_ns()))
        owned = tmp_path.stat()
        result = read_packet(
            tmp_path,
            PacketDescriptor(len(payload), hashlib.sha256(payload).hexdigest(), 1),
            640,
            480,
            directory_identity=(owned.st_dev, owned.st_ino),
        )
        assert result == payload

    @pytest.mark.parametrize("change", ["symlink", "hardlink", "digest", "directory"])
    def test_refuses_changed_custody(self, tmp_path, change):
        payload = packet_bytes()
        directory = tmp_path / "owned"
        directory.mkdir()
        path = directory / PACKET_NAME
        path.write_bytes(payload)
        original = directory.stat()
        descriptor = PacketDescriptor(
            len(payload), hashlib.sha256(payload).hexdigest(), 1
        )
        if change == "symlink":
            target = tmp_path / "other"
            path.rename(target)
            path.symlink_to(target)
        elif change == "hardlink":
            os.link(path, tmp_path / "other")
        elif change == "digest":
            path.write_bytes(payload[:-1] + b"x")
        else:
            directory.rename(tmp_path / "moved")
            directory.mkdir()
            (directory / PACKET_NAME).write_bytes(payload)
        with pytest.raises((ValueError, OSError)):
            read_packet(
                directory,
                descriptor,
                640,
                480,
                directory_identity=(original.st_dev, original.st_ino),
            )


class TestPreparedExecution:
    def test_parser_prepares_packet_without_compute_transport(self, tmp_path):
        source = tmp_path / "source.stl"
        source.write_bytes(trimesh.creation.box().export(file_type="stl"))
        output = tmp_path / "packet"
        output.mkdir()
        script = r"""
import importlib.abc, sys, time
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.runtime.compute.client', 'app.runtime.compute.protocol', 'app.runtime.compute.contracts'}:
            raise AssertionError('unexpected compute transport import: ' + fullname)
sys.meta_path.insert(0, Block())
from app.runtime.native_admission import LocalResourcePool
from app.runtime.native_runtime import bind_pool
bind_pool(LocalResourcePool(Path(sys.argv[3])))
from app.modules.media.mesh_worker import ThumbnailEngine
from app.modules.media.mesh_contracts import PreparedThumbnailResult, ThumbnailRequest
from app.modules.media.thumbnail_packet import read_packet, validate_payload
result = ThumbnailEngine().prepare(
    ThumbnailRequest(Path(sys.argv[1]), include_fingerprint=False, output_format='WEBP'),
    Path(sys.argv[2]), deadline=time.monotonic() + 30,
)
assert isinstance(result, PreparedThumbnailResult)
assert result.metadata.geometry['triangle_count'] == 12
assert result.metadata.geometry['volume_mm3'] == 1
output = Path(sys.argv[2])
stat = output.stat()
payload = read_packet(output, result.packet, 640, 480, directory_identity=(stat.st_dev, stat.st_ino))
assert validate_payload(payload, 640, 480) == 12
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(source),
                str(output),
                str(tmp_path / "pool"),
            ],
            capture_output=True,
            text=True,
            timeout=40,
            cwd=BACKEND_DIR,
        )
        assert result.returncode == 0, result.stderr

    def test_codec_imports_no_numeric_runtime(self):
        script = r"""
import importlib.abc,sys,time,json,struct
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'numpy','scipy','trimesh','wgpu','cascadio','onnxruntime'}:
            raise AssertionError('unexpected native import:'+fullname)
sys.meta_path.insert(0,Block())
from app.modules.media.thumbnail_pixels import encode_pixels
from app.modules.media.compute_render import render_packet
from app.runtime.compute import client
from printstash_core.mesh.rasterizer import RenderedPixels
pixels=bytearray(320*240*4)
pixels[(100*320+100)*4:(100*320+100)*4+4]=bytes((10,20,30,255))
header=json.dumps(dict(vertices=3,faces=1,normals=3,normal_mode='gpu-visual-v2',width=320,height=240,views=[None],matte=False)).encode()
packet=struct.pack('!I',len(header))+header+bytes(48)
client.available=lambda **kwargs: True
client.exchange_render=lambda *args,**kwargs: b'\x01'+struct.pack('!III',320,240,1)+bytes(pixels)
rendered=render_packet(packet,width=320,height=240,deadline=time.monotonic()+10)
assert rendered is not None
assert encode_pixels(rendered,deadline=time.monotonic()+10).startswith(b'RIFF')
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=BACKEND_DIR,
        )
        assert result.returncode == 0, result.stderr


class TestPreparedEligibility:
    def test_emits_geometry_when_renderer_readiness_changes(
        self, tmp_path, monkeypatch
    ):
        from app.core.config import _overlay
        from app.modules.media.mesh_contracts import PreparedThumbnailResult
        from app.modules.media.mesh_protocol import PreparedThumbnailOutput
        from app.modules.media.thumbnail_engine import ThumbnailEngine
        from app.runtime.compute import client

        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        source = tmp_path / "mesh.stl"
        source.write_bytes(trimesh.creation.box().export(file_type="stl"))
        output = tmp_path / "packet"
        output.mkdir()
        frames = []
        # GPU eligibility can change after the supervisor chooses the handoff.
        # Its eventual dispatch owns the refusal and guarded packet recovery.
        monkeypatch.setattr(client, "render_ready", lambda **kwargs: False)

        result = ThumbnailEngine().prepare(
            ThumbnailRequest(source, include_fingerprint=False, output_format="WEBP"),
            output,
            deadline=time.monotonic() + 30,
            on_output=frames.append,
        )

        assert isinstance(result, PreparedThumbnailResult)
        assert result.metadata.geometry["triangle_count"] == 12
        assert result.metadata.geometry["volume_mm3"] == pytest.approx(1)
        packet = next(
            frame.packet
            for frame in frames
            if isinstance(frame, PreparedThumbnailOutput)
        )
        owned = output.stat()
        assert read_packet(
            output, packet, 640, 480, directory_identity=(owned.st_dev, owned.st_ino)
        )

    @pytest.mark.parametrize("cancelled", [False, True], ids=["deadline", "cancelled"])
    def test_propagates_withdrawal_before_packet_preparation(
        self, tmp_path, monkeypatch, cancelled
    ):
        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.core.config import _overlay
        from app.modules.media.thumbnail_engine import ThumbnailEngine

        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        source = tmp_path / "mesh.stl"
        source.write_bytes(trimesh.creation.box().export(file_type="stl"))
        output = tmp_path / "packet"
        output.mkdir()
        error = OperationCancelled if cancelled else TimeoutError
        deadline = time.monotonic() + 30 if cancelled else time.monotonic() - 1

        with cancellation_scope(lambda: cancelled), pytest.raises(error):
            ThumbnailEngine().prepare(
                ThumbnailRequest(
                    source, include_fingerprint=False, output_format="WEBP"
                ),
                output,
                deadline=deadline,
            )
        assert not (output / PACKET_NAME).exists()


class TestCodecProgress:
    def test_encodes_while_a_parser_owns_the_native_pool(self, tmp_path, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor
        from io import BytesIO

        from PIL import Image
        from printstash_core.mesh.rasterizer import RenderedPixels

        from app.modules.media import compute_render, native_process, thumbnail_handoff
        from app.runtime import native_runtime, preparation_runtime
        from app.runtime.native_admission import LocalResourcePool, Resources

        payload = packet_bytes()
        directory = tmp_path / "packet"
        directory.mkdir()
        (directory / PACKET_NAME).write_bytes(payload)
        owned = directory.stat()
        packet = PacketDescriptor(len(payload), hashlib.sha256(payload).hexdigest(), 1)
        frame = Image.new("RGBA", (640, 480))
        frame.paste((70, 100, 130, 255), (100, 100, 400, 300))
        pixels = RenderedPixels(640, 480, frame.tobytes())
        monkeypatch.setattr(compute_render, "render_packet", lambda *a, **k: pixels)
        pool = LocalResourcePool(tmp_path / "native")
        capacity = native_process.native_capacity()
        previous = native_runtime.bind_pool(pool)
        amount = Resources(1, thumbnail_handoff.resident_bytes(640, 480))
        plan = thumbnail_handoff.HandoffPlan(640, 480, amount, amount)

        def encode():
            with preparation_runtime.reserve(amount, amount, checkpoint=lambda: None):
                with thumbnail_handoff.reserve(plan, directory) as owner:
                    return owner.complete(
                        packet,
                        directory,
                        (owned.st_dev, owned.st_ino),
                        fallback=lambda: pytest.fail("GPU success fell back"),
                    )

        try:
            with ThreadPoolExecutor(1) as executor:
                with pool.reserve(capacity, capacity, checkpoint=lambda: None):
                    encoded = executor.submit(encode).result(timeout=2)
            with Image.open(BytesIO(encoded)) as image:
                assert image.size == (640, 480)
                assert image.getpixel((200, 200))[3] == 255
        finally:
            native_runtime.bind_pool(previous)
