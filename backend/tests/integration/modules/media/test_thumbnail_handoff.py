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
    def test_keeps_unqualified_geometry_in_the_parser(self, tmp_path, monkeypatch):
        from app.core.config import _overlay
        from app.modules.media.mesh_contracts import ThumbnailResult
        from app.modules.media.mesh_protocol import PreparedThumbnailOutput
        from app.modules.media.thumbnail_engine import ThumbnailEngine
        from app.runtime.compute import client

        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        source = tmp_path / "mesh.stl"
        source.write_bytes(trimesh.creation.box().export(file_type="stl"))
        output = tmp_path / "packet"
        output.mkdir()
        deadline = time.monotonic() + 30
        checks, frames = [], []

        def refuse(**kwargs):
            checks.append(kwargs)
            return False

        monkeypatch.setattr(client, "render_ready", refuse)
        result = ThumbnailEngine().prepare(
            ThumbnailRequest(source, include_fingerprint=False, output_format="WEBP"),
            output,
            deadline=deadline,
            on_output=frames.append,
        )
        assert isinstance(result, ThumbnailResult)
        assert result.image is not None
        assert checks[0]["units"] == 12
        assert checks[0]["deadline"] == deadline
        assert not (output / PACKET_NAME).exists()
        assert not any(isinstance(frame, PreparedThumbnailOutput) for frame in frames)

    @pytest.mark.parametrize("cancelled", [False, True], ids=["deadline", "cancelled"])
    def test_propagates_withdrawal_during_readiness(
        self, tmp_path, monkeypatch, cancelled
    ):
        from app.core.cancellation import OperationCancelled
        from app.core.config import _overlay
        from app.modules.media import mesh_render
        from app.modules.media.thumbnail_engine import ThumbnailEngine
        from app.runtime.compute import client

        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        source = tmp_path / "mesh.stl"
        source.write_bytes(trimesh.creation.box().export(file_type="stl"))
        output = tmp_path / "packet"
        output.mkdir()
        error = OperationCancelled if cancelled else TimeoutError

        def withdraw(**kwargs):
            raise error()

        monkeypatch.setattr(client, "render_ready", withdraw)
        monkeypatch.setattr(
            mesh_render,
            "render_mesh_thumbnail",
            lambda *a, **k: pytest.fail("withdrawn packet rendered"),
        )
        with pytest.raises(error):
            ThumbnailEngine().prepare(
                ThumbnailRequest(
                    source, include_fingerprint=False, output_format="WEBP"
                ),
                output,
                deadline=time.monotonic() + 30,
            )
        assert not (output / PACKET_NAME).exists()
