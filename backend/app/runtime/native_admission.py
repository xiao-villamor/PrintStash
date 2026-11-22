"""Local process-shared resource credits held by file descriptors, not PID leases.

Use separate pools for native CPU/RAM and source staging slots/bytes. POSIX
flock ownership follows the open file description across fork/exec; closing a
parent's handle cannot release a credit still held by its native child.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.core.work_priority import WorkPriority

_POLL_SECONDS = 0.025
# A claim waiting for capacity is woken at once by a permit this process
# releases; this bound only covers capacity freed by another process.
_WAIT_SECONDS = 0.25


class _Releases:
    """Counts permits this process released, so a waiter never misses one."""

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._count = 0

    def seen(self) -> int:
        with self._changed:
            return self._count

    def released(self) -> None:
        with self._changed:
            self._count += 1
            self._changed.notify_all()

    def wait(self, seen: int, seconds: float) -> None:
        with self._changed:
            if self._count == seen:
                self._changed.wait(seconds)


_releases = _Releases()
_MAX_RECORD_BYTES = 4096
_BACKFILL_MAX_WAIT_NS = 30 * 1_000_000_000


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
    priority: WorkPriority
    hard_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.priority, WorkPriority):
            raise TypeError("priority must be a WorkPriority")
        if (
            type(self.hard_bytes) is not int
            or not self.request.bytes <= self.hard_bytes <= self.capacity.bytes
        ):
            raise ValueError("invalid hard resource ceiling")

    def encode(self) -> bytes:
        data = {
            "version": 2,
            "name": self.name,
            "order": self.order,
            "state": self.state.value,
            "request": [self.request.slots, self.request.bytes],
            "capacity": [self.capacity.slots, self.capacity.bytes],
            "priority": self.priority.value,
        }
        if self.hard_bytes != self.request.bytes:
            # Only split claims need the new record shape. Drain old native
            # claimants before starting processes that write split tickets.
            data.update(version=3, hard_bytes=self.hard_bytes)
        return json.dumps(data, separators=(",", ":")).encode("ascii")

    @classmethod
    def read(cls, descriptor: int) -> _Ticket:
        return cls.decode(_read_record(descriptor))

    @classmethod
    def decode(cls, raw: bytes) -> _Ticket:
        try:
            if len(raw) > _MAX_RECORD_BYTES:
                raise ValueError("oversized record")
            data = json.loads(raw)
            if (
                type(data["version"]) is not int
                or data["version"] not in (1, 2, 3)
                or not isinstance(data["name"], str)
                or len(data["name"]) != 32
                or any(c not in "0123456789abcdef" for c in data["name"])
                or type(data["order"]) is not int
                or data["order"] < 0
            ):
                raise ValueError("invalid ticket identity")
            required = {"version", "name", "order", "state", "request", "capacity"}
            if data["version"] == 1:
                # Historical receipts declared FIFO, not a work priority. Keep
                # their live credits accounted for, treating their old order as
                # backfill subject to the same finite aging guarantee. All old
                # application claimants must drain before v2 writers start:
                # v1 schedulers cannot interpret v2 records. Guardians only
                # retain inherited descriptors and do not make queue decisions.
                priority = WorkPriority.BACKFILL
            else:
                required.add("priority")
                if type(data["priority"]) is not str:
                    raise TypeError("invalid priority representation")
                priority = WorkPriority(data["priority"])
            request = Resources(*data["request"])
            hard_bytes = request.bytes
            if data["version"] == 3:
                required.add("hard_bytes")
                hard_bytes = data["hard_bytes"]
            if set(data) != required:
                raise ValueError("invalid ticket fields")
            ticket = cls(
                data["name"],
                data["order"],
                _State(data["state"]),
                request,
                Resources(*data["capacity"]),
                priority,
                hard_bytes,
            )
            if not ticket.request.fits(ticket.capacity):
                raise ValueError("invalid ticket resources")
            return ticket
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise AdmissionCorrupted("invalid live resource ticket") from exc


def _read_record(descriptor: int) -> bytes:
    return os.pread(descriptor, _MAX_RECORD_BYTES + 1, 0)


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
    hard_bytes: int

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
            yield cls(owned, ticket.request, ticket.name, path, ticket.hard_bytes)


class LocalResourcePool:
    def __init__(
        self, directory: Path, *, reclaim: Callable[[str], None] | None = None
    ) -> None:
        self.directory = directory
        self._reclaim = reclaim
        self._records: dict[bytes, _Ticket] = {}

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
        records = {}
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
                # Decode only changed receipts, but read every record and
                # re-prove descriptor custody every scan. The cache is keyed by
                # the record's bytes, never by stat: file timestamps advance by
                # a coarse clock tick, so a same-size QUEUED -> ACTIVE rewrite
                # within one tick keeps every stat field, and an admitted claim
                # would stay a queued head that blocks every later claim.
                raw = _read_record(handle.fileno())
                ticket = self._records.get(raw)
                if ticket is None:
                    ticket = _Ticket.decode(raw)
                records[raw] = ticket
                if path.name != ticket.name + ".ticket":
                    raise AdmissionCorrupted("ticket path does not match identity")
                live.append(ticket)
        self._records = records
        return live, abandoned

    def has_waiters(self, *, checkpoint: Callable[[], None]) -> bool:
        """Inspect live bounded receipts without running callbacks under the lock.

        Dead native receipts may be retired; prepared workspaces retain their
        records for the normal recovery path. Neither disk cleanup nor external
        cancellation probes run while the coordinator is held.
        """
        with ExitStack() as recovery:
            with self._coordinator(checkpoint):
                live, _abandoned = self._live(recovery)
                return any(ticket.state is _State.QUEUED for ticket in live)

    @staticmethod
    def _available(ticket: _Ticket, live: list[_Ticket]) -> bool:
        registered = next((item for item in live if item.name == ticket.name), None)
        if registered is None:
            raise AdmissionCorrupted("owned resource ticket disappeared")
        if registered != ticket:
            raise AdmissionCorrupted("owned resource ticket changed")
        now = time.monotonic_ns()

        def rank(item: _Ticket) -> tuple[int, int, str]:
            # Promotion preserves registration order: an aged large claim
            # becomes the head and accumulates capacity instead of being
            # bypassed forever by a stream of smaller interactive claims.
            backfill = item.priority is WorkPriority.BACKFILL
            aged = now - item.order >= _BACKFILL_MAX_WAIT_NS
            return (int(backfill and not aged), item.order, item.name)

        waiting = sorted(
            (item for item in live if item.state is _State.QUEUED),
            key=rank,
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
        priority: WorkPriority = WorkPriority.INTERACTIVE,
        hard_bytes: int | None = None,
    ) -> Iterator[NativePermit]:
        if not isinstance(priority, WorkPriority):
            raise TypeError("priority must be a WorkPriority")
        if not request.fits(capacity):
            raise AdmissionTooLarge("request exceeds total resource capacity")
        with self._coordinator(checkpoint):
            # Registration defines FIFO order within a priority; a slow probe
            # cannot jump ahead of claims already registered in the same tier.
            ticket = _Ticket(
                uuid4().hex,
                time.monotonic_ns(),
                _State.QUEUED,
                request,
                capacity,
                priority,
                request.bytes if hard_bytes is None else hard_bytes,
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
                seen = _releases.seen()
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
                                priority,
                                ticket.hard_bytes,
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
                if abandoned:
                    # Retiring a finished claim's record frees its share: look
                    # again at once. Waiting here for a release that already
                    # happened cost each reservation a full wait interval.
                    continue
                _releases.wait(seen, _WAIT_SECONDS)
            checkpoint()
            yield NativePermit(
                handle,
                request,
                ticket.name,
                self.directory / (ticket.name + ".ticket"),
                ticket.hard_bytes,
            )
        finally:
            # Never unlink here: a child may still hold this exact descriptor.
            handle.close()
            _releases.released()
