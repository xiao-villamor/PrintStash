"""Native remote reads release Python threads without changing byte contracts."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time

import pytest

from app.modules.storage.remote_deadline import remote_budget
from app.modules.storage.remote_io_adapters import OpenDALRemoteIO
from app.modules.storage.storage_backend.contracts import (
    StorageConfigurationError,
    StorageObjectInfo,
)
from app.modules.storage.storage_providers import TransportKind, TransportSpec
from tests.fakes.slow_s3 import PAYLOAD
from tests.paths import BACKEND_DIR


@pytest.fixture
def remote():
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "tests.fakes.slow_s3"],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        port = int(process.stdout.readline())
        yield OpenDALRemoteIO(
            TransportSpec(
                kind=TransportKind.S3,
                provider="s3",
                namespace="source",
                options={
                    "bucket": "test",
                    "root": "",
                    "region": "us-east-1",
                    "endpoint_url": f"http://127.0.0.1:{port}",
                    "access_key": "contract-access",
                    "secret_key": "contract-secret",
                    "addressing_style": "path",
                },
            )
        )
    finally:
        process.terminate()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()


@pytest.mark.parametrize(
    "operation", ["metadata", "listing", "stream", "bytes", "size", "exists", "check"]
)
def test_keeps_python_threads_responsive(remote, operation):
    stop = threading.Event()
    ready = threading.Event()
    ticks = []

    def pulse():
        ready.set()
        while not stop.wait(0.005):
            ticks.append(time.monotonic())

    thread = threading.Thread(target=pulse)
    thread.start()
    assert ready.wait(2)
    started = time.monotonic()
    try:
        if operation == "metadata":
            assert remote.object_info("source/slow/object").size == len(PAYLOAD)
        elif operation == "listing":
            with remote.iter_directory("slow") as entries:
                assert [entry.key for entry in entries] == ["slow/object"]
        elif operation == "stream":
            assert b"".join(remote.stream_chunks("source/slow/object")) == PAYLOAD
        elif operation == "bytes":
            assert remote.read_bytes("source/slow/object") == PAYLOAD
        elif operation == "size":
            assert remote.stat_size("source/slow/object") == len(PAYLOAD)
        elif operation == "check":
            assert remote.check() is None
        else:
            assert remote.exists("source/slow/object")
        ended = time.monotonic()
    finally:
        stop.set()
        thread.join(timeout=2)
    assert sum(started < tick < ended for tick in ticks) >= 10


def test_streams_exact_content(remote):
    chunks = list(remote.stream_chunks("source/object", chunk_size=997))
    assert b"".join(chunks) == PAYLOAD
    assert all(0 < len(chunk) <= 997 for chunk in chunks)


def test_rejects_changed_content(remote):
    expected = StorageObjectInfo(size=len(PAYLOAD), etag='"replaced"')
    with pytest.raises(StorageConfigurationError, match="remote_storage_read_failed"):
        with remote.open_reader("source/object", expected=expected) as reader:
            reader.read(1)


def test_preserves_absence(remote):
    assert remote.object_info("source/missing") is None
    with pytest.raises(StorageConfigurationError, match="remote_storage_read_failed"):
        with remote.open_reader("source/missing") as reader:
            reader.read(1)


@pytest.mark.parametrize("operation", ["metadata", "listing", "stream"])
def test_interrupts_native_wait_at_deadline(remote, operation):
    started = time.monotonic()
    with remote_budget(deadline=started + 0.1):
        with pytest.raises(
            StorageConfigurationError, match="remote_scan_slice_deadline"
        ):
            if operation == "metadata":
                remote.object_info("source/very-slow/object")
            elif operation == "listing":
                with remote.iter_directory("very-slow") as entries:
                    list(entries)
            else:
                with remote.open_reader("source/very-slow/object") as reader:
                    reader.read(1)
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize("operation", ["metadata", "listing", "stream"])
def test_cancels_native_wait(remote, operation):
    cancelled = threading.Event()
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with remote_budget(cancelled=cancelled):
            with pytest.raises(asyncio.CancelledError):
                if operation == "metadata":
                    remote.object_info("source/very-slow/object")
                elif operation == "listing":
                    with remote.iter_directory("very-slow") as entries:
                        list(entries)
                else:
                    with remote.open_reader("source/very-slow/object") as reader:
                        reader.read(1)
        assert time.monotonic() - started < 1.0
        assert remote.read_bytes("source/object") == PAYLOAD
    finally:
        timer.cancel()
        timer.join(timeout=2)


def test_closes_partial_stream(remote):
    with remote.open_reader("source/object") as reader:
        assert reader.read(3) == PAYLOAD[:3]
    assert reader.closed
    with pytest.raises(ValueError):
        reader.read(3)


def test_slow_read_does_not_delay_another_request(remote):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as executor:
        slow = executor.submit(remote.read_bytes, "source/very-slow/object")
        time.sleep(0.1)
        started = time.monotonic()
        assert remote.read_bytes("source/object") == PAYLOAD
        assert time.monotonic() - started < 1.0
        assert slow.result(timeout=2) == PAYLOAD
