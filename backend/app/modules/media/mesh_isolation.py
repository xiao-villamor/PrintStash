"""Run one mesh derivative in a disposable child process.

Mesh loading and rasterising allocate in proportion to attacker-controlled input,
and an estimate of that input is never exact (#259). In the API process a miss
kills every request and, because the derivative is retried, the container with
it. In a child the same miss costs one process: the parent watches the child's
resident memory and a deadline, kills its whole process group when either is
exceeded, and records a failure for that one Artifact.

The child is `mesh_worker`; it streams validated basic outputs before a terminal
frame. The staged wire contract lives in `mesh_protocol`; the legacy one-reply
codec remains here for compatibility. Nothing here
imports trimesh or NumPy, so the parent stays as small as before.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import selectors
import shutil
import signal
import stat
import struct
import subprocess  # nosec B404 - fixed interpreter/module invocation only
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Any, Final

from printstash_core.mesh.measurements import decode_volume, encode_volume
from printstash_core.mesh.similarity import GeometryError

from app import __file__ as application_file
from app.core.cancellation import OperationCancelled, checkpoint
from app.core.native_settings import settings
from app.modules.media import mesh_policy, native_process
from app.modules.media.fingerprints import (
    FingerprintRecord,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_contracts import (
    GeometryNotRequested,
    MeshMeasurements,
    PreviewCoverage,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
    decode_coverage,
    decode_geometry,
    encode_coverage,
    encode_geometry,
)
from app.modules.media.mesh_facts import FingerprintFailureCode
from app.modules.media.mesh_protocol import (
    FrameDecoder,
    GeometryOutput,
    MeshProtocolError,
    OutputSink,
    PreparedThumbnailOutput,
    ThumbnailOutput,
)
from app.modules.media.mesh_telemetry import (
    SupervisedReply,
    SupervisionStats,
    WorkerExitCause,
    decode_phase_stats,
    encode_phase_stats,
)
from app.modules.media.native_budget import (
    AnalysisWork,
    GeometryWork,
    MeshSource,
    RasterCodec,
    RasterWork,
    WorkProfile,
    estimate_sources,
)
from app.modules.media.native_execution import admission
from app.modules.media.worker_bootstrap import (
    PRIORITY_ENV,
    RESOURCE_EXIT,
    WorkerLifecycle,
    launch_resources,
    reap_descendants,
    terminate_worker,
)
from app.modules.media.worker_bootstrap import command as worker_command
from app.runtime.native_admission import NativePermit, Resources
from app.runtime.native_runtime import current_permit

if TYPE_CHECKING:
    from app.modules.media.thumbnail_handoff import ThumbnailHandoff
    from app.modules.media.thumbnail_packet import PacketDescriptor

REPLY_MAGIC = b"MSH1"
MAX_REPLY_BYTES = 32 * 1024 * 1024
# Output wakes the supervisor at once; this is only how often it samples the
# worker's memory and looks for a cancel or deadline while the worker is quiet.
# The worker's own address-space limit is the hard ceiling in between.
_POLL_SECONDS = 0.1
# After the reply ends, only the exit status is awaited.
_REAP_POLL_SECONDS = 0.01


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
    return native_process.native_capacity().bytes


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


def encode_reply(result: ThumbnailResult) -> bytes:
    image = result.image or b""
    fingerprint = result.fingerprint_result
    header = json.dumps(
        {
            "phase_stats": encode_phase_stats(result.phase_stats),
            "geometry": result.geometry,
            "volume": encode_volume(result.volume),
            "geometry_outcome": encode_geometry(result.geometry_outcome),
            "strategy": result.strategy.value,
            "coverage": encode_coverage(result.coverage),
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
                    "state": fingerprint.state.value,
                    "failure_code": (
                        fingerprint.failure_code.value
                        if fingerprint.failure_code is not None
                        else None
                    ),
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
                state=FingerprintResultState(raw["state"]),
                failure_code=(
                    None
                    if raw["failure_code"] is None
                    else FingerprintFailureCode(raw["failure_code"])
                ),
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
            volume=decode_volume(header["volume"]),
            geometry_outcome=decode_geometry(header["geometry_outcome"]),
            strategy=ThumbnailStrategy(header["strategy"]),
            coverage=decode_coverage(header["coverage"]),
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


class _CallbackWatchdog:
    """Bound native activity while a synchronous publication callback blocks.

    The monitor has no cancellation probe or application/database context. Main
    thread polling/reaping shares the lifecycle lock: the monitor cannot signal
    a process group after its root PID has been reaped and reused. Cancellation
    and publication callbacks remain cooperative on the calling thread.
    """

    def __init__(
        self,
        pid: int,
        deadline: float,
        memory_budget: int,
        lifecycle: WorkerLifecycle = WorkerLifecycle.PROCESS_GROUP,
    ) -> None:
        self.pid: Final[int] = pid
        self.deadline: Final[float] = deadline
        self.memory_budget: Final[int] = memory_budget
        self.worker_lifecycle: Final[WorkerLifecycle] = lifecycle
        self.stopped = Event()
        self.lifecycle = Lock()
        self.failure: WorkerExitCause | None = None
        self.peak_rss: int | None = None
        self.thread = Thread(
            target=self._monitor, name="mesh-output-watchdog", daemon=True
        )

    def _monitor(self) -> None:
        while not self.stopped.is_set():
            # Sampling outside the lifecycle lock lets cleanup withdraw the
            # monitor even if a filesystem read is delayed. Check withdrawal
            # again under the lock before recording a cause or signalling.
            rss = native_process.process_tree_rss_bytes(self.pid)
            with self.lifecycle:
                if self.stopped.is_set():
                    return
                if rss is not None:
                    self.peak_rss = (
                        rss if self.peak_rss is None else max(self.peak_rss, rss)
                    )
                cause = (
                    WorkerExitCause.DEADLINE
                    if time.monotonic() >= self.deadline
                    else WorkerExitCause.MEMORY_LIMIT
                    if rss is not None and rss > self.memory_budget
                    else None
                )
                if cause is not None:
                    self.failure = cause
                    try:
                        if (
                            self.worker_lifecycle is WorkerLifecycle.GUARDED
                            and sys.platform == "linux"
                        ):
                            # Leave the sentinel alive to drain escaped descendants.
                            os.kill(self.pid, signal.SIGKILL)
                        else:
                            os.killpg(self.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        self.failure = WorkerExitCause.SUPERVISION_FAILED
                    return
            self.stopped.wait(_POLL_SECONDS)

    def failure_cause(self) -> WorkerExitCause | None:
        with self.lifecycle:
            return self.failure

    def poll(self, process: subprocess.Popen[bytes]) -> int | None:
        with self.lifecycle:
            code = process.poll()
            if code is not None:
                self.stopped.set()
            return code

    def close(self) -> None:
        self.stopped.set()
        # A signal already in progress finishes before the caller reaps/reuses
        # the PID. A late sample observes withdrawal and never signals.
        with self.lifecycle:
            pass
        # Startup may fail before launch or be interrupted immediately after
        # launch. Withdrawal is always safe; joining a never-started thread is not.
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)


def _watchdog_error(cause: WorkerExitCause) -> MeshWorkerError:
    return MeshWorkerError(
        ThumbnailFailureReason.TIMEOUT
        if cause is WorkerExitCause.DEADLINE
        else ThumbnailFailureReason.RESOURCE_LIMIT
        if cause is WorkerExitCause.MEMORY_LIMIT
        else ThumbnailFailureReason.WORKER_FAILED
    )


def supervise_result(
    command: list[str],
    *,
    memory_budget: int,
    timeout_seconds: float,
    permit: NativePermit | None = None,
    lifecycle: WorkerLifecycle = WorkerLifecycle.PROCESS_GROUP,
    accepted_exit_codes: frozenset[int] = frozenset({0}),
    temporary_directory: Path | None = None,
    environment: dict[str, str] | None = None,
    reply_limit: int = MAX_REPLY_BYTES,
    withdrawn: Callable[[], bool] | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
) -> SupervisedReply:
    """Own the process lifecycle and retain observed costs on every exit.

    Tree RSS is sampled, not a kernel high-water mark. None means no sample was
    available. Staged callbacks retain an independent deadline/RSS monitor;
    callbacks themselves and cancellation remain cooperative on this thread.
    No terminal frame can establish the active phase of a killed child.
    """
    if type(reply_limit) is not int or not 0 < reply_limit <= MAX_REPLY_BYTES:
        raise ValueError("invalid worker reply limit")
    if not accepted_exit_codes or any(
        type(code) is not int or not 0 <= code <= 255 or code == RESOURCE_EXIT
        for code in accepted_exit_codes
    ):
        raise ValueError("accepted worker exit codes must be non-resource status codes")
    started = time.monotonic_ns()
    execution_id = secrets.token_hex(16)
    peak_rss: int | None = None
    reply = bytearray()
    cause = WorkerExitCause.SPAWN_FAILED
    error: MeshWorkerError | None = None
    cancelled: OperationCancelled | None = None
    process: subprocess.Popen[bytes] | None = None
    watchdog: _CallbackWatchdog | None = None
    temporary = (
        tempfile.TemporaryDirectory(prefix="printstash-mesh-")
        if temporary_directory is None
        else None
    )
    temporary_path = (
        Path(temporary.name) if temporary is not None else temporary_directory
    )
    assert temporary_path is not None
    if not temporary_path.is_dir():
        raise ValueError("worker temporary directory must exist")
    from app.core.work_priority import current_priority

    inherited = launch_resources(permit) if permit is not None else None
    try:
        try:
            process = subprocess.Popen(  # nosec B603 - fixed argv; no shell
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=inherited.descriptors if inherited is not None else (),
                env={
                    **os.environ,
                    **(environment if environment is not None else {}),
                    "OMP_NUM_THREADS": "1",
                    "OPENBLAS_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "NUMEXPR_NUM_THREADS": "1",
                    "TMPDIR": str(temporary_path),
                    PRIORITY_ENV: current_priority().value,
                    **(inherited.environment if inherited is not None else {}),
                },
                cwd=Path(application_file).resolve().parent.parent,
            )
        except OSError as exc:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
        cause = WorkerExitCause.SUPERVISION_FAILED
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        deadline = time.monotonic() + timeout_seconds
        if on_chunk is not None:
            watchdog = _CallbackWatchdog(
                process.pid, deadline, memory_budget, lifecycle
            )
            watchdog.thread.start()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            stdout_closed = False
            while True:
                checkpoint()
                if withdrawn is not None and withdrawn():
                    raise OperationCancelled()
                monitored_cause = (
                    watchdog.failure_cause() if watchdog is not None else None
                )
                if monitored_cause is not None:
                    cause = monitored_cause
                    raise _watchdog_error(cause)
                if time.monotonic() >= deadline:
                    cause = WorkerExitCause.DEADLINE
                    raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)
                # Staged publication already has an independent RSS monitor.
                # Keep a single sampler for that tree, including while callbacks
                # block; duplicate /proc walks only contend with useful work.
                if watchdog is None:
                    rss = native_process.process_tree_rss_bytes(process.pid)
                    if rss is not None:
                        peak_rss = rss if peak_rss is None else max(peak_rss, rss)
                        if rss > memory_budget:
                            cause = WorkerExitCause.MEMORY_LIMIT
                            raise MeshWorkerError(ThumbnailFailureReason.RESOURCE_LIMIT)
                for key, _ in selector.select(
                    _REAP_POLL_SECONDS if stdout_closed else _POLL_SECONDS
                ):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        stdout_closed = True
                        selector.unregister(process.stdout)
                        break
                    reply.extend(chunk)
                    if len(reply) > reply_limit:
                        cause = WorkerExitCause.REPLY_LIMIT
                        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
                    if on_chunk is not None:
                        on_chunk(chunk)
                        checkpoint(force=True)
                        if withdrawn is not None and withdrawn():
                            raise OperationCancelled()
                        monitored_cause = (
                            watchdog.failure_cause() if watchdog is not None else None
                        )
                        if monitored_cause is not None:
                            cause = monitored_cause
                            raise _watchdog_error(cause)
                        if time.monotonic() >= deadline:
                            cause = WorkerExitCause.DEADLINE
                            raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)
                if stdout_closed:
                    code = (
                        watchdog.poll(process)
                        if watchdog is not None
                        else process.poll()
                    )
                    if code is not None:
                        break
        # EOF only ends the reply: native work may continue after closing stdout.
        # Polling above keeps resource and cancellation checks active until exit.
        if watchdog is not None:
            watchdog.close()
            monitored_cause = watchdog.failure_cause()
            if monitored_cause is not None:
                cause = monitored_cause
                raise _watchdog_error(cause)
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
        if code not in accepted_exit_codes:
            cause = WorkerExitCause.EXITED_NONZERO
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        checkpoint(force=True)
        if withdrawn is not None and withdrawn():
            raise OperationCancelled()
        cause = (
            WorkerExitCause.EXITED_ZERO if code == 0 else WorkerExitCause.EXITED_NONZERO
        )
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
        if watchdog is not None:
            watchdog.close()
            monitored_cause = watchdog.failure_cause()
            if (
                monitored_cause is not None
                and cause is WorkerExitCause.SUPERVISION_FAILED
            ):
                # A callback exception remains the exception propagated to its
                # owner; observed native termination still keeps its true cost.
                cause = monitored_cause
            observed_rss = watchdog.peak_rss
            if observed_rss is not None:
                peak_rss = (
                    observed_rss if peak_rss is None else max(peak_rss, observed_rss)
                )
        try:
            if process is not None:
                terminate_worker(process, lifecycle)
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
                if temporary is not None:
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
            from app.modules.media.mesh_observability import record_supervision

            record_supervision(stats)
    if cancelled is not None:
        raise MeshWorkerCancelled(stats) from cancelled
    if error is not None:
        raise error
    assert process is not None and process.returncode is not None
    return SupervisedReply(bytes(reply), stats, process.returncode)


def supervise(
    command: list[str],
    *,
    memory_budget: int,
    timeout_seconds: float,
    lifecycle: WorkerLifecycle = WorkerLifecycle.PROCESS_GROUP,
) -> bytes:
    """Byte-only convenience retained for non-mesh workers and containment probes."""
    return supervise_result(
        command,
        memory_budget=memory_budget,
        timeout_seconds=timeout_seconds,
        lifecycle=lifecycle,
    ).payload


def runtime_overrides() -> dict[str, str | int | float | bool]:
    """The API process's scalar runtime overrides, for the worker to adopt.

    Administrators change some settings at runtime and tests change others; both
    live in an in-process overlay a new process cannot see. Without this the
    worker would apply the environment's defaults to a request the parent
    configured differently. Only scalars cross: every setting the worker reads
    is one.
    """
    from app.core.config import _overlay

    return {
        key: value
        for key, value in dict(_overlay).items()
        if isinstance(value, str | int | float | bool)
    }


ERROR_MAGIC = b"ERR1"


def encode_error(error: GeometryError) -> bytes:
    """The reply a worker sends for a failure that owns a stable code."""
    return ERROR_MAGIC + FingerprintFailureCode(error.code).value.encode("ascii")


def raise_reported_error(payload: bytes) -> None:
    """Raise the failure a worker reported; return if *payload* reports none.

    Only a known closed cause may cross the native worker boundary.
    """
    if not payload.startswith(ERROR_MAGIC):
        return
    try:
        code = FingerprintFailureCode(payload[len(ERROR_MAGIC) :].decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
    raise GeometryError(code.value)


def absolute(path: Path) -> str:
    """*path* as the worker must name it.

    Storage may hand back a path relative to this process's working directory;
    the worker starts elsewhere, where a relative path would name nothing.
    """
    return str(path.absolute())


def _run_worker(
    module: str,
    spec: dict[str, Any],
    *,
    sources: tuple[MeshSource, ...],
    work: WorkProfile,
    on_chunk: Callable[[bytes], None] | None = None,
    deadline: float | None = None,
) -> SupervisedReply:
    """Plan a weighted native allowance, then run one supervised worker."""
    from app.runtime.compute.client import warm_render

    def check() -> None:
        checkpoint()
        if deadline is not None and time.monotonic() >= deadline:
            raise MeshWorkerError(ThumbnailFailureReason.TIMEOUT)

    check()
    if isinstance(work, (RasterWork, AnalysisWork)):
        warm_render()
        check()
    existing = current_permit()
    capacity = Resources(native_process.native_capacity().slots, memory_budget_bytes())
    amount = (
        existing.resources
        if existing is not None
        else estimate_sources(capacity, sources, work=work)
    )
    with admission(amount, capacity, checkpoint=check) as permit:
        check()
        if module == "app.modules.media.mesh_worker":
            from app.core.native_settings import capture

            payload = {**spec, "native_settings": capture()}
        else:
            payload = {"overrides": runtime_overrides(), **spec}
        command = worker_command(
            module,
            [json.dumps(payload)],
            permit.resources.bytes,
        )
        return supervise_result(
            command,
            memory_budget=permit.resources.bytes,
            timeout_seconds=(
                float(settings.mesh_worker_timeout_seconds)
                if deadline is None
                else max(0.001, deadline - time.monotonic())
            ),
            permit=permit,
            lifecycle=WorkerLifecycle.GUARDED,
            on_chunk=on_chunk,
        )


@contextmanager
def worker_output_directory(workspace: Path) -> Iterator[Path]:
    """Own a staged output directory without deleting a replaced name."""
    directory = Path(tempfile.mkdtemp(prefix="printstash-mesh-", dir=workspace))
    owned = directory.lstat()
    identity = (owned.st_dev, owned.st_ino)
    try:
        yield directory
    finally:
        try:
            current = directory.lstat()
        except FileNotFoundError:
            current = None
        if (
            current is not None
            and stat.S_ISDIR(current.st_mode)
            and (current.st_dev, current.st_ino) == identity
        ):
            shutil.rmtree(directory)


@contextmanager
def prepared_worker_result(
    module: str,
    spec: dict[str, Any],
    *,
    workspace: Path,
    sources: tuple[MeshSource, ...],
    work: WorkProfile,
    reply_limit: int = MAX_REPLY_BYTES,
) -> Iterator[tuple[SupervisedReply, Path]]:
    """Supervise native work, then retain staged custody through publication.

    Output bytes and disk capacity are reserved by the caller before this
    workspace is used. Native credits are released after supervised exit and
    descendant cleanup; the caller retains output bytes and disk reservations.
    """
    existing = current_permit()
    capacity = Resources(native_process.native_capacity().slots, memory_budget_bytes())
    amount = (
        existing.resources
        if existing is not None
        else estimate_sources(capacity, sources, work=work)
    )
    with worker_output_directory(workspace) as directory:
        owned = directory.lstat()
        identity = (owned.st_dev, owned.st_ino)
        with admission(amount, capacity, checkpoint=checkpoint) as permit:
            command = worker_command(
                module,
                [
                    json.dumps(
                        {
                            "overrides": runtime_overrides(),
                            **spec,
                            "output_directory": str(directory),
                        }
                    )
                ],
                permit.resources.bytes,
            )
            reply = supervise_result(
                command,
                memory_budget=permit.resources.bytes,
                timeout_seconds=float(settings.mesh_worker_timeout_seconds),
                permit=permit,
                lifecycle=WorkerLifecycle.GUARDED,
                temporary_directory=directory,
                reply_limit=reply_limit,
            )
        try:
            current = directory.lstat()
        except OSError as exc:
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
        yield reply, directory


def run_worker(
    module: str,
    spec: dict[str, Any],
    *,
    sources: tuple[MeshSource, ...],
    work: WorkProfile,
) -> bytes:
    """Byte reply convenience for STL conversion, embedding and verification."""
    return _run_worker(module, spec, sources=sources, work=work).payload


def read_spec(argv: list[str]) -> dict[str, Any]:
    """The request a worker was started with, after adopting the parent's overrides."""
    (raw,) = argv
    spec = json.loads(raw)
    from app.core.native_settings import installed

    if not installed():
        from app.core.config import _overlay

        _overlay.update(spec["overrides"])
    return spec


def generate(
    request: ThumbnailRequest,
    *,
    on_output: OutputSink | None = None,
    handoff: ThumbnailHandoff | None = None,
) -> ThumbnailResult:
    """`ThumbnailEngine.generate` for *request*, run in a supervised child."""
    decoder = FrameDecoder(request, prepared=handoff is not None)
    geometry_output: GeometryOutput | None = None
    thumbnail_output: ThumbnailOutput | None = None
    prepared_output: PreparedThumbnailOutput | None = None
    fallback_stats: SupervisionStats | None = None

    def receive(chunk: bytes) -> None:
        nonlocal geometry_output, thumbnail_output, prepared_output
        outputs = iter(decoder.feed(chunk))
        while True:
            try:
                output = next(outputs)
            except StopIteration:
                break
            except MeshProtocolError as exc:
                raise MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED) from exc
            if isinstance(output, GeometryOutput):
                geometry_output = output
            elif isinstance(output, ThumbnailOutput):
                thumbnail_output = output
            elif isinstance(output, PreparedThumbnailOutput):
                prepared_output = output
                continue
            else:
                continue
            if on_output is not None:
                on_output(output)

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
    raster: RasterWork | None = None
    if request.include_thumbnail:
        width = int(request.width or settings.model_thumbnail_width)
        height = int(request.height or round(width * 3 / 4))
        raster = RasterWork(
            width, height, 1, RasterCodec(request.output_format.lower())
        )
    work: WorkProfile = (
        AnalysisWork(raster)
        if request.include_fingerprint
        else raster
        if raster is not None
        else GeometryWork()
    )
    with ExitStack() as resources:
        directory: Path | None = None
        directory_identity: tuple[int, int] | None = None
        if handoff is not None:
            directory = resources.enter_context(
                worker_output_directory(handoff.workspace)
            )
            owned = directory.stat()
            directory_identity = (owned.st_dev, owned.st_ino)
            spec["prepare_directory"] = str(directory)
            spec["prepare_deadline"] = handoff.deadline
        if request.include_fingerprint and mesh_policy.canonical_suffix(
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
        run_options = {"deadline": handoff.deadline} if handoff is not None else {}
        reply = _run_worker(
            "app.modules.media.mesh_worker",
            spec,
            sources=(
                MeshSource(request.path, request.file_type or request.path.suffix),
            ),
            work=work,
            on_chunk=receive,
            **run_options,
        )
        try:
            final = decoder.finish()
            if prepared_output is not None:
                assert (
                    handoff is not None
                    and directory is not None
                    and directory_identity is not None
                )
                packet = prepared_output.packet

                def recover() -> bytes:
                    nonlocal fallback_stats
                    assert (
                        handoff is not None
                        and directory is not None
                        and directory_identity is not None
                    )
                    recovered = _render_packet_cpu(
                        packet, directory, directory_identity, handoff
                    )
                    fallback_stats = recovered.stats
                    return recovered.payload

                image = handoff.complete(
                    packet,
                    directory,
                    directory_identity,
                    fallback=recover,
                )
                peaks = [
                    value
                    for value in (
                        final.peak_rss_bytes,
                        reply.stats.peak_tree_rss_bytes,
                        fallback_stats.peak_tree_rss_bytes
                        if fallback_stats is not None
                        else None,
                    )
                    if value is not None
                ]
                peak_rss = max(peaks, default=None)
                from app.core.logging import get_logger

                get_logger(__name__).info(
                    "thumbnail_handoff_supervision %s",
                    json.dumps(
                        {
                            "event": "thumbnail_handoff_supervision",
                            "handoff_id": handoff.identity,
                            "parser_execution_id": reply.stats.execution_id,
                            "fallback_execution_id": fallback_stats.execution_id
                            if fallback_stats is not None
                            else None,
                            "peak_supervised_rss_bytes": peak_rss,
                        }
                    ),
                )
                coverage = replace(
                    prepared_output.coverage, preview=PreviewCoverage.COMPLETE
                )
                thumbnail_output = ThumbnailOutput(
                    image,
                    ThumbnailStrategy.FULL,
                    coverage,
                    None,
                    max(
                        round(
                            (
                                time.monotonic()
                                - handoff.deadline
                                + settings.mesh_worker_timeout_seconds
                            )
                            * 1000
                        ),
                        0,
                    ),
                    peak_rss,
                )
                final = replace(
                    final,
                    coverage=replace(final.coverage, preview=PreviewCoverage.COMPLETE),
                    duration_ms=thumbnail_output.duration_ms,
                    peak_rss_bytes=peak_rss,
                )
                if on_output is not None:
                    on_output(thumbnail_output)
            if geometry_output is None:
                from printstash_core.mesh.measurements import (
                    VolumeNotCalculated,
                    VolumeNotCalculatedCause,
                )

                geometry = MeshMeasurements.unavailable().geometry
                volume = VolumeNotCalculated(VolumeNotCalculatedCause.NOT_REQUESTED)
                outcome = GeometryNotRequested()
            else:
                geometry = geometry_output.geometry
                volume = geometry_output.volume
                outcome = geometry_output.outcome
            result = ThumbnailResult(
                image=thumbnail_output.image if thumbnail_output is not None else None,
                geometry=geometry,
                geometry_outcome=outcome,
                volume=volume,
                strategy=thumbnail_output.strategy
                if thumbnail_output is not None
                else ThumbnailStrategy.NONE,
                coverage=final.coverage,
                failure_reason=thumbnail_output.failure_reason
                if thumbnail_output is not None
                else None,
                duration_ms=final.duration_ms,
                peak_rss_bytes=final.peak_rss_bytes,
                fingerprint_result=final.fingerprint,
                phase_stats=final.phase_stats,
                supervision=reply.stats,
                fallback_supervision=fallback_stats,
            )
        except TimeoutError as exc:
            error = MeshWorkerError(ThumbnailFailureReason.TIMEOUT)
            error.supervision = reply.stats
            raise error from exc
        except MeshWorkerError as exc:
            if exc.supervision is None:
                exc.supervision = reply.stats
            raise
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            error = MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)
            error.supervision = reply.stats
            raise error from exc
        from app.modules.media.mesh_observability import record_phases

        record_phases(result.phase_stats, reply.stats)
        return result


def _render_packet_cpu(
    packet: PacketDescriptor,
    directory: Path,
    directory_identity: tuple[int, int],
    handoff: ThumbnailHandoff,
) -> SupervisedReply:
    """Recover one valid GPU refusal under the original deadline, without parsing."""
    from app.modules.media.native_budget import request as request_resources

    handoff.check(force=True)
    capacity = native_process.native_capacity()
    amount = request_resources(
        capacity,
        (packet.face_count,),
        work=RasterWork(handoff.plan.width, handoff.plan.height, 1, RasterCodec.WEBP),
    )
    with admission(amount, capacity, checkpoint=handoff.check) as permit:
        handoff.check(force=True)
        spec = {
            "overrides": {**runtime_overrides(), "compute_mode": "cpu"},
            "directory": str(directory),
            "directory_identity": directory_identity,
            "packet": asdict(packet),
            "width": handoff.plan.width,
            "height": handoff.plan.height,
        }
        reply = supervise_result(
            worker_command(
                "app.modules.media.thumbnail_cpu_worker",
                [json.dumps(spec)],
                permit.resources.bytes,
            ),
            memory_budget=permit.resources.bytes,
            timeout_seconds=max(0.001, handoff.deadline - time.monotonic()),
            permit=permit,
            lifecycle=WorkerLifecycle.GUARDED,
        )
        handoff.check(force=True)
        if not reply.payload:
            raise MeshWorkerError(ThumbnailFailureReason.RENDERER_NO_OUTPUT)
        return reply
