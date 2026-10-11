"""Opt-in tracing for isolated compute benchmarks, never enabled by the app.

Spans overlap. Mapping includes a GPU completion fence, not just transfer time.
Records contain stage names, resource amounts and clocks, never request bodies.
"""

from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_local = threading.local()
_directory: Path | None = None


def emit(stage, started, cpu_started, **values):
    if _directory is None:
        return
    record = dict(
        stage=stage,
        pid=os.getpid(),
        tid=threading.get_native_id(),
        start_ns=started,
        duration_ns=time.monotonic_ns() - started,
        thread_cpu_ns=time.thread_time_ns() - cpu_started,
        **values,
    )
    data = (json.dumps(record, separators=(",", ":")) + "\n").encode()
    fd = os.open(
        _directory / f"trace-{os.getpid()}.jsonl",
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o600,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def timed(owner, name, stage):
    original = getattr(owner, name)

    @functools.wraps(original)
    def call(*args, **kwargs):
        start, cpu = time.monotonic_ns(), time.thread_time_ns()
        outcome = "error"
        try:
            result = original(*args, **kwargs)
            outcome = "returned"
            return result
        finally:
            emit(stage, start, cpu, outcome=outcome)

    setattr(owner, name, call)


class SocketProbe:
    """Measure socket calls independently of cancellation and polling overhead."""

    def __init__(self, connection):
        self.connection = connection
        self.bytes = self.calls = self.timeouts = self.call_ns = 0

    def transfer(self, method, *args):
        start = time.monotonic_ns()
        self.calls += 1
        try:
            result = getattr(self.connection, method)(*args)
        except socket.timeout:
            self.timeouts += 1
            raise
        else:
            self.bytes += len(result) if isinstance(result, bytes) else result
            return result
        finally:
            self.call_ns += time.monotonic_ns() - start

    def recv(self, *args):
        return self.transfer("recv", *args)

    def send(self, *args):
        return self.transfer("send", *args)


def instrument_transport(module, name):
    original = getattr(module, name)

    @functools.wraps(original)
    def transfer(connection, *args, **kwargs):
        start, cpu = time.monotonic_ns(), time.thread_time_ns()
        probe = SocketProbe(connection)
        try:
            return original(probe, *args, **kwargs)
        finally:
            emit(
                name,
                start,
                cpu,
                bytes=probe.bytes,
                calls=probe.calls,
                socket_ns=probe.call_ns,
                timeouts=probe.timeouts,
            )

    setattr(module, name, transfer)


def instrument_pool(module):
    cls = module.LocalResourcePool
    coordinator, reserve, available = cls._coordinator, cls.reserve, cls._available

    @contextmanager
    def lock(self, checkpoint):
        start = time.monotonic_ns()
        with coordinator(self, checkpoint):
            acquired = time.monotonic_ns()
            try:
                yield
            finally:
                stats = getattr(_local, "pool_stats", None)
                if stats is not None:
                    stats["lock_wait_ns"] += acquired - start
                    stats["lock_hold_ns"] += time.monotonic_ns() - acquired
                    stats["lock_acquisitions"] += 1

    def can_admit(ticket, live):
        result = available(ticket, live)
        stats = getattr(_local, "pool_stats", None)
        if stats is not None:
            active = [item for item in live if item.state.value == "active"]
            slots = sum(item.request.slots for item in active)
            memory = sum(item.request.bytes for item in active)
            stats["polls"] += 1
            stats["active_slots_peak"] = max(stats["active_slots_peak"], slots)
            stats["active_bytes_peak"] = max(stats["active_bytes_peak"], memory)
            stats["queued_peak"] = max(stats["queued_peak"], len(live) - len(active))
            if not result:
                stats["slot_block_polls"] += int(
                    slots + ticket.request.slots > ticket.capacity.slots
                )
                stats["memory_block_polls"] += int(
                    memory + ticket.request.bytes > ticket.capacity.bytes
                )
                stats["denied_with_free_capacity_polls"] += int(
                    slots + ticket.request.slots <= ticket.capacity.slots
                    and memory + ticket.request.bytes <= ticket.capacity.bytes
                )
        return result

    @contextmanager
    def claim(self, request, capacity, **kwargs):
        previous = getattr(_local, "pool_stats", None)
        stats = dict.fromkeys(
            (
                "lock_wait_ns",
                "lock_hold_ns",
                "lock_acquisitions",
                "polls",
                "active_slots_peak",
                "active_bytes_peak",
                "queued_peak",
                "slot_block_polls",
                "memory_block_polls",
                "denied_with_free_capacity_polls",
            ),
            0,
        )
        _local.pool_stats = stats
        start, cpu = time.monotonic_ns(), time.thread_time_ns()
        acquired = None
        outcome = "error"
        try:
            with reserve(self, request, capacity, **kwargs) as permit:
                acquired = time.monotonic_ns()
                emit(
                    "pool_admission",
                    start,
                    cpu,
                    pool=self.directory.name,
                    requested_slots=request.slots,
                    requested_bytes=request.bytes,
                    capacity_slots=capacity.slots,
                    capacity_bytes=capacity.bytes,
                    **stats,
                )
                _local.pool_stats = previous
                yield permit
                outcome = "returned"
        finally:
            _local.pool_stats = previous
            emit(
                "pool_lifetime",
                start,
                cpu,
                pool=self.directory.name,
                requested_slots=request.slots,
                requested_bytes=request.bytes,
                acquired_ns=acquired,
                outcome=outcome,
            )

    cls._coordinator, cls.reserve, cls._available = lock, claim, staticmethod(can_admit)


_METHODS = {
    "app.runtime.compute.client": [
        (None, "_connect", "broker_connect"),
        (None, "exchange_render", "render_exchange"),
    ],
    "app.runtime.compute.dispatcher": [
        ("Dispatcher", "admit_host", "broker_host_admission"),
        ("Dispatcher", "execute_render_batch", "broker_batch"),
    ],
    "app.modules.media.mesh_loading": [(None, "load_mesh", "mesh_load")],
    "app.modules.media.thumbnail_pixels": [(None, "encode_pixels", "codec")],
    "app.modules.media.thumbnail_packet": [
        (None, "read_packet", "packet_read"),
        (None, "write_packet", "packet_write"),
    ],
    "app.modules.media.compute_geometry": [(None, "decode_arrays", "geometry_decode")],
    "app.modules.media.webgpu_render": [
        ("Renderer", "_execute_prepared_batch", "render_batch")
    ],
    "app.modules.media.gpu_compact": [
        ("CompactGeometry", "acquire", "geometry_acquire"),
        ("CompactGeometry", "encode", "geometry_encode"),
    ],
    "wgpu.backends.wgpu_native._api": [
        ("GPUQueue", "submit", "gpu_submit_host"),
        ("GPUQueue", "write_buffer", "gpu_write_buffer_host"),
        ("GPUBuffer", "map_sync", "gpu_map_fence_wait"),
        ("GPUBuffer", "read_mapped", "gpu_read_mapped"),
    ],
}
_TARGETS = set(_METHODS) | {
    "app.runtime.native_admission",
    "app.runtime.compute.protocol",
}


def instrument(module):
    if module.__name__ == "app.runtime.native_admission":
        instrument_pool(module)
    elif module.__name__ == "app.runtime.compute.protocol":
        instrument_transport(module, "_read")
        instrument_transport(module, "send_body")
    else:
        for cls, name, stage in _METHODS[module.__name__]:
            timed(module if cls is None else getattr(module, cls), name, stage)


class TraceLoader:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def create_module(self, spec):
        return self.wrapped.create_module(spec)

    def exec_module(self, module):
        start, cpu = time.monotonic_ns(), time.thread_time_ns()
        self.wrapped.exec_module(module)
        emit("module_import", start, cpu, module=module.__name__)
        instrument(module)


class TraceFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _TARGETS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = TraceLoader(spec.loader)
        return spec


def install(directory: str):
    global _directory
    if _directory is not None:
        raise RuntimeError("compute trace already installed")
    _directory = Path(directory)
    _directory.mkdir(parents=True, exist_ok=True)
    now = time.monotonic_ns()
    emit("process_trace_start", now, time.thread_time_ns(), realtime_ns=time.time_ns())
    sys.meta_path.insert(0, TraceFinder())
