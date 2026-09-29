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
import selectors
import signal
import struct
import subprocess  # nosec B404 - fixed interpreter/module invocation only
import sys
import time
from pathlib import Path
from typing import Any

from app import __file__ as application_file
from app.core.config import _overlay, settings
from app.modules.media import mesh_processing
from app.modules.media.fingerprints import FingerprintRecord, FingerprintResult
from app.modules.media.stl_streaming import _terminate_process_group
from app.modules.media.thumbnail_engine import (
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)

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


def memory_budget_bytes() -> int:
    """Resident memory one worker may reach before it is killed.

    The same budget the triangle caps are derived from, so a mesh the caps admit
    fits in it; a mesh the caps mis-sized is what the kill is for.
    """
    return mesh_processing.step_memory_budget_bytes() or _FALLBACK_MEMORY_BUDGET


def _pack(value: Any) -> Any:
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
        return {"$tuple": [_pack(item) for item in value]}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or key.startswith("$") for key in value):
            raise TypeError("unencodable mapping key")
        return {key: _pack(item) for key, item in value.items()}
    raise TypeError(f"unencodable {type(value).__name__}")


def _unpack(value: Any) -> Any:
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1 and "$bytes" in value:
            return base64.b64decode(value["$bytes"], validate=True)
        if len(value) == 1 and "$tuple" in value:
            return tuple(_unpack(item) for item in value["$tuple"])
        if any(key.startswith("$") for key in value):
            raise ValueError("unknown tag")
        return {key: _unpack(item) for key, item in value.items()}
    return value


def encode_reply(result: ThumbnailResult) -> bytes:
    image = result.image or b""
    fingerprint = result.fingerprint_result
    header = json.dumps(
        {
            "geometry": result.geometry,
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
                            "values": _pack(record.values),
                            "instances": [_pack(item) for item in record.instances],
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
                        values=_unpack(record["values"]),
                        instances=tuple(_unpack(item) for item in record["instances"]),
                    )
                    for record in raw["records"]
                ),
            )
        )
        reason = header["failure_reason"]
        return ThumbnailResult(
            image=image if header["has_image"] else None,
            geometry=dict(header["geometry"]),
            strategy=ThumbnailStrategy(header["strategy"]),
            complete=bool(header["complete"]),
            failure_reason=None if reason is None else ThumbnailFailureReason(reason),
            duration_ms=int(header["duration_ms"]),
            peak_rss_bytes=header["peak_rss_bytes"],
            fingerprint_result=fingerprint,
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


def supervise(
    command: list[str], *, memory_budget: int, timeout_seconds: float
) -> bytes:
    """Run *command*, returning everything it wrote to stdout on a clean exit.

    The child leads its own process group, so a kill reaches anything it spawned.
    Where a process's resident memory cannot be read (non-Linux) the memory limit
    is not enforced; the deadline still is.
    """
    process = subprocess.Popen(  # nosec B603 - argv is fixed; no shell
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
        cwd=Path(application_file).resolve().parent.parent,
    )
    try:
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        deadline = time.monotonic() + timeout_seconds
        reply = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if time.monotonic() >= deadline:
                    raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)
                rss = mesh_processing.process_rss_bytes(process.pid)
                if rss is not None and rss > memory_budget:
                    raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
                finished = False
                for key, _ in selector.select(_POLL_SECONDS):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        finished = True
                        break
                    reply.extend(chunk)
                    if len(reply) > MAX_REPLY_BYTES:
                        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
                if finished:
                    break
        try:
            code = process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired as exc:
            raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT) from exc
        if code == -signal.SIGKILL:
            # Nothing of ours sends SIGKILL after a clean read; the kernel's
            # out-of-memory killer does.
            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
        if code != 0:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        return bytes(reply)
    finally:
        _terminate_process_group(process)
        process.wait()
        if process.stdout is not None:
            process.stdout.close()


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


def generate(request: ThumbnailRequest) -> ThumbnailResult:
    """`ThumbnailEngine.generate` for *request*, run in a supervised child."""
    spec = {
        "overrides": runtime_overrides(),
        # Storage may hand back a path relative to this process's working
        # directory; the worker starts elsewhere, where it would name nothing.
        "path": str(request.path.absolute()),
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
    command = [
        sys.executable,
        "-m",
        "app.modules.media.mesh_worker",
        json.dumps(spec),
    ]
    with mesh_processing._render_semaphore():
        payload = supervise(
            command,
            memory_budget=memory_budget_bytes(),
            timeout_seconds=float(settings.mesh_worker_timeout_seconds),
        )
    return decode_reply(payload)
