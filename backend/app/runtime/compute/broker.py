"""Private host-local compute owner; transient requests never replace Jobs."""

import base64
import fcntl
import os
import queue
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from printstash_core.inference import EmbeddingError
from pydantic import ValidationError

from app.core.config import settings
from app.modules.media.native_process import process_rss_bytes

from .batching import merge
from .budget import QueueBudget
from .contracts import ComputeMode, ComputeUnavailable, Reason
from .dispatcher import Dispatcher
from .health import failed
from .profile import identity as profile_identity
from .protocol import (
    BinaryRenderRequest,
    InferenceRequest,
    Priority,
    RenderRequest,
    StatusRequest,
    encode,
    peer_disconnected,
    receive,
    request_type,
    send,
    send_body,
    valid_deadline,
)


@dataclass
class Ticket:
    request: InferenceRequest | RenderRequest | BinaryRenderRequest
    received: float
    ready: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: bytes | None = None


def error(reason: Reason) -> bytes:
    return encode({"error": reason.value})


def serve(root: Path, *, dispatcher_factory=Dispatcher) -> None:
    """The owner flock lives until all GPU state has been destroyed."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        root.is_symlink()
        or root.stat().st_uid != os.getuid()
        or root.stat().st_mode & 0o077
    ):
        raise ValueError("compute_private_directory_required")
    with (root / "owner.lock").open("a+b") as ownership:
        try:
            fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        address = root / "broker.sock"
        address.unlink(missing_ok=True)
        (root / "profile").write_text(profile_identity(), encoding="ascii")
        pending: queue.Queue[Ticket] = queue.Queue(maxsize=16)
        stop = threading.Event()
        slots = threading.BoundedSemaphore(16)
        # Three copies cover framing, JSON decoding and base64 staging. Credits
        # live until execution releases the ticket, even after client cancellation.
        from .geometry_cache import INPUT_QUEUE_BYTES, OUTPUT_QUEUE_BYTES

        queue_budget = QueueBudget(INPUT_QUEUE_BYTES)
        output_budget = QueueBudget(OUTPUT_QUEUE_BYTES)
        handlers = []
        dispatcher = dispatcher_factory(
            root,
            mode=ComputeMode(settings.compute_mode),
            selector=settings.compute_adapter,
            budget_bytes=settings.compute_memory_mb * 1024**2,
        )
        last_used = time.monotonic()
        waiting_count = 0

        def scheduler() -> None:
            nonlocal last_used, waiting_count
            waiting = []
            while not stop.is_set():
                if not waiting:
                    try:
                        waiting.append(pending.get(timeout=0.05))
                    except queue.Empty:
                        pass
                while not pending.empty():
                    waiting.append(pending.get_nowait())
                now = time.monotonic()
                waiting.sort(
                    key=lambda t: (
                        t.request.priority != Priority.INTERACTIVE
                        and now - t.received < 30,
                        t.received,
                    )
                )
                if not waiting:
                    dispatcher.memory.expire(now)
                    if dispatcher.pressure():
                        stop.set()
                    continue
                ticket = waiting.pop(0)
                waiting_count = len(waiting)
                dispatcher.queue_seconds += max(0, now - ticket.received)
                group = [ticket]
                batch = None
                if (
                    isinstance(ticket.request, InferenceRequest)
                    and not ticket.cancelled.is_set()
                ):
                    # The clock begins when the first READY ticket arrived.
                    # Parsing never happens in this accumulation window.
                    flush_at = min(
                        ticket.received + settings.compute_batch_wait_ms / 1000,
                        ticket.request.deadline,
                    )
                    preempted = False
                    if ticket.request.priority == Priority.BACKGROUND:
                        while time.monotonic() < flush_at:
                            try:
                                arrival = pending.get(
                                    timeout=max(0.001, flush_at - time.monotonic())
                                )
                                waiting.append(arrival)
                                if arrival.request.priority == Priority.INTERACTIVE:
                                    waiting.append(ticket)
                                    preempted = True
                                    break
                            except queue.Empty:
                                break
                    if preempted:
                        waiting_count = len(waiting)
                        continue
                    for candidate in list(waiting):
                        if candidate.cancelled.is_set() or not isinstance(
                            candidate.request, InferenceRequest
                        ):
                            continue
                        if candidate.request.deadline <= time.monotonic():
                            continue
                        try:
                            combined = merge(
                                [member.request for member in (*group, candidate)]
                            )
                        except ValueError:
                            combined = None
                        if combined is not None:
                            group.append(candidate)
                            waiting.remove(candidate)
                            batch = combined

                # Only independently admitted, already-prepared render tickets
                # may coalesce. Interactive work never waits for a batch.
                render_group = False
                if (
                    isinstance(ticket.request, (RenderRequest, BinaryRenderRequest))
                    and ticket.request.priority == Priority.BACKGROUND
                    and not ticket.cancelled.is_set()
                ):
                    flush_at = min(
                        ticket.received + settings.compute_batch_wait_ms / 1000,
                        ticket.request.deadline,
                    )
                    while time.monotonic() < flush_at:
                        try:
                            arrival = pending.get(
                                timeout=max(0.001, flush_at - time.monotonic())
                            )
                            waiting.append(arrival)
                            if arrival.request.priority == Priority.INTERACTIVE:
                                break
                        except queue.Empty:
                            break
                    if time.monotonic() - ticket.received < 30 and any(
                        t.request.priority == Priority.INTERACTIVE for t in waiting
                    ):
                        waiting.append(ticket)
                        continue
                    for candidate in list(waiting):
                        if len(group) >= 8:
                            break
                        if (
                            isinstance(candidate.request, type(ticket.request))
                            and candidate.request.priority == Priority.BACKGROUND
                            and candidate.request.recipe == ticket.request.recipe
                            and not candidate.cancelled.is_set()
                            and candidate.request.deadline > time.monotonic()
                        ):
                            group.append(candidate)
                            waiting.remove(candidate)
                    render_group = len(group) > 1

                waiting_count = len(waiting)

                def encode_output(member, result):
                    if isinstance(member.request, BinaryRenderRequest):
                        return b"\x00" + result
                    return encode({"result": base64.b64encode(result).decode()})

                def execute_one(member):
                    if member.cancelled.is_set():
                        return error(Reason.CANCELLED)
                    if not valid_deadline(member.request.deadline, time.monotonic()):
                        return error(Reason.DEADLINE)
                    try:
                        result = dispatcher.execute(member.request)
                        return encode_output(member, result)
                    except ComputeUnavailable as exc:
                        dispatcher.fallbacks += 1
                        return error(exc.reason)
                    except EmbeddingError as exc:
                        return encode(
                            {
                                "result": base64.b64encode(
                                    encode({"code": exc.code})
                                ).decode()
                            }
                        )
                    except ValueError, ValidationError:
                        return error(Reason.INVALID_INPUT)

                try:
                    if render_group:
                        try:
                            outputs = dispatcher.execute_render_batch(
                                [member.request for member in group]
                            )
                            for member, output in zip(group, outputs, strict=True):
                                member.result = (
                                    b"\x00" + output
                                    if isinstance(member.request, BinaryRenderRequest)
                                    else encode(
                                        {"result": base64.b64encode(output).decode()}
                                    )
                                )
                        except ValueError, ComputeUnavailable:
                            # Re-admit each member independently after a batch budget
                            # refusal/invalid member; preserve each original deadline.
                            for member in group:
                                member.result = execute_one(member)
                    elif batch is None:
                        ticket.result = execute_one(ticket)
                    else:
                        try:
                            result = dispatcher.execute(batch.request)
                            outputs = batch.split(result)
                            for member, output in zip(group, outputs, strict=True):
                                member.result = (
                                    b"\x00" + output
                                    if isinstance(member.request, BinaryRenderRequest)
                                    else encode(
                                        {"result": base64.b64encode(output).decode()}
                                    )
                                )
                        except EmbeddingError, ValueError, ComputeUnavailable:
                            # Fault isolation belongs to original ticket boundaries;
                            # a bad member must not fail another Artifact.
                            for member in group:
                                member.result = execute_one(member)
                except MemoryError, RuntimeError, OSError:
                    for member in group:
                        if member.result is None:
                            member.result = error(Reason.DEVICE_FAILED)
                    failed(root)
                    stop.set()
                except ValueError, ValidationError:
                    for member in group:
                        if member.result is None:
                            member.result = error(Reason.INVALID_INPUT)
                except Exception:
                    # Native runtimes expose different exception hierarchies.
                    # Never leave a dead scheduler behind a live listening socket.
                    for member in group:
                        if member.result is None:
                            member.result = error(Reason.DEVICE_FAILED)
                    failed(root)
                    stop.set()
                finally:
                    last_used = time.monotonic()
                    for member in group:
                        if (
                            member.cancelled.is_set()
                            or member.request.deadline <= last_used
                        ):
                            member.result = error(
                                Reason.CANCELLED
                                if member.cancelled.is_set()
                                else Reason.DEADLINE
                            )
                        member.ready.set()
            for ticket in waiting:
                ticket.result = error(Reason.BROKER_UNAVAILABLE)
                ticket.ready.set()

        executor = threading.Thread(
            target=scheduler, name="compute-device", daemon=True
        )
        executor.start()

        def handle(connection: socket.socket) -> None:
            nonlocal last_used
            ticket = None
            reserved = 0
            output_reserved = 0
            geometry_entry = None

            def admit(length):
                nonlocal reserved
                amount = 3 * length
                try:
                    queue_budget.reserve(amount)
                except ComputeUnavailable as exc:
                    send(connection, error(exc.reason))
                    raise
                reserved = amount
                send(connection, encode({"ready": True}))

            try:
                connection.settimeout(0.2)
                request = request_type.validate_json(
                    receive(connection, time.monotonic() + 5, admit=admit)
                )
                last_used = time.monotonic()
                if isinstance(request, StatusRequest):
                    send(
                        connection,
                        dispatcher.status(
                            pending.qsize() + waiting_count, queue_budget.used
                        )
                        .model_dump_json()
                        .encode(),
                    )
                    return
                if not valid_deadline(request.deadline, time.monotonic()):
                    send(connection, error(Reason.DEADLINE))
                    return
                if isinstance(request, BinaryRenderRequest):
                    from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

                    from app.modules.media.compute_geometry import (
                        decode_arrays,
                        decode_header,
                        identity,
                    )

                    counts, width, height, views, matte = decode_header(
                        request.header.encode()
                    )
                    n, f, k = counts
                    if request.geometry_bytes != n * 20 + f * 24 + k * 24:
                        raise ValueError("compute_geometry_size")
                    factor = PREVIEW_PROFILE.supersample_for(width)
                    output_reserved = 3 * (
                        12 + width * height * factor**2 * 4 * len(views)
                    )
                    # A rejected reserve must not be released in finally.
                    amount = output_reserved
                    output_reserved = 0
                    output_budget.reserve(amount)
                    output_reserved = amount
                    dispatcher.admit_host(min(request.deadline, time.monotonic() + 5))
                    geometry_entry = dispatcher.geometry_cache.acquire(
                        request.geometry_key, request.geometry_bytes, counts
                    )
                    if geometry_entry is None:
                        amount = 2 * request.geometry_bytes
                        queue_budget.reserve(amount)
                        reserved += amount
                        send(connection, encode({"geometry": "upload"}))

                        def check_length(length):
                            if length != request.geometry_bytes:
                                raise ValueError("compute_geometry_size")

                        body = receive(
                            connection,
                            min(request.deadline, time.monotonic() + 5),
                            admit=check_length,
                        )
                        if identity(counts, body) != request.geometry_key:
                            raise ValueError("compute_geometry_digest")
                        prepared = decode_arrays(body, counts)
                        geometry_entry = dispatcher.geometry_cache.insert(
                            request.geometry_key, len(body), counts, prepared
                        )
                    else:
                        send(connection, encode({"geometry": "cached"}))
                    request._decoded = (
                        geometry_entry.prepared,
                        width,
                        height,
                        views,
                        matte,
                    )
                ticket = Ticket(request, time.monotonic())
                pending.put_nowait(ticket)
                while not ticket.ready.wait(0.05):
                    if stop.is_set() or time.monotonic() >= request.deadline:
                        ticket.cancelled.set()
                        return
                    if peer_disconnected(connection):
                        ticket.cancelled.set()
                        return
                if ticket.result is not None and not ticket.cancelled.is_set():
                    result = ticket.result
                    if isinstance(request, BinaryRenderRequest):
                        if not result.startswith(b"\x00"):
                            result = b"\x01" + result
                        send_body(
                            connection,
                            struct.pack("!I", len(result)),
                            request.deadline,
                        )
                        send_body(connection, result, request.deadline)
                    else:
                        send(connection, result)
            except (ComputeUnavailable, ValueError) as exc:
                if ticket is not None:
                    ticket.cancelled.set()
                try:
                    reason = (
                        exc.reason
                        if isinstance(exc, ComputeUnavailable)
                        else Reason.INVALID_INPUT
                    )
                    send(connection, error(reason))
                except OSError:
                    pass
            except OSError, queue.Full:
                if ticket is not None:
                    ticket.cancelled.set()
            finally:
                connection.close()
                if (
                    ticket is not None
                    and not ticket.ready.is_set()
                    and not stop.is_set()
                ):
                    ticket.cancelled.set()
                    while not ticket.ready.wait(0.05) and not stop.is_set():
                        pass
                if geometry_entry is not None:
                    dispatcher.geometry_cache.release(geometry_entry)
                output_budget.release(output_reserved)
                queue_budget.release(reserved)
                slots.release()

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(address))
                os.chmod(address, 0o600)
                server.listen(16)
                server.settimeout(0.2)
                while not stop.is_set():
                    if time.monotonic() - last_used > 300:
                        break
                    if (
                        dispatcher.active_deadline is not None
                        and time.monotonic() >= dispatcher.active_deadline
                    ):
                        failed(root)
                        os._exit(74)
                    rss = process_rss_bytes(os.getpid())
                    if rss is not None and rss > (
                        dispatcher.host_capacity
                        or settings.embedding_worker_memory_mb * 1024**2
                    ):
                        failed(root)
                        os._exit(74)
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    if not slots.acquire(blocking=False):
                        connection.close()
                        continue
                    handlers = [thread for thread in handlers if thread.is_alive()]
                    thread = threading.Thread(
                        target=handle, args=(connection,), daemon=True
                    )
                    handlers.append(thread)
                    thread.start()
        finally:
            stop.set()
            executor.join(timeout=5)
            if executor.is_alive():
                os._exit(74)
            until = time.monotonic() + 6
            for thread in handlers:
                thread.join(timeout=max(0, until - time.monotonic()))
            if any(thread.is_alive() for thread in handlers):
                os._exit(74)
            dispatcher.close()
            address.unlink(missing_ok=True)


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
