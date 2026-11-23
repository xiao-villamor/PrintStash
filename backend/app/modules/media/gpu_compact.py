"""Compact render geometry, grouped uploads and fenced transient ownership."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.runtime.compute.budget import Residency
from app.runtime.compute.contracts import ComputeUnavailable, Reason

from .gpu_geometry import FACE_CHUNK, CompletionFence
from .gpu_normals import canonical
from .gpu_render_layout import bounds_scratch_sizes, geometry_sizes
from .gpu_views import ViewPipeline


@dataclass
class Geometry:
    positions: Any
    faces: Any
    corner_normals: Any
    bounds: Any
    has_surface: bool

    def destroy(self):
        for buffer in (
            self.positions,
            self.faces,
            self.corner_normals,
            self.bounds,
        ):
            buffer.destroy()


@dataclass
class Pending:
    key: str
    geometry_key: str
    vertex_count: int
    owned: list[Any] = field(default_factory=list)
    bounds: list[Any] = field(default_factory=list)


class CompactGeometry:
    """A batch's inputs stay pinned until its shared final map or failure fence."""

    def __init__(self, device: Any, memory: Residency, views: ViewPipeline):
        self.device, self.memory, self.views = device, memory, views
        self.geometry: dict[str, Geometry] = {}
        self.pending: list[Pending] = []
        self.upload_parts: list[tuple[Any, int, Any]] = []
        self.fence = CompletionFence(device, memory)
        self.staging_key: str | None = None
        self.staging = None
        self.normal_seconds = self.transfer_seconds = 0.0
        self.transferred_bytes = self.upload_calls = 0
        # upload_calls counts host copies into staging, not GPU submissions.
        # transfer_seconds is host upload latency; it is not kernel GPU time.
        self.bounds_vertices = 0

    def _buffer(self, size: int):
        import wgpu

        if size > self.device.limits["max-storage-buffer-binding-size"]:
            raise ComputeUnavailable(Reason.CAPACITY)
        return self.device.create_buffer(
            size=size,
            usage=wgpu.BufferUsage.STORAGE
            | wgpu.BufferUsage.COPY_DST
            | wgpu.BufferUsage.COPY_SRC,
        )

    def acquire(self, prepared: Any, key: str) -> Geometry:
        if key in self.geometry:
            return self.geometry[key]
        started = time.monotonic()
        prepared = canonical(prepared)
        self.normal_seconds += time.monotonic() - started
        sizes = geometry_sizes(len(prepared.vertices), prepared.face_count)
        buffers: list[Any] = []

        def release():
            self.geometry.pop(key, None)
            for buffer in buffers:
                buffer.destroy()

        self.memory.reserve(key, sum(sizes), release, time.monotonic())
        self.memory.pin(key, time.monotonic())
        pending_key = f"compact-scratch:{id(self)}:{key}"
        item = Pending(pending_key, key, len(prepared.vertices))
        self.pending.append(item)
        try:
            for size in sizes:
                buffers.append(self._buffer(size))
            geometry = Geometry(
                *buffers, has_surface=bool(prepared.corner_normals.any())
            )
            self.geometry[key] = geometry
            bound_sizes = bounds_scratch_sizes(len(prepared.vertices))
            scratch_size = sum(bound_sizes)
            if scratch_size:
                self.memory.reserve(
                    pending_key,
                    scratch_size,
                    lambda: self._destroy(item.owned),
                    time.monotonic(),
                )
                self.memory.pin(pending_key, time.monotonic())
            for size in bound_sizes:
                buffer = self._buffer(size)
                item.owned.append(buffer)
                item.bounds.append(buffer)
            self.upload_parts.append((geometry.positions, 0, prepared.vertices))
            offset = 0
            for faces in prepared.face_chunks(FACE_CHUNK):
                values = np.asarray(faces, dtype="<u4")
                self.upload_parts.append((geometry.faces, offset, values))
                offset += values.nbytes
            self.upload_parts.append(
                (geometry.corner_normals, 0, prepared.corner_normals)
            )
            return geometry
        except BaseException:
            # Keep every allocation in pending ownership; release_completed also
            # handles partial allocation and command-encoding failures.
            raise

    @staticmethod
    def _destroy(buffers: list[Any]):
        for buffer in buffers:
            buffer.destroy()
        buffers.clear()

    def encode(self, encoder: Any, configs: Any, config_data: bytes):
        import wgpu

        parts = [*self.upload_parts, (configs, 0, config_data)]
        size = sum(memoryview(data).nbytes for _, _, data in parts)
        key = f"compact-staging:{id(self)}"
        self.memory.reserve(key, size, self._destroy_staging, time.monotonic())
        self.memory.pin(key, time.monotonic())
        self.staging_key = key
        self.staging = []
        # Portable max-buffer limits need not cover the sum of an admitted
        # batch. Bound the CPU staging copy and split only transfer buffers;
        # all rendering still shares one command encoder and submission.
        slab_limit = min(
            64 * 1024**2, self.device.limits.get("max-buffer-size", 256 * 1024**2)
        )
        part_index = part_offset = 0
        remaining = size
        while remaining:
            slab_size = min(remaining, slab_limit)
            arena = bytearray(slab_size)
            copies = []
            offset = 0
            while offset < slab_size:
                target, target_offset, data = parts[part_index]
                source = memoryview(data).cast("B")
                count = min(source.nbytes - part_offset, slab_size - offset)
                arena[offset : offset + count] = source[
                    part_offset : part_offset + count
                ]
                copies.append((offset, target, target_offset + part_offset, count))
                offset += count
                part_offset += count
                if part_offset == source.nbytes:
                    part_index += 1
                    part_offset = 0
            # MAP_WRITE|COPY_SRC is the portable upload usage combination.
            # Mapping this owned buffer avoids queue.write_buffer's additional
            # native staging allocation and hidden copy into our staging slab.
            started = time.monotonic()
            staging = self.device.create_buffer(
                size=slab_size,
                usage=wgpu.BufferUsage.MAP_WRITE | wgpu.BufferUsage.COPY_SRC,
                mapped_at_creation=True,
            )
            self.staging.append(staging)
            try:
                staging.write_mapped(arena)
            finally:
                staging.unmap()
            self.transfer_seconds += time.monotonic() - started
            for source_offset, target, target_offset, count in copies:
                encoder.copy_buffer_to_buffer(
                    staging, source_offset, target, target_offset, count
                )
            self.transferred_bytes += slab_size
            self.upload_calls += 1
            remaining -= slab_size
        self.upload_parts.clear()
        for item in self.pending:
            geometry = self.geometry[item.geometry_key]
            self.views.encode_bounds(
                encoder,
                geometry.positions,
                item.vertex_count,
                geometry.bounds,
                item.bounds,
            )
            self.bounds_vertices += item.vertex_count

    def _destroy_staging(self):
        if self.staging is not None:
            self._destroy(self.staging)
            self.staging = None

    def release_completed(self, *, fence_complete: bool):
        if not fence_complete and (self.pending or self.staging_key is not None):
            self.fence.wait()
        self.upload_parts.clear()
        if self.staging_key is not None:
            self.memory.unpin(self.staging_key)
            self.memory.remove(self.staging_key)
            self.staging_key = None
        for item in self.pending:
            if item.key in self.memory.entries:
                self.memory.unpin(item.key)
                self.memory.remove(item.key)
            self.memory.unpin(item.geometry_key)
        self.pending.clear()

    def close(self):
        self.release_completed(fence_complete=False)
        self.fence.close()
