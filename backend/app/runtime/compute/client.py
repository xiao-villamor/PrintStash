"""Bounded broker client with deadline-preserving CPU fallback at the caller."""

import base64
import fcntl
import importlib.util
import json
import math
import os
import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.core.cancellation import checkpoint as cancellation_checkpoint
from app.core.config import settings

from .contracts import (
    Capability,
    ComputeMode,
    ComputeStatus,
    ComputeUnavailable,
    Operation,
    Reason,
)
from .discovery import runtime_identity
from .health import cooling
from .profile import identity as profile_identity
from .protocol import (
    MAX_FRAME,
    BinaryRenderRequest,
    InferenceRequest,
    Priority,
    StatusRequest,
    admit_control_response,
    receive,
    send_admitted,
    send_body,
)

ROOT_ENV = "PRINTSTASH_COMPUTE_ROOT"
_disabled_until = 0.0


def cooldown() -> None:
    global _disabled_until
    _disabled_until = time.monotonic() + 30


def bind(root: Path) -> None:
    os.environ[ROOT_ENV] = str(root / "runtime" / "compute")


def directory() -> Path | None:
    value = os.environ.get(ROOT_ENV)
    return Path(value) if value else None


def cpu_status(reason: Reason) -> ComputeStatus:
    return ComputeStatus(
        mode=ComputeMode(settings.compute_mode),
        device=None,
        runtime_identity=runtime_identity(),
        capabilities=[
            Capability(operation=op, available=False, reason=reason) for op in Operation
        ],
        budget_bytes=settings.compute_memory_mb * 1024**2,
        reserved_bytes=0,
        resident_entries=0,
        queue_depth=0,
        completed=0,
        fallbacks=0,
        cold_start_seconds=0,
        resident_model_ids=[],
        queue_seconds=0,
        execution_seconds=0,
        input_bytes=0,
        output_bytes=0,
        batch_inputs=0,
        residency_hits=0,
        residency_misses=0,
        host_reserved_bytes=0,
        queued_bytes=0,
        rendering_transfer_seconds=0,
        inference_batches=0,
    )


def available(*, render: bool = False) -> bool:
    root = directory()
    return (
        settings.compute_mode == "auto"
        and time.monotonic() >= _disabled_until
        and root is not None
        and not cooling(root)
        and (
            (root / "qualification.json").is_file()
            or (render and settings.compute_render_policy == "preview")
        )
        and importlib.util.find_spec("wgpu") is not None
    )


def warm_render() -> None:
    """Start ownership in the supervisor, outside disposable parser limits.

    Status is a bounded private IPC request; the API/worker never imports a GPU
    driver. Reuse the elected owner across the whole import, including after an
    idle owner has exited. Failed startup leaves the normal CPU path available.
    """
    if available(render=True):
        state = status()
        if state.device is None:
            cooldown()


def _connect(
    root: Path, deadline: float, *, start: bool = True, checkpoint=lambda: None
) -> socket.socket:
    checkpoint()
    address = root / "broker.sock"
    if len(os.fsencode(address)) >= 104:
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        root.is_symlink()
        or root.stat().st_uid != os.getuid()
        or root.stat().st_mode & 0o077
    ):
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)

    def checked(connection):
        try:
            if (root / "profile").read_text(encoding="ascii") == profile_identity():
                return connection
        except OSError:
            pass
        connection.close()
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(max(0.001, min(0.2, deadline - time.monotonic())))
    try:
        connection.connect(str(address))
        checkpoint()
        return checked(connection)
    except OSError:
        connection.close()
    except BaseException:
        connection.close()
        raise
    if not start:
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
    with (root / "launch.lock").open("a+b") as launch:
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise ComputeUnavailable(Reason.DEADLINE)
            try:
                fcntl.flock(launch, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ComputeUnavailable(Reason.DEADLINE) from None
                time.sleep(0.025)
        child = None
        while time.monotonic() < deadline:
            checkpoint()
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(max(0.001, min(0.2, deadline - time.monotonic())))
            try:
                connection.connect(str(address))
                checkpoint()
                return checked(connection)
            except OSError:
                connection.close()
            except BaseException:
                connection.close()
                raise
            if child is None:
                env = os.environ.copy()
                env.update(
                    VAULT_COMPUTE_MODE=settings.compute_mode,
                    VAULT_COMPUTE_BACKEND=settings.compute_backend,
                    VAULT_COMPUTE_RENDER_POLICY=settings.compute_render_policy,
                    VAULT_COMPUTE_MEMORY_MB=str(settings.compute_memory_mb),
                    VAULT_COMPUTE_BATCH_WAIT_MS=str(settings.compute_batch_wait_ms),
                    VAULT_EMBEDDING_WORKER_MEMORY_MB=str(
                        settings.embedding_worker_memory_mb
                    ),
                    VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION=str(
                        settings.embedding_memory_budget_fraction
                    ),
                    VAULT_MESH_MEMORY_BUDGET_FRACTION=str(
                        settings.mesh_memory_budget_fraction
                    ),
                    VAULT_EMBEDDING_RESIDENT_WORKERS=str(
                        settings.embedding_resident_workers
                    ),
                )
                if settings.compute_adapter is not None:
                    env["VAULT_COMPUTE_ADAPTER"] = settings.compute_adapter
                else:
                    env.pop("VAULT_COMPUTE_ADAPTER", None)
                child = subprocess.Popen(
                    [sys.executable, "-m", "app.runtime.compute.broker", str(root)],
                    cwd=Path(__file__).resolve().parents[3],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    start_new_session=True,
                )
            if child.poll() is not None:
                raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
            time.sleep(0.025)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    raise ComputeUnavailable(Reason.DEADLINE)


def exchange(
    request,
    *,
    checkpoint=lambda: None,
    start: bool | None = None,
    deadline: float | None = None,
) -> bytes:
    root = directory()
    if root is None:
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
    request_deadline: float = getattr(request, "deadline", time.monotonic() + 5)
    deadline = request_deadline if deadline is None else min(deadline, request_deadline)
    checkpoint()
    try:
        if start is None:
            start = (
                isinstance(request, StatusRequest)
                or request.priority == Priority.BACKGROUND
            )
        with _connect(
            root,
            min(deadline, time.monotonic() + 30),
            start=start,
            checkpoint=checkpoint,
        ) as connection:
            checkpoint()
            connection.settimeout(max(0.001, min(0.05, deadline - time.monotonic())))
            send_admitted(
                connection,
                request.model_dump_json().encode(),
                deadline,
                checkpoint=checkpoint,
            )
            # Poll readiness so cancellation is checked while the device executes.
            import select

            while not select.select(
                [connection], [], [], max(0, min(0.05, deadline - time.monotonic()))
            )[0]:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise ComputeUnavailable(Reason.DEADLINE)
            result = receive(connection, deadline, checkpoint=checkpoint)
            checkpoint()
            return result
    except OSError:
        checkpoint()
        cooldown()
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE) from None


def status(
    *,
    start: bool = True,
    deadline: float | None = None,
    checkpoint: Callable[[], None] = cancellation_checkpoint,
) -> ComputeStatus:
    def remaining() -> None:
        checkpoint()
        if deadline is not None and (
            not math.isfinite(deadline) or time.monotonic() >= deadline
        ):
            raise TimeoutError("compute_deadline")

    remaining()
    if settings.compute_mode == "cpu":
        return cpu_status(Reason.DISABLED)
    if importlib.util.find_spec("wgpu") is None:
        return cpu_status(Reason.RUNTIME_MISSING)
    root = directory()
    if root is not None and cooling(root):
        return cpu_status(Reason.DEVICE_FAILED)
    if root is None:
        return cpu_status(Reason.BROKER_UNAVAILABLE)
    try:
        state = ComputeStatus.model_validate_json(
            exchange(
                StatusRequest(), start=start, deadline=deadline, checkpoint=remaining
            )
        )
    except ComputeUnavailable, ValueError:
        remaining()
        return cpu_status(Reason.BROKER_UNAVAILABLE)
    remaining()
    return state


def render_ready(
    *,
    recipe: str,
    units: int | None = None,
    deadline: float | None = None,
    checkpoint: Callable[[], None] = cancellation_checkpoint,
) -> bool:
    """Only an already qualified final-pixel owner can accept parser handoff.

    Unknown units are used before parsing to require a valid recipe interval;
    the parser repeats this check with its actual face count. The broker retains
    final authority after a restart or receipt change. A cold owner stays on the
    combined path, which can initialize rendering outside this readiness probe.
    """

    def remaining() -> None:
        checkpoint()
        if deadline is not None and (
            not math.isfinite(deadline) or time.monotonic() >= deadline
        ):
            raise TimeoutError("compute_deadline")

    remaining()
    if units is not None and (type(units) is not int or units < 1):
        raise ValueError("compute_render_units")
    if not available(render=True):
        remaining()
        return False
    state = status(start=False, deadline=deadline, checkpoint=remaining)
    if (
        state.mode is not ComputeMode.AUTO
        or state.device is None
        or not state.render_postprocess_gpu
        or not any(
            capability.operation is Operation.RENDER and capability.available
            for capability in state.capabilities
        )
    ):
        return False
    if settings.compute_render_policy == "preview":
        return True
    from .qualification import read_receipts

    root = directory()
    if root is None:
        return False
    accepted = any(
        receipt.accepts(
            state.device,
            state.runtime_identity,
            Operation.RENDER,
            recipe,
            receipt.minimum_units if units is None else units,
        )
        for receipt in read_receipts(root / "qualification.json")
    )
    remaining()
    return accepted


def infer(
    directory: Path, model_key: str, threads: int, payload: bytes, context
) -> bytes | None:
    if not available():
        return None
    request = InferenceRequest(
        deadline=time.monotonic() + min(context.remaining(), 90),
        priority=Priority.INTERACTIVE
        if context.priority == "interactive"
        else Priority.BACKGROUND,
        request_id=uuid4().hex,
        directory=str(directory),
        model_key=model_key,
        threads=threads,
        payload=base64.b64encode(payload).decode(),
    )
    try:
        result = json.loads(exchange(request, checkpoint=context.remaining))
        if "error" in result:
            reason = Reason(result["error"])
            if reason in (Reason.DEVICE_FAILED, Reason.BROKER_UNAVAILABLE):
                cooldown()
            raise ComputeUnavailable(reason)
        return base64.b64decode(result["result"], validate=True)
    except ComputeUnavailable, ValueError, KeyError:
        context.remaining()
        return None


def is_warm(identity: str) -> bool:
    return available() and identity in status().resident_model_ids


def exchange_render(
    request: BinaryRenderRequest,
    body: bytes | memoryview,
    *,
    checkpoint=lambda: None,
    response_limit: int = MAX_FRAME,
) -> bytes:
    """Negotiate a content reference on every connection, including after restart."""
    if type(response_limit) is not int or not 0 < response_limit <= MAX_FRAME:
        raise ValueError("compute_render_response_limit")

    def admit_result(length: int) -> None:
        if length > response_limit:
            raise ComputeUnavailable(Reason.INVALID_INPUT)

    root = directory()
    if root is None:
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
    checkpoint()
    try:
        with _connect(
            root,
            min(request.deadline, time.monotonic() + 30),
            start=request.priority == Priority.BACKGROUND,
            checkpoint=checkpoint,
        ) as connection:
            connection.settimeout(0.05)
            send_admitted(
                connection,
                request.model_dump_json().encode(),
                request.deadline,
                checkpoint=checkpoint,
            )
            response = json.loads(
                receive(
                    connection,
                    request.deadline,
                    checkpoint=checkpoint,
                    admit=admit_control_response,
                )
            )
            if not isinstance(response, dict):
                raise ComputeUnavailable(Reason.INVALID_INPUT)
            if "error" in response:
                raise ComputeUnavailable(Reason(response["error"]))
            if response == {"geometry": "upload"}:
                send_body(
                    connection,
                    struct.pack("!I", len(body)),
                    request.deadline,
                    checkpoint=checkpoint,
                )
                send_body(connection, body, request.deadline, checkpoint=checkpoint)
            elif response != {"geometry": "cached"}:
                raise ComputeUnavailable(Reason.INVALID_INPUT)
            result = receive(
                connection, request.deadline, checkpoint=checkpoint, admit=admit_result
            )
            checkpoint()
            if result.startswith(b"\x00"):
                return result[1:]
            error = json.loads(result[1:] if result.startswith(b"\x01") else result)
            raise ComputeUnavailable(Reason(error["error"]))
    except OSError:
        cooldown()
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE) from None
