"""Local process-shared resource credits held by file descriptors, not PID leases.

Use separate pools for native CPU/RAM and source staging slots/bytes. POSIX
flock ownership follows the open file description across fork/exec; closing a
parent's handle cannot release a credit still held by its native child.
"""

from __future__ import annotations

import errno
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

_POLL_SECONDS = 0.025
_MAX_RECORD_BYTES = 4096


class AdmissionCorrupted(RuntimeError):
    """A live reservation cannot be interpreted; admission must fail closed."""


class AdmissionTooLarge(ValueError):
    """The request cannot fit even when this resource pool is idle."""


@dataclass(frozen=True)
class Resources:
    slots: int
    bytes: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0 for value in (self.slots, self.bytes)
        ):
            raise ValueError("resource amounts must be positive integers")

    def fits(self, capacity: Resources) -> bool:
        return self.slots <= capacity.slots and self.bytes <= capacity.bytes


class _State(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"


@dataclass(frozen=True)
class _Ticket:
    name: str
    order: int
    state: _State
    request: Resources
    capacity: Resources

    def encode(self) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "name": self.name,
                "order": self.order,
                "state": self.state.value,
                "request": [self.request.slots, self.request.bytes],
                "capacity": [self.capacity.slots, self.capacity.bytes],
            },
            separators=(",", ":"),
        ).encode("ascii")

    @classmethod
    def read(cls, descriptor: int) -> _Ticket:
        try:
            raw = os.pread(descriptor, _MAX_RECORD_BYTES + 1, 0)
            if len(raw) > _MAX_RECORD_BYTES:
                raise ValueError("oversized record")
            data = json.loads(raw)
            if (
                type(data["version"]) is not int
                or data["version"] != 1
                or not isinstance(data["name"], str)
                or len(data["name"]) != 32
                or any(c not in "0123456789abcdef" for c in data["name"])
                or type(data["order"]) is not int
                or data["order"] < 0
            ):
                raise ValueError("invalid ticket identity")
            ticket = cls(
                data["name"],
                data["order"],
                _State(data["state"]),
                Resources(*data["request"]),
                Resources(*data["capacity"]),
            )
            if not ticket.request.fits(ticket.capacity):
                raise ValueError("invalid ticket resources")
            return ticket
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise AdmissionCorrupted("invalid live resource ticket") from exc


def _open(path: Path, *, exclusive: bool = False) -> BinaryIO:
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    if exclusive:
        flags |= os.O_EXCL
    return os.fdopen(os.open(path, flags, 0o600), "r+b", buffering=0)


def _lock(descriptor: int) -> bool:
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return False
        raise


@dataclass(frozen=True)
class NativePermit:
    """A scoped open descriptor; child launchers explicitly inherit ``fileno``."""

    _file: BinaryIO
    resources: Resources
    identity: str
    path: Path

    @property
    def fileno(self) -> int:
        return self._file.fileno()

    @classmethod
    @contextmanager
    def inherit(cls, descriptor: int, path: Path) -> Iterator[NativePermit]:
        # Duplicate the parent's open file description, retaining its lock.
        with os.fdopen(os.dup(descriptor), "r+b", buffering=0) as owned:
            ticket = _Ticket.read(owned.fileno())
            if ticket.state is not _State.ACTIVE:
                raise AdmissionCorrupted("inherited ticket is not active")
            # A separate open file description must resolve to the same inode
            # and remain excluded by the inherited lock. This works on POSIX
            # without Linux procfs; a path alone is never authority.
            probe = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                inherited, reopened = os.fstat(owned.fileno()), os.fstat(probe)
                if path.name != ticket.name + ".ticket" or (
                    inherited.st_dev,
                    inherited.st_ino,
                ) != (reopened.st_dev, reopened.st_ino):
                    raise AdmissionCorrupted("inherited ticket identity changed")
                if _lock(probe):
                    raise AdmissionCorrupted("inherited ticket has no held credit")
                if not _lock(owned.fileno()):
                    raise AdmissionCorrupted("inherited descriptor does not own credit")
            finally:
                os.close(probe)
            yield cls(owned, ticket.request, ticket.name, path)


class LocalResourcePool:
    def __init__(
        self, directory: Path, *, reclaim: Callable[[str], None] | None = None
    ) -> None:
        self.directory = directory
        self._reclaim = reclaim

    @contextmanager
    def _coordinator(self, checkpoint: Callable[[], None]) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _open(self.directory / "coordinator") as handle:
            while True:
                checkpoint()
                if _lock(handle.fileno()):
                    break
                time.sleep(_POLL_SECONDS)
            # Closing only: LOCK_UN would also unlock a descriptor inherited by
            # another process. No external checkpoint or application I/O here.
            yield

    def _live(
        self, recovery: ExitStack
    ) -> tuple[list[_Ticket], list[tuple[Path, BinaryIO]]]:
        live = []
        abandoned = []
        for path in self.directory.glob("*.ticket"):
            with ExitStack() as opened:
                handle = opened.enter_context(_open(path))
                if _lock(handle.fileno()):
                    if self._reclaim is None:
                        path.unlink()
                    else:
                        # Keep the original record locked and counted by other
                        # claimants while its workspace is removed outside the
                        # coordinator. A dead owner can still have disk usage.
                        recovery.enter_context(opened.pop_all())
                        abandoned.append((path, handle))
                    continue
                ticket = _Ticket.read(handle.fileno())
                if path.name != ticket.name + ".ticket":
                    raise AdmissionCorrupted("ticket path does not match identity")
                live.append(ticket)
        return live, abandoned

    @staticmethod
    def _available(ticket: _Ticket, live: list[_Ticket]) -> bool:
        registered = next((item for item in live if item.name == ticket.name), None)
        if registered is None:
            raise AdmissionCorrupted("owned resource ticket disappeared")
        if registered != ticket:
            raise AdmissionCorrupted("owned resource ticket changed")
        waiting = sorted(
            (item for item in live if item.state is _State.QUEUED),
            key=lambda item: (item.order, item.name),
        )
        if not waiting or waiting[0].name != ticket.name:
            return False
        active = [item for item in live if item.state is _State.ACTIVE]
        # A new configuration takes effect only after old native credits drain.
        if any(item.capacity != ticket.capacity for item in active):
            return False
        return (
            sum(item.request.slots for item in active) + ticket.request.slots
            <= ticket.capacity.slots
            and sum(item.request.bytes for item in active) + ticket.request.bytes
            <= ticket.capacity.bytes
        )

    @staticmethod
    def _write(handle: BinaryIO, ticket: _Ticket) -> None:
        payload = ticket.encode()
        handle.seek(0)
        if handle.write(payload) != len(payload):
            raise OSError("incomplete resource ticket write")
        handle.truncate()

    @contextmanager
    def reserve(
        self,
        request: Resources,
        capacity: Resources,
        *,
        checkpoint: Callable[[], None],
    ) -> Iterator[NativePermit]:
        if not request.fits(capacity):
            raise AdmissionTooLarge("request exceeds total resource capacity")
        with self._coordinator(checkpoint):
            # Registration defines FIFO order; a slow cancellation probe before
            # registration cannot jump ahead of claims already waiting.
            ticket = _Ticket(
                uuid4().hex, time.monotonic_ns(), _State.QUEUED, request, capacity
            )
            handle = _open(self.directory / (ticket.name + ".ticket"), exclusive=True)
            try:
                if not _lock(handle.fileno()):
                    raise AdmissionCorrupted("new resource ticket is already held")
                self._write(handle, ticket)
            except BaseException:
                handle.close()
                raise
        try:
            while True:
                with ExitStack() as recovery:
                    with self._coordinator(checkpoint):
                        live, abandoned = self._live(recovery)
                        if not abandoned and self._available(ticket, live):
                            ticket = _Ticket(
                                ticket.name,
                                ticket.order,
                                _State.ACTIVE,
                                request,
                                capacity,
                            )
                            self._write(handle, ticket)
                            break
                    for path, retired in abandoned:
                        assert self._reclaim is not None
                        self._reclaim(path.stem)
                        with self._coordinator(checkpoint):
                            before, current = os.fstat(retired.fileno()), path.stat()
                            if (before.st_dev, before.st_ino) != (
                                current.st_dev,
                                current.st_ino,
                            ):
                                raise AdmissionCorrupted("recovery ticket was replaced")
                            path.unlink()
                time.sleep(_POLL_SECONDS)
            checkpoint()
            yield NativePermit(
                handle, request, ticket.name, self.directory / (ticket.name + ".ticket")
            )
        finally:
            # Never unlink here: a child may still hold this exact descriptor.
            handle.close()
