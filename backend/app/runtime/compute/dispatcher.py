"""One compute owner with shared residency and independent per-request outcomes."""

import base64
import gc
import time
from pathlib import Path

from .budget import Residency
from .contracts import (
    Capability,
    ComputeMode,
    ComputeStatus,
    ComputeUnavailable,
    Operation,
    Reason,
)
from .discovery import discover, runtime_identity
from .protocol import InferenceRequest, RenderRequest
from .qualification import read_receipts


class Dispatcher:
    def __init__(
        self, root: Path, *, mode: ComputeMode, selector: str | None, budget_bytes: int
    ):
        self.root, self.mode = root, mode
        self.memory = Residency(budget_bytes)
        self.workers = {}
        self.factory = None
        self.inference_reason = Reason.RUNTIME_MISSING
        self.device_info = self.device = None
        self.reason = Reason.DISABLED
        self.completed = self.fallbacks = self.inference_batches = 0
        self.cold_start = 0.0
        self.runtime = runtime_identity()
        self.renderer = None
        self.queue_seconds = self.execution_seconds = 0.0
        self.input_bytes = self.output_bytes = self.batch_inputs = 0
        self.residency_hits = self.residency_misses = 0
        self.host_sizes = {}
        self.host_claim = None
        self.host_pool = None
        self.host_capacity = 0
        self.active_deadline = None
        if mode is ComputeMode.AUTO:
            started = time.monotonic()
            try:
                self.device_info, self.device = discover(selector)
                self.reason = Reason.UNQUALIFIED
                self.memory.reserve(
                    "runtime", 128 * 1024**2, self._release_device, started
                )
                self.memory.pin("runtime", started)
                from app.modules.inference.webgpu import SessionFactory

                try:
                    self.factory = SessionFactory(self.device_info)
                    self.factory.probe()
                    self.inference_reason = Reason.UNQUALIFIED
                except ComputeUnavailable as exc:
                    self.inference_reason = exc.reason
                    self.factory = None
                except Exception:
                    self.inference_reason = Reason.DEVICE_FAILED
                    self.factory = None
            except ComputeUnavailable as exc:
                self.reason = exc.reason
            self.cold_start = time.monotonic() - started

    def _release_device(self) -> None:
        if self.device is not None:
            self.device.destroy()
            self.device = None

    def status(self, queue_depth: int = 0, queued_bytes: int = 0) -> ComputeStatus:
        from app.modules.inference.manifest import manifest_identity

        receipts = read_receipts(self.root / "qualification.json")
        ready = {
            receipt.operation
            for receipt in receipts
            if self.device_info is not None
            and receipt.device_identity == self.device_info.identity
            and receipt.runtime_identity == self.runtime
            and receipt.quality_passed
            and (receipt.operation is Operation.RENDER or self.factory is not None)
        }
        return ComputeStatus(
            mode=self.mode,
            device=self.device_info,
            runtime_identity=self.runtime,
            capabilities=[
                Capability(
                    operation=op,
                    available=op in ready,
                    reason=Reason.READY
                    if op in ready
                    else (
                        self.inference_reason
                        if self.device is not None and op is not Operation.RENDER
                        else self.reason
                    ),
                )
                for op in Operation
            ],
            budget_bytes=self.memory.capacity,
            reserved_bytes=self.memory.used,
            resident_entries=len(self.memory.entries),
            queue_depth=queue_depth,
            completed=self.completed,
            fallbacks=self.fallbacks,
            cold_start_seconds=self.cold_start,
            resident_model_ids=sorted(
                {
                    manifest_identity(worker.manifest)
                    for worker in tuple(self.workers.values())
                }
            ),
            queue_seconds=self.queue_seconds,
            execution_seconds=self.execution_seconds,
            input_bytes=self.input_bytes,
            output_bytes=self.output_bytes,
            batch_inputs=self.batch_inputs,
            residency_hits=self.residency_hits,
            residency_misses=self.residency_misses,
            host_reserved_bytes=self.host_capacity,
            queued_bytes=queued_bytes,
            rendering_transfer_seconds=self.renderer.transfer_seconds
            if self.renderer is not None
            else 0,
            inference_batches=self.inference_batches,
        )

    def admit_host(self, deadline: float) -> None:
        if self.host_claim is not None:
            return
        from app.runtime.inference_resources import capacity
        from app.runtime.native_admission import LocalResourcePool, Resources

        allowance = capacity()
        from app.core.config import settings

        amount = min(settings.embedding_worker_memory_mb * 1024**2, allowance.bytes)
        self.host_pool = LocalResourcePool(self.root.parent / "inference-models")

        def checkpoint():
            if time.monotonic() >= deadline:
                raise ComputeUnavailable(Reason.DEADLINE) from None

        claim = self.host_pool.reserve(
            Resources(1, amount), allowance, checkpoint=checkpoint
        )
        claim.__enter__()
        self.host_claim = claim
        self.host_capacity = amount

    def make_host_room(self, key: str, size: int) -> None:
        # Leave room for Python, both runtimes and the immutable input frame.
        available = self.host_capacity - 192 * 1024**2
        for candidate in list(self.memory.entries):
            if (
                sum(self.host_sizes.values()) - self.host_sizes.get(key, 0) + size
                <= available
            ):
                break
            if candidate != key and not self.memory.entries[candidate].pins:
                self.memory.remove(candidate)
        if (
            sum(self.host_sizes.values()) - self.host_sizes.get(key, 0) + size
            > available
        ):
            raise ComputeUnavailable(Reason.CAPACITY)

    def pressure(self) -> bool:
        return self.host_pool is not None and self.host_pool.has_waiters(
            checkpoint=lambda: None
        )

    def qualification(self, operation: Operation, recipe: str, units: int):
        if self.device_info is None or self.device is None:
            raise ComputeUnavailable(self.reason)
        receipts = read_receipts(self.root / "qualification.json")
        for receipt in receipts:
            if receipt.accepts(
                self.device_info, self.runtime, operation, recipe, units
            ):
                return receipt
        raise ComputeUnavailable(Reason.UNQUALIFIED)

    def execute(self, request: InferenceRequest | RenderRequest) -> bytes:
        if time.monotonic() >= request.deadline:
            raise ComputeUnavailable(Reason.DEADLINE) from None
        self.active_deadline = request.deadline
        started = time.monotonic()
        self.input_bytes += len(request.payload)
        try:
            self.memory.expire(time.monotonic())
            if isinstance(request, RenderRequest):
                result = self.render(request)
            else:
                result = self.inference(request)
            if time.monotonic() >= request.deadline:
                raise ComputeUnavailable(Reason.DEADLINE) from None
            self.completed += 1
            self.output_bytes += len(result)
            return result
        finally:
            self.execution_seconds += time.monotonic() - started
            self.active_deadline = None

    def inference(self, request: InferenceRequest) -> bytes:
        from app.modules.inference.manifest import (
            SparseModelManifest,
            manifest_identity,
            read_manifest,
        )
        from app.modules.inference.model_cache import safe_directory
        from app.modules.inference.webgpu import SessionFactory, native_worker
        from app.modules.inference.worker_protocol import MAX_INPUT_BYTES, WorkerRequest

        if self.factory is None:
            raise ComputeUnavailable(
                self.inference_reason if self.device is not None else self.reason
            )
        payload = base64.b64decode(request.payload, validate=True)
        if len(payload) > MAX_INPUT_BYTES:
            raise ComputeUnavailable(Reason.INVALID_INPUT)
        message = WorkerRequest.model_validate_json(payload)
        directory = safe_directory(Path(request.directory))
        manifest = read_manifest(directory, request.model_key)
        identity = manifest_identity(manifest)
        operation = (
            Operation.SPARSE
            if isinstance(manifest, SparseModelManifest)
            else Operation.DENSE
        )
        receipt = self.qualification(
            operation, identity, max(1, len(message.inputs), len(message.sparse_texts))
        )
        self.admit_host(request.deadline)
        key = f"{directory}:{identity}:{request.threads}"
        # Stat fingerprints are part of residency identity: a cached session can
        # never silently certify newly replaced bytes with an earlier digest.
        fingerprints = tuple(
            (
                a.filename,
                (directory / a.filename).stat().st_ino,
                (directory / a.filename).stat().st_mtime_ns,
                (directory / a.filename).stat().st_size,
            )
            for a in manifest.assets()
        )
        key += repr(fingerprints)
        self.inference_batches += 1
        self.batch_inputs += max(
            len(message.inputs),
            len(message.sparse_texts),
            int(message.sparse_text is not None),
        )
        if key not in self.workers:
            self.residency_misses += 1
            self.make_host_room(key, receipt.peak_host_bytes)
            self.memory.make_room(receipt.peak_device_bytes)
            if self.factory is None:
                assert self.device_info is not None
                self.factory = SessionFactory(self.device_info)

            def release():
                self.workers.pop(key, None)
                self.host_sizes.pop(key, None)
                gc.collect()

            self.memory.reserve(
                key, receipt.peak_device_bytes, release, time.monotonic()
            )
            self.host_sizes[key] = receipt.peak_host_bytes
            try:
                self.workers[key] = native_worker(
                    directory, request.model_key, request.threads, self.factory
                )
            except Exception:
                self.memory.remove(key)
                raise
        else:
            # A larger qualified batch can require more workspace than the one
            # that loaded these weights. Admit the additional peak without
            # discarding the warm session; failure leaves it available for
            # smaller work and sends this request back to CPU.
            host_size = max(self.host_sizes[key], receipt.peak_host_bytes)
            self.make_host_room(key, host_size)
            self.memory.grow(key, receipt.peak_device_bytes)
            self.host_sizes[key] = host_size
            self.residency_hits += 1
        self.memory.pin(key, time.monotonic())
        try:
            from .recovery import execute

            def evict():
                for idle in list(self.memory.entries):
                    if not self.memory.entries[idle].pins:
                        self.memory.remove(idle)

            return execute(self.workers[key], payload, request.deadline, evict)
        finally:
            self.memory.unpin(key)

    def render(self, request: RenderRequest) -> bytes:
        from app.modules.media.webgpu_render import Renderer

        receipt = self.qualification(Operation.RENDER, request.recipe, request.units)
        self.admit_host(request.deadline)
        key = "renderer"
        if (
            key in self.memory.entries
            and self.memory.entries[key].size < receipt.peak_device_bytes
        ):
            self.memory.remove(key)
        if key not in self.memory.entries:
            self.make_host_room(key, receipt.peak_host_bytes)
            self.memory.make_room(receipt.peak_device_bytes)
            if self.renderer is None:
                self.renderer = Renderer(self.device, self.memory)

            def release():
                assert self.renderer is not None
                self.renderer.close()
                self.host_sizes.pop(key, None)

            self.memory.reserve(
                key, receipt.peak_device_bytes, release, time.monotonic()
            )
            self.host_sizes[key] = receipt.peak_host_bytes
        self.memory.pin(key, time.monotonic())
        try:
            assert self.renderer is not None
            self.renderer.workspace_limit = self.memory.entries[key].size
            from .recovery import allocation_failure

            payload = base64.b64decode(request.payload, validate=True)
            try:
                return self.renderer.execute(payload)
            except Exception as exc:
                if not allocation_failure(exc):
                    raise
            for idle in list(self.memory.entries):
                if not self.memory.entries[idle].pins:
                    self.memory.remove(idle)
            if time.monotonic() >= request.deadline:
                raise ComputeUnavailable(Reason.DEADLINE)
            try:
                return self.renderer.execute(payload)
            except Exception as exc:
                if allocation_failure(exc):
                    raise ComputeUnavailable(Reason.CAPACITY) from exc
                raise
        finally:
            self.memory.unpin(key)

    def close(self) -> None:
        for key in sorted(self.memory.entries, key=lambda key: key == "runtime"):
            entry = self.memory.entries[key]
            while entry.pins:
                self.memory.unpin(key)
            self.memory.remove(key)
        if self.host_claim is not None:
            self.host_claim.__exit__(None, None, None)
            self.host_claim = None
