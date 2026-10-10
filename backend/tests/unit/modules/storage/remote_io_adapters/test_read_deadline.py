"""Native transport failures retain slice deadlines and cancellation identity."""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest

from app.modules.storage import remote_deadline
from app.modules.storage.remote_io_adapters import OpenDALRemoteIO, _ChunkReader
from app.modules.storage.storage_backend.contracts import StorageConfigurationError
from app.modules.storage.storage_providers import TransportKind, TransportSpec


@pytest.fixture(params=["exists", "stat", "open", "read", "chunk", "close"], ids=str)
def operation(request, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(
        remote_deadline, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    kind = request.param
    fault = {"error": TimeoutError("native transport timeout"), "advance": False}

    def fail():
        if fault["advance"]:
            clock[0] = 11.0
        raise fault["error"]

    class Reader(BytesIO):
        def read(self, size=-1):
            fail()

        def read1(self, size=-1):
            fail()

    class Operator:
        def exists(self, key):
            if kind == "exists":
                fail()
            return True

        def stat(self, key):
            fail()

        def open(self, key, mode, **kwargs):
            if kind == "open":
                fail()
            return Reader(b"bytes")

    class Chunks:
        closed = False

        def __next__(self):
            fail()

        def close(self):
            if not self.closed:
                self.closed = True
                if kind == "close":
                    fail()

    backend = OpenDALRemoteIO(
        TransportSpec(
            kind=TransportKind.S3, provider="s3", namespace="bucket/library", options={}
        ),
        operator=Operator(),
    )

    def invoke():
        with remote_deadline.remote_budget(deadline=10):
            if kind in {"exists", "stat"}:
                backend.object_info("bucket/library/model.stl")
            elif kind in {"open", "read"}:
                with backend.open_reader("bucket/library/model.stl") as stream:
                    stream.read(1)
            else:
                stream = _ChunkReader(Chunks())
                if kind == "close":
                    stream.close()
                else:
                    try:
                        stream.read(1)
                    finally:
                        stream.close()

    return invoke, fault, kind


class TestReadDeadline:
    def test_reports_expired_slice_after_native_failure(self, operation):
        invoke, fault, _kind = operation
        fault["advance"] = True
        with pytest.raises(
            StorageConfigurationError, match="^remote_scan_slice_deadline$"
        ):
            invoke()

    def test_preserves_explicit_deadline(self, operation):
        invoke, fault, _kind = operation
        fault["error"] = StorageConfigurationError("remote_scan_slice_deadline")
        with pytest.raises(
            StorageConfigurationError, match="^remote_scan_slice_deadline$"
        ):
            invoke()

    def test_keeps_transport_failure_before_deadline(self, operation):
        invoke, _fault, kind = operation
        reason = (
            "remote_storage_metadata_failed"
            if kind in {"exists", "stat"}
            else "remote_storage_read_failed"
        )
        with pytest.raises(StorageConfigurationError, match="^" + reason + "$"):
            invoke()

    def test_preserves_cancellation(self, operation):
        invoke, fault, _kind = operation
        fault["error"] = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            invoke()
