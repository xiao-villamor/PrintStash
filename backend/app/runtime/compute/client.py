"""Bounded broker client with deadline-preserving CPU fallback at the caller."""

import base64
import fcntl
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

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
from .protocol import InferenceRequest, Priority, StatusRequest, receive, send

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


def available() -> bool:
    root = directory()
    return (
        settings.compute_mode == "auto"
        and time.monotonic() >= _disabled_until
        and root is not None
        and not cooling(root)
        and (root / "qualification.json").is_file()
        and importlib.util.find_spec("wgpu") is not None
    )


def _connect(root: Path, deadline: float, *, start: bool = True) -> socket.socket:
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
    connection.settimeout(0.2)
    try:
        connection.connect(str(address))
        return checked(connection)
    except OSError:
        connection.close()
    if not start:
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
    with (root / "launch.lock").open("a+b") as launch:
        while True:
            try:
                fcntl.flock(launch, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ComputeUnavailable(Reason.DEADLINE) from None
                time.sleep(0.025)
        child = None
        while time.monotonic() < deadline:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(0.2)
            try:
                connection.connect(str(address))
                return checked(connection)
            except OSError:
                connection.close()
            if child is None:
                env = os.environ.copy()
                env.update(
                    VAULT_COMPUTE_MODE=settings.compute_mode,
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


def exchange(request, *, checkpoint=lambda: None) -> bytes:
    root = directory()
    if root is None:
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE)
    deadline = getattr(request, "deadline", time.monotonic() + 5)
    checkpoint()
    try:
        start = (
            isinstance(request, StatusRequest)
            or request.priority == Priority.BACKGROUND
        )
        with _connect(
            root, min(deadline, time.monotonic() + 30), start=start
        ) as connection:
            checkpoint()
            connection.settimeout(max(0.001, min(1, deadline - time.monotonic())))
            send(connection, request.model_dump_json().encode())
            connection.settimeout(0.05)
            # Poll readiness so cancellation is checked while the device executes.
            import select

            while not select.select([connection], [], [], 0.05)[0]:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise ComputeUnavailable(Reason.DEADLINE)
            result = receive(connection, deadline, checkpoint=checkpoint)
            checkpoint()
            return result
    except OSError:
        cooldown()
        raise ComputeUnavailable(Reason.BROKER_UNAVAILABLE) from None


def status() -> ComputeStatus:
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
        return ComputeStatus.model_validate_json(exchange(StatusRequest()))
    except ComputeUnavailable, ValueError:
        return cpu_status(Reason.BROKER_UNAVAILABLE)


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
