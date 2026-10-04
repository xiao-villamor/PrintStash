"""Run one mesh derivative in a disposable child process.

Mesh loading and rasterising allocate in proportion to attacker-controlled input,
and an estimate of that input is never exact (#259). In the API process a miss
kills every request and, because the derivative is retried, the container with
it. In a child the same miss costs one process: the parent watches the child's
resident memory and a deadline, kills its whole process group when either is
exceeded, and records a failure for that one Artifact.

The child is `mesh_worker`; it answers with one framed reply that this module
also encodes and decodes, so the wire format lives in a single place. Nothing here
imports trimesh or NumPy, so the parent stays as small as before.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import selectors
import signal
import struct
import subprocess  # nosec B404 - fixed interpreter/module invocation only
import tempfile
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

from printstash_core.mesh.similarity import GeometryError

from app import __file__ as application_file
from app.core.cancellation import OperationCancelled, checkpoint
from app.core.config import _overlay, settings
from app.modules.media import mesh_processing
from app.modules.media.fingerprints import FingerprintRecord, FingerprintResult
from app.modules.media.mesh_observability import record_phases, record_supervision
from app.modules.media.mesh_telemetry import (
    SupervisedReply,
    SupervisionStats,
    WorkerExitCause,
    decode_phase_stats,
    encode_phase_stats,
)
from app.modules.media.stl_streaming import _terminate_process_group
from app.modules.media.thumbnail_engine import (
    GeometryNotRequested,
    GeometryOutcome,
    GeometryReady,
    GeometryRefused,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.worker_bootstrap import RESOURCE_EXIT, reap_descendants
from app.modules.media.worker_bootstrap import command as worker_command

REPLY_MAGIC = b"MSH1"
MAX_REPLY_BYTES = 32 * 1024 * 1024
_POLL_SECONDS = 0.025
# Used only where neither a cgroup limit nor host memory can be read.
_FALLBACK_MEMORY_BUDGET = 2 * 1024**3


class MeshWorkerError(Exception):
    """The child could not produce a result; `reason` says why."""

    def __init__(self, reason: ThumbnailFailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.supervision: SupervisionStats | None = None


class MeshWorkerCancelled(OperationCancelled):
    """Withdrawal preserves observed parent costs without becoming job failure."""

    def __init__(self, supervision: SupervisionStats) -> None:
        super().__init__()
        self.supervision = supervision


def memory_budget_bytes() -> int:
    """Resident memory one worker may reach before it is killed.

    The same budget the triangle caps are derived from, so a mesh the caps admit
    fits in it; a mesh the caps mis-sized is what the kill is for.
    """
    return mesh_processing.step_memory_budget_bytes() or (
        _FALLBACK_MEMORY_BUDGET // mesh_processing._render_jobs_limit()
    )


def pack_value(value: Any) -> Any:
    """JSON-safe form of a fingerprint value that keeps bytes and tuples distinct.

    Fingerprints carry raw descriptor blobs and tuples, which plain JSON would
    drop or flatten. Only these types are allowed: the reply comes from a process
    that has just parsed a hostile file, so the parent decodes data, never code
    (no pickle), and refuses anything it does not recognise.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"$tuple": [pack_value(item) for item in value]}
    if isinstance(value, list):
        return [pack_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or key.startswith("$") for key in value):
            raise TypeError("unencodable mapping key")
        return {key: pack_value(item) for key, item in value.items()}
    raise TypeError(f"unencodable {type(value).__name__}")


def unpack_value(value: Any) -> Any:
    if isinstance(value, list):
        return [unpack_value(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1 and "$bytes" in value:
            return base64.b64decode(value["$bytes"], validate=True)
        if len(value) == 1 and "$tuple" in value:
            return tuple(unpack_value(item) for item in value["$tuple"])
        if any(key.startswith("$") for key in value):
            raise ValueError("unknown tag")
        return {key: unpack_value(item) for key, item in value.items()}
    return value


def encode_geometry(outcome: GeometryOutcome) -> dict[str, str]:
    if isinstance(outcome, GeometryReady):
        return {"state": "ready"}
    if isinstance(outcome, GeometryRefused):
        return {"state": "refused", "reason": outcome.reason.value}
    if isinstance(outcome, GeometryNotRequested):
        return {"state": "not_requested"}
    raise TypeError("invalid geometry outcome")


def decode_geometry(raw: dict[str, str]) -> GeometryOutcome:
    if raw == {"state": "ready"}:
        return GeometryReady()
    if raw == {"state": "not_requested"}:
        return GeometryNotRequested()
    if set(raw) == {"state", "reason"} and raw["state"] == "refused":
        return GeometryRefused(ThumbnailFailureReason(raw["reason"]))
    raise ValueError("invalid geometry outcome")


def encode_reply(result: ThumbnailResult) -> bytes:
    image = result.image or b""
    fingerprint = result.fingerprint_result
    header = json.dumps(
        {
            "phase_stats": encode_phase_stats(result.phase_stats),
            "geometry": result.geometry,
            "geometry_outcome": encode_geometry(result.geometry_outcome),
            "strategy": result.strategy.value,
            "complete": result.complete,
            "failure_reason": (
                result.failure_reason.value if result.failure_reason else None
            ),
            "duration_ms": result.duration_ms,
            "peak_rss_bytes": result.peak_rss_bytes,
            "has_image": result.image is not None,
            "image_length": len(image),
            "fingerprint": (
                None
                if fingerprint is None
                else {
                    "state": fingerprint.state,
                    "failure_code": fingerprint.failure_code,
                    "algorithm_version": fingerprint.algorithm_version,
                    "records": [
                        {
                            "component_index": record.component_index,
                            "instance_count": record.instance_count,
                            "values": pack_value(record.values),
                            "instances": [
                                pack_value(item) for item in record.instances
                            ],
                        }
                        for record in fingerprint.records
                    ],
                }
            ),
        },
        allow_nan=False,
    ).encode()
    return REPLY_MAGIC + struct.pack("!I", len(header)) + header + image


def decode_reply(payload: bytes) -> ThumbnailResult:
    """Rebuild the result, treating anything malformed as a failed worker."""
    try:
        if payload[:4] != REPLY_MAGIC:
            raise ValueError("magic")
        (header_length,) = struct.unpack("!I", payload[4:8])
        header = json.loads(payload[8 : 8 + header_length])
        image_length = header["image_length"]
        image = payload[8 + header_length :]
        if len(image) != image_length:
            raise ValueError("image length")
        raw = header["fingerprint"]
        fingerprint = (
            None
            if raw is None
            else FingerprintResult(
                state=raw["state"],
                failure_code=raw["failure_code"],
                algorithm_version=raw["algorithm_version"],
                records=tuple(
                    FingerprintRecord(
                        component_index=record["component_index"],
                        instance_count=record["instance_count"],
                        values=unpack_value(record["values"]),
                        instances=tuple(
                            unpack_value(item) for item in record["instances"]
                        ),
                    )
                    for record in raw["records"]
                ),
            )
        )
        reason = header["failure_reason"]
        return ThumbnailResult(
            image=image if header["has_image"] else None,
            geometry=dict(header["geometry"]),
            geometry_outcome=decode_geometry(header["geometry_outcome"]),
            strategy=ThumbnailStrategy(header["strategy"]),
            complete=bool(header["complete"]),
            failure_reason=None if reason is None else ThumbnailFailureReason(reason),
            duration_ms=int(header["duration_ms"]),
            peak_rss_bytes=header["peak_rss_bytes"],
            fingerprint_result=fingerprint,
            phase_stats=decode_phase_stats(header["phase_stats"]),
        )
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        binascii.Error,
        struct.error,
    ) as exc:
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc


def supervise_result(
    command: list[str], *, memory_budget: int, timeout_seconds: float
) -> SupervisedReply:
    """Own the process lifecycle and retain observed costs on every exit.

    Tree RSS is sampled, not a kernel high-water mark. None means no sample was
    available. The one terminal frame provides no phase attribution on a kill.
    """
    started = time.monotonic_ns()
    execution_id = secrets.token_hex(16)
    peak_rss: int | None = None
    reply = bytearray()
    cause = WorkerExitCause.SPAWN_FAILED
    error: MeshWorkerError | None = None
    cancelled: OperationCancelled | None = None
    process: subprocess.Popen[bytes] | None = None
    temporary = tempfile.TemporaryDirectory(prefix="printstash-mesh-")
    try:
        try:
            process = subprocess.Popen(  # nosec B603 - fixed argv; no shell
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={
                    **os.environ,
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "TMPDIR": temporary.name,
                },
                cwd=Path(application_file).resolve().parent.parent,
            )
        except OSError as exc:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
        cause = WorkerExitCause.SUPERVISION_FAILED
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        deadline = time.monotonic() + timeout_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            stdout_closed = False
            while True:
                checkpoint()
                if time.monotonic() >= deadline:
                    cause = WorkerExitCause.DEADLINE
                    raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)
                rss = mesh_processing.process_tree_rss_bytes(process.pid)
                if rss is not None:
                    peak_rss = rss if peak_rss is None else max(peak_rss, rss)
                    if rss > memory_budget:
                        cause = WorkerExitCause.MEMORY_LIMIT
                        raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
                for key, _ in selector.select(_POLL_SECONDS):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        stdout_closed = True
                        selector.unregister(process.stdout)
                        break
                    reply.extend(chunk)
                    if len(reply) > MAX_REPLY_BYTES:
                        cause = WorkerExitCause.REPLY_LIMIT
                        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
                if stdout_closed and process.poll() is not None:
                    break
        # EOF only ends the reply: native work may continue after closing stdout.
        # Polling above keeps resource and cancellation checks active until exit.
        code = process.wait()
        if code in (-signal.SIGKILL, RESOURCE_EXIT):
            # Preserve the existing refusal classification, without claiming
            # that a signal proves an OOM: users and the child can send it too.
            cause = (
                WorkerExitCause.SIGKILL
                if code == -signal.SIGKILL
                else WorkerExitCause.MEMORY_LIMIT
            )
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        if code != 0:
            cause = WorkerExitCause.EXITED_NONZERO
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        checkpoint(force=True)
        cause = WorkerExitCause.EXITED_ZERO
    except MeshWorkerError as exc:
        error = exc
        try:
            checkpoint(force=True)
        except OperationCancelled as exc:
            cancelled = exc
            cause = WorkerExitCause.CANCELLED
    except OperationCancelled as exc:
        cancelled = exc
        cause = WorkerExitCause.CANCELLED
    except OSError as exc:
        error = MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        error.__cause__ = exc
    finally:
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _terminate_process_group(process)
                process.wait()
                reap_descendants(process.pid)
                if process.stdout is not None:
                    process.stdout.close()
        except OSError as exc:
            cause = WorkerExitCause.SUPERVISION_FAILED
            error = MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            error.__cause__ = exc
        finally:
            try:
                temporary.cleanup()
            except OSError as exc:
                cause = WorkerExitCause.SUPERVISION_FAILED
                error = MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
                error.__cause__ = exc
            stats = SupervisionStats(
                execution_id, time.monotonic_ns() - started, peak_rss, len(reply), cause
            )
            if error is not None:
                error.supervision = stats
            record_supervision(stats)
    if cancelled is not None:
        raise MeshWorkerCancelled(stats) from cancelled
    if error is not None:
        raise error
    return SupervisedReply(bytes(reply), stats)


def supervise(
    command: list[str], *, memory_budget: int, timeout_seconds: float
) -> bytes:
    """Byte-only convenience retained for non-mesh workers and containment probes."""
    return supervise_result(
        command, memory_budget=memory_budget, timeout_seconds=timeout_seconds
    ).payload


def runtime_overrides() -> dict[str, str | int | float | bool]:
    """The API process's scalar runtime overrides, for the worker to adopt.

    Administrators change some settings at runtime and tests change others; both
    live in an in-process overlay a new process cannot see. Without this the
    worker would apply the environment's defaults to a request the parent
    configured differently. Only scalars cross: every setting the worker reads
    is one.
    """
    return {
        key: value
        for key, value in dict(_overlay).items()
        if isinstance(value, str | int | float | bool)
    }


ERROR_MAGIC = b"ERR1"
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def encode_error(error: GeometryError) -> bytes:
    """The reply a worker sends for a failure that owns a stable code."""
    return ERROR_MAGIC + error.code.encode("ascii")[:64]


def raise_reported_error(payload: bytes) -> None:
    """Raise the failure a worker reported; return if *payload* reports none.

    A code that is not a plain identifier is not trusted to name a failure: it
    came from a process that has just parsed a hostile file.
    """
    if not payload.startswith(ERROR_MAGIC):
        return
    code = payload[len(ERROR_MAGIC) :].decode("ascii", errors="replace")
    if _ERROR_CODE.fullmatch(code) is None:
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
    raise GeometryError(code)


def absolute(path: Path) -> str:
    """*path* as the worker must name it.

    Storage may hand back a path relative to this process's working directory;
    the worker starts elsewhere, where a relative path would name nothing.
    """
    return str(path.absolute())


def _run_worker(module: str, spec: dict[str, Any]) -> SupervisedReply:
    """Run `python -m module` on *spec* under supervision; return its reply.

    Every isolated worker starts the same way: it is handed the parent's runtime
    overrides with its request, and it counts against the same local concurrency
    limit as the mesh derivatives it shares memory with.
    """
    with mesh_processing._render_semaphore():
        budget = memory_budget_bytes()
        command = worker_command(
            module,
            [json.dumps({"overrides": runtime_overrides(), **spec})],
            budget,
        )
        return supervise_result(
            command,
            memory_budget=budget,
            timeout_seconds=float(settings.mesh_worker_timeout_seconds),
        )


def run_worker(module: str, spec: dict[str, Any]) -> bytes:
    """Byte reply convenience for STL conversion, embedding and verification."""
    return _run_worker(module, spec).payload


def read_spec(argv: list[str]) -> dict[str, Any]:
    """The request a worker was started with, after adopting the parent's overrides."""
    (raw,) = argv
    spec = json.loads(raw)
    _overlay.update(spec["overrides"])
    return spec


def generate(request: ThumbnailRequest) -> ThumbnailResult:
    """`ThumbnailEngine.generate` for *request*, run in a supervised child."""
    spec = {
        "path": absolute(request.path),
        "file_type": request.file_type,
        "width": request.width,
        "height": request.height,
        "include_geometry": request.include_geometry,
        "include_thumbnail": request.include_thumbnail,
        "include_fingerprint": request.include_fingerprint,
        "triangle_cap": request.triangle_cap,
        "output_format": request.output_format,
        "reason": request.reason,
    }
    with mesh_processing._render_semaphore(), ExitStack() as resources:
        if request.include_fingerprint and mesh_processing._canonical_suffix(
            request.path, request.file_type
        ) in (".step", ".stp"):
            from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

            from app.db.session import get_session_factory
            from app.modules.storage.capacity import CapacityManager, CapacityResource

            faces = min(settings.mesh_max_render_triangles, MAX_ANALYSIS_FACES)
            reservation = CapacityManager(get_session_factory()).reserve(
                "step-tessellation:" + secrets.token_hex(12),
                [
                    CapacityResource.for_path(
                        Path(tempfile.gettempdir()),
                        max(faces, 1) * 96 + 1024 * 1024 + 64 * 1024,
                        role="STEP tessellation",
                    )
                ],
            )
            resources.callback(reservation.release)
        reply = _run_worker("app.modules.media.mesh_worker", spec)
        try:
            result = decode_reply(reply.payload)
        except MeshWorkerError as exc:
            exc.supervision = reply.stats
            raise
        record_phases(result.phase_stats, reply.stats)
        return replace(result, supervision=reply.stats)
