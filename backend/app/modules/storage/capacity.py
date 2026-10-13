"""Serialized durable reservations for peak operation allocations.

Reservations are budget claims, never ownership receipts. Expiry does not free
budget until the workflow owner proves that an operation has stopped writing.
A caller must renew before increasing its peak allocation and release after its
last allocation. Actual free space is re-probed under the admission lock.

A transient claim (scratch space one operation holds for seconds) is kept in
this process and published as part of one lease row per process. The lease is
never below what the process holds, so every other admission still counts it;
a claim that fits inside the lease is decided from a read and writes nothing.
"""

from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Callable, Iterator, Sequence
from weakref import WeakKeyDictionary

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.time import ensure_utc, utcnow
from app.db.models import CapacityAdmissionEvent, CapacityLock, CapacityReservation
from app.db.session import SessionFactory
from app.modules.storage.capacity_policy import CapacityPolicy


@dataclass(frozen=True)
class CapacityResource:
    domain_id: str
    required_bytes: int
    available_bytes: int | None
    role: str
    path: str | None = None
    total_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.domain_id
            or self.required_bytes < 0
            or (self.available_bytes is not None and self.available_bytes < 0)
            or (self.total_bytes is not None and self.total_bytes < 0)
        ):
            raise ValueError("invalid capacity resource")

    @classmethod
    def for_path(
        cls, path: Path, required_bytes: int, *, role: str
    ) -> CapacityResource:
        candidate = path.expanduser().resolve(strict=False)
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        try:
            info = candidate.stat()
        except OSError as exc:
            raise OperationError(
                "storage_capacity_unavailable", kind=ErrorKind.CAPACITY
            ) from exc
        return cls(
            f"volume:{info.st_dev}",
            required_bytes,
            None,
            role,
            str(path.expanduser().absolute()),
        )

    @classmethod
    def for_quota(
        cls,
        domain_id: str,
        required_bytes: int,
        available_bytes: int | None,
        *,
        role: str,
        total_bytes: int | None = None,
    ) -> CapacityResource:
        return cls(
            f"quota:{domain_id}",
            required_bytes,
            available_bytes,
            role,
            total_bytes=total_bytes,
        )

    def probe(self) -> int | None:
        return self.measure()[0]

    @classmethod
    def for_budget(
        cls, domain_id: str, required_bytes: int, available_bytes: int, *, role: str
    ) -> CapacityResource:
        """An application ceiling, paired with a separate physical allocation.

        Volume headroom belongs to the physical resource. Charging it against
        this ceiling would make a 100 MiB application budget unusable whenever
        the host reserves 1 GiB of free disk space.
        """
        return cls(f"budget:{domain_id}", required_bytes, available_bytes, role)

    def measure(self) -> tuple[int | None, int | None]:
        if self.path is None:
            return self.available_bytes, self.total_bytes
        import os

        candidate = Path(self.path).resolve(strict=False)
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        try:
            if f"volume:{candidate.stat().st_dev}" != self.domain_id:
                raise OperationError(
                    "storage_capacity_volume_changed", kind=ErrorKind.CAPACITY
                )
            stats = os.statvfs(candidate)
            return stats.f_bavail * stats.f_frsize, stats.f_blocks * stats.f_frsize
        except OSError as exc:
            raise OperationError(
                "storage_capacity_unavailable", kind=ErrorKind.CAPACITY
            ) from exc


def _process_identity() -> dict[str, str | int]:
    try:
        return {
            "host": Path("/etc/machine-id").read_text().strip()
            + ":"
            + socket.gethostname(),
            "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "pid": os.getpid(),
            "start": Path(f"/proc/{os.getpid()}/stat")
            .read_text()
            .rsplit(")", 1)[1]
            .split()[19],
        }
    except OSError, IndexError:
        return {}


def _owner_stopped(payload: str) -> bool:
    identity = json.loads(payload)
    if identity.get("lifetime") == "workflow":
        return False
    current = _process_identity()
    if not identity or identity.get("host") != current.get("host"):
        return False
    if identity.get("boot") != current.get("boot"):
        return True
    pid = identity.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        return start != identity.get("start")
    except FileNotFoundError:
        return True
    except OSError, IndexError:
        return False


def _decode(payload: str) -> list[CapacityResource]:
    return [CapacityResource(**item) for item in json.loads(payload)]


_LEASE_PREFIX = "process:"
_LEASE_ROLE = "process claims"
_LEASE_TTL_SECONDS = 900


def _lease_id() -> str:
    """This process's lease row: one per process lifetime, never reused."""
    identity = _process_identity()
    if not identity:
        return f"{_LEASE_PREFIX}{os.getpid()}"
    # The boot id keeps a restarted container, where the same pid can start at
    # the same tick, from adopting the row of the process it replaced.
    return f"{_LEASE_PREFIX}{identity['boot']}:{identity['pid']}:{identity['start']}"


def _totals(resources: Sequence[CapacityResource]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in resources:
        totals[item.domain_id] = totals.get(item.domain_id, 0) + item.required_bytes
    return totals


@dataclass
class _Ledger:
    """The transient claims this process holds against one database."""

    lock: Lock = field(default_factory=Lock)
    claims: dict[str, tuple[CapacityResource, ...]] = field(default_factory=dict)
    # The most the process held at once since its lease was last trimmed.
    peak: dict[str, int] = field(default_factory=dict)

    def note_peak(self) -> None:
        for domain, size in self.held().items():
            self.peak[domain] = max(self.peak.get(domain, 0), size)

    def held(self, *, without: str | None = None) -> dict[str, int]:
        totals: dict[str, int] = {}
        for operation_id, resources in self.claims.items():
            if operation_id == without:
                continue
            for domain, required in _totals(resources).items():
                totals[domain] = totals.get(domain, 0) + required
        return totals


_ledgers: WeakKeyDictionary[object, _Ledger] = WeakKeyDictionary()
_ledgers_lock = Lock()


def forget_transient_claims() -> None:
    """Drop every in-process claim. For a process whose database was replaced."""
    with _ledgers_lock:
        _ledgers.clear()


@dataclass
class CapacityReservationHandle:
    manager: CapacityManager
    operation_id: str
    resources: Sequence[CapacityResource]
    warnings: tuple[str, ...]
    transient: bool = False

    def renew(
        self,
        resources: Sequence[CapacityResource] | None = None,
        *,
        ttl_seconds: int = 900,
    ) -> None:
        replacement = self.resources if resources is None else resources
        renewed = (
            self.manager._claim(self.operation_id, replacement, renew=True)
            if self.transient
            else self.manager._reserve(
                self.operation_id, replacement, ttl_seconds=ttl_seconds, renew=True
            )
        )
        self.resources, self.warnings = renewed.resources, renewed.warnings

    def release(self) -> None:
        if self.transient:
            self.manager._release_claim(self.operation_id)
        else:
            self.manager.release(self.operation_id)


class CapacityManager:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        headroom_bytes: int | None = None,
        headroom_percent: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.headroom_bytes = (
            settings.storage_min_free_bytes
            if headroom_bytes is None
            else headroom_bytes
        )
        self.policy = CapacityPolicy(
            self.headroom_bytes,
            settings.storage_min_free_percent
            if headroom_percent is None
            else headroom_percent,
        )

    @staticmethod
    def serialize_admission(session: Session) -> None:
        """Serialize admission in a caller transaction; caller owns its outcome.

        Do not call reserve while holding this lock in another session. Owners
        can atomically check their own durable quotas under this boundary.
        """
        # INSERT conflict handling also serializes the first concurrent callers;
        # UPDATE keeps the lock through commit on SQLite and PostgreSQL alike.
        table = getattr(CapacityLock, "__table__")  # noqa: B009
        insert = (
            sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        )
        session.execute(
            insert(table)
            .values(id=1, revision=0)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        session.execute(
            update(CapacityLock)
            .where(col(CapacityLock.id) == 1)
            .values(revision=col(CapacityLock.revision) + 1)
        )

    def reserve(
        self,
        operation_id: str,
        resources: Sequence[CapacityResource],
        *,
        ttl_seconds: int = 900,
        durable: bool = False,
        transient: bool = False,
    ) -> CapacityReservationHandle:
        """Admit one operation's peak allocation.

        ``transient`` is for scratch space held for the length of one call and
        released by the same process: it is admitted against the same budget
        but recorded in this process's lease instead of a row of its own. A
        claim that outlives the process, or that another process reads back by
        its operation id, is not transient.
        """
        if transient:
            if durable:
                raise ValueError("invalid capacity reservation")
            return self._claim(operation_id, resources, renew=False)
        return self._reserve(
            operation_id,
            resources,
            ttl_seconds=ttl_seconds,
            renew=False,
            durable=durable,
        )

    def _ledger(self, session: Session) -> _Ledger:
        bind = session.get_bind()
        with _ledgers_lock:
            ledger = _ledgers.get(bind)
            if ledger is None:
                ledger = _ledgers[bind] = _Ledger()
            return ledger

    def _decide(
        self, resources: Sequence[CapacityResource], reserved: dict[str, int]
    ) -> list[str]:
        """Warnings for admitting ``resources`` beside ``reserved`` bytes.

        ``reserved`` is every other claim per domain. Raises the denial.
        """
        requested = _totals(resources)
        free: dict[str, int | None] = {}
        volume_totals: dict[str, int | None] = {}
        for item in resources:
            measured, total = item.measure()
            previous_total = volume_totals.get(item.domain_id)
            volume_totals[item.domain_id] = (
                total if previous_total is None else max(previous_total, total or 0)
            )
            previous = free.get(item.domain_id)
            free[item.domain_id] = (
                measured
                if previous is None
                else previous
                if measured is None
                else min(previous, measured)
            )
        warnings = []
        for domain, available in free.items():
            policy = CapacityPolicy() if domain.startswith("budget:") else self.policy
            decision = policy.evaluate(
                requested[domain],
                available_bytes=available,
                reserved_bytes=reserved.get(domain, 0),
                total_bytes=volume_totals[domain],
            )
            if decision.outcome == "warn":
                warnings.append(f"{decision.reason}:{domain}")
            elif decision.outcome == "deny":
                raise OperationError(
                    "storage_capacity_exceeded",
                    kind=ErrorKind.CAPACITY,
                    retry_after_seconds=decision.refresh_after_seconds,
                    capacity={
                        "required_bytes": decision.required_bytes,
                        "available_bytes": decision.available_bytes,
                        "reserved_bytes": decision.reserved_bytes,
                        "headroom_bytes": decision.headroom_bytes,
                    },
                )
        return warnings

    @staticmethod
    def _others(
        session: Session, lease_id: str
    ) -> tuple[dict[str, int], dict[str, int]]:
        """(bytes every other row reserves, bytes this process's lease holds)."""
        others: dict[str, int] = {}
        lease: dict[str, int] = {}
        for row in session.exec(select(CapacityReservation)):
            target = lease if row.operation_id == lease_id else others
            for item in _decode(row.resources_json):
                target[item.domain_id] = (
                    target.get(item.domain_id, 0) + item.required_bytes
                )
        return others, lease

    @staticmethod
    def _write_lease(session: Session, lease_id: str, lease: dict[str, int]) -> None:
        row = session.get(CapacityReservation, lease_id)
        held = {domain: size for domain, size in lease.items() if size > 0}
        if not held:
            if row is not None:
                session.delete(row)
            return
        payload = json.dumps(
            [
                asdict(CapacityResource(domain, size, None, _LEASE_ROLE))
                for domain, size in sorted(held.items())
            ],
            sort_keys=True,
        )
        if row is None:
            row = CapacityReservation(
                operation_id=lease_id,
                resources_json=payload,
                expires_at=utcnow(),
                owner_identity_json=json.dumps(
                    {**_process_identity(), "lifetime": "process"}
                ),
            )
        row.resources_json = payload
        row.expires_at = utcnow() + timedelta(seconds=_LEASE_TTL_SECONDS)
        session.add(row)

    def _with_room(
        self,
        claimed: Sequence[CapacityResource],
        raised: dict[str, int],
        held: dict[str, int],
        others: dict[str, int],
    ) -> dict[str, int]:
        """``raised`` with twice the room in each domain being claimed, if it fits.

        The extra is admitted like any claim, against the same free space and
        the same other owners. Where it would be refused the lease stays at
        what is needed: room to grow is a convenience, never a reason to deny.
        """
        roomy = dict(raised)
        for item in claimed:
            if (
                item.domain_id in roomy
                and roomy[item.domain_id] != raised[item.domain_id]
            ):
                continue
            extra = raised[item.domain_id]
            try:
                self._decide(
                    [
                        CapacityResource(
                            item.domain_id,
                            raised[item.domain_id]
                            + extra
                            - held.get(item.domain_id, 0),
                            item.available_bytes,
                            item.role,
                            item.path,
                            item.total_bytes,
                        )
                    ],
                    {
                        domain: others.get(domain, 0) + held.get(domain, 0)
                        for domain in {*others, *held}
                    },
                )
            except OperationError:
                continue
            roomy[item.domain_id] = raised[item.domain_id] + extra
        return roomy

    def _claim(
        self,
        operation_id: str,
        resources: Sequence[CapacityResource],
        *,
        renew: bool,
    ) -> CapacityReservationHandle:
        if not operation_id or len(operation_id) > 200 or not resources:
            raise ValueError("invalid capacity reservation")
        claimed = tuple(resources)
        lease_id = _lease_id()
        with self.session_factory.scoped_session() as session:
            ledger = self._ledger(session)
        try:
            with ledger.lock:
                existing = ledger.claims.get(operation_id)
                if renew and existing is None:
                    raise OperationError(
                        "capacity_reservation_lost", kind=ErrorKind.CONFLICT
                    )
                if existing is not None and not renew and existing != claimed:
                    raise OperationError(
                        "capacity_operation_conflict", kind=ErrorKind.CONFLICT
                    )
                held = ledger.held(without=operation_id)
                need = dict(held)
                for domain, required in _totals(claimed).items():
                    need[domain] = need.get(domain, 0) + required
                with self.session_factory.scoped_session() as session:
                    others, lease = self._others(session, lease_id)
                covered = all(
                    size <= lease.get(domain, 0) for domain, size in need.items()
                )
                if covered:
                    # The published lease already tells every other admission
                    # about these bytes: deciding needs no write.
                    warnings = self._decide(
                        claimed,
                        {
                            domain: others.get(domain, 0) + held.get(domain, 0)
                            for domain in {*others, *held}
                        },
                    )
                else:
                    with self.session_factory.scoped_session() as session:
                        self.serialize_admission(session)
                        self._reconcile_stopped(session)
                        others, lease = self._others(session, lease_id)
                        warnings = self._decide(
                            claimed,
                            {
                                domain: others.get(domain, 0) + held.get(domain, 0)
                                for domain in {*others, *held}
                            },
                        )
                        # Keep what the lease already covers, and ask for room
                        # to grow: the high-water mark is what lets the next
                        # claims skip this write, and a burst that is still
                        # ramping up would otherwise raise it claim by claim.
                        raised = {
                            domain: max(lease.get(domain, 0), need.get(domain, 0))
                            for domain in {*lease, *need}
                        }
                        self._write_lease(
                            session,
                            lease_id,
                            self._with_room(claimed, raised, held, others),
                        )
                        session.commit()
                ledger.claims[operation_id] = claimed
                ledger.note_peak()
        except OperationError as exc:
            if exc.kind is ErrorKind.CAPACITY and exc.capacity is not None:
                self._record_admission_event(operation_id, exc.detail, exc.capacity)
                from app.modules.storage.capacity_observability import record_decision

                record_decision(operation_id, "deny", exc.detail)
            raise
        from app.modules.storage.capacity_observability import (
            change_reservations,
            record_decision,
        )

        record_decision(
            operation_id,
            "warn" if warnings else "allow",
            warnings[0].partition(":")[0] if warnings else "capacity_available",
        )
        if existing is None:
            change_reservations(operation_id, 1)
        return CapacityReservationHandle(
            self, operation_id, claimed, tuple(warnings), transient=True
        )

    def _release_claim(self, operation_id: str) -> None:
        with self.session_factory.scoped_session() as session:
            ledger = self._ledger(session)
        with ledger.lock:
            released = ledger.claims.pop(operation_id, None) is not None
        if released:
            from app.modules.storage.capacity_observability import change_reservations

            change_reservations(operation_id, -1)

    def operation_reservations(self) -> list[str]:
        """Operations that hold a reservation row of their own, by id.

        A process's lease is not one of them: it stands for the transient
        claims of that process, which ``transient_claims`` reports.
        """
        with self.session_factory.scoped_session() as session:
            return [
                operation_id
                for operation_id in session.exec(
                    select(CapacityReservation.operation_id).order_by(
                        col(CapacityReservation.operation_id)
                    )
                )
                if not operation_id.startswith(_LEASE_PREFIX)
            ]

    def transient_claims(self) -> dict[str, dict[str, int]]:
        """Bytes per domain each transient operation of this process holds now."""
        with self.session_factory.scoped_session() as session:
            ledger = self._ledger(session)
        with ledger.lock:
            return {
                operation_id: _totals(resources)
                for operation_id, resources in ledger.claims.items()
            }

    def trim_lease(self) -> bool:
        """Lower this process's lease to what it has needed lately; whether it wrote.

        Called on a timer. The lease is a high-water mark, and it is lowered
        to the most the process held at once since the previous call, not to
        what it holds this instant: a burst in progress keeps the lease its
        claims fit in, and one that has passed gives its bytes back a call
        later.
        """
        lease_id = _lease_id()
        with self.session_factory.scoped_session() as session:
            ledger = self._ledger(session)
        with ledger.lock:
            ledger.note_peak()
            needed, ledger.peak = ledger.peak, ledger.held()
            with self.session_factory.scoped_session() as session:
                _others, lease = self._others(session, lease_id)
            if all(lease.get(domain, 0) <= needed.get(domain, 0) for domain in lease):
                return False
            with self.session_factory.scoped_session() as session:
                self.serialize_admission(session)
                self._write_lease(session, lease_id, needed)
                session.commit()
        return True

    def _reserve(
        self,
        operation_id: str,
        resources: Sequence[CapacityResource],
        *,
        ttl_seconds: int,
        renew: bool,
        durable: bool = False,
    ) -> CapacityReservationHandle:
        if (
            not operation_id
            or len(operation_id) > 200
            or ttl_seconds <= 0
            or not resources
        ):
            raise ValueError("invalid capacity reservation")
        payload = json.dumps([asdict(item) for item in resources], sort_keys=True)
        try:
            with self.session_factory.scoped_session() as session:
                self.serialize_admission(session)
                self._reconcile_stopped(session)
                existing = session.get(CapacityReservation, operation_id)
                if renew and existing is None:
                    raise OperationError(
                        "capacity_reservation_lost", kind=ErrorKind.CONFLICT
                    )
                if (
                    existing is not None
                    and not renew
                    and json.dumps(
                        [asdict(item) for item in _decode(existing.resources_json)],
                        sort_keys=True,
                    )
                    != payload
                ):
                    raise OperationError(
                        "capacity_operation_conflict", kind=ErrorKind.CONFLICT
                    )
                # This process's lease may hold room it has not claimed yet.
                # Its own admissions count what it really holds, so that room
                # never refuses the process that set it aside.
                reserved = self._ledger(session).held()
                lease_id = _lease_id()
                for persisted in session.exec(
                    select(CapacityReservation).where(
                        CapacityReservation.operation_id != operation_id
                    )
                ):
                    if persisted.operation_id == lease_id:
                        continue
                    for item in _decode(persisted.resources_json):
                        reserved[item.domain_id] = (
                            reserved.get(item.domain_id, 0) + item.required_bytes
                        )
                warnings = self._decide(resources, reserved)
                row = existing or CapacityReservation(
                    operation_id=operation_id,
                    resources_json=payload,
                    expires_at=utcnow(),
                    owner_identity_json=json.dumps(
                        {
                            **_process_identity(),
                            "lifetime": "workflow" if durable else "process",
                        }
                    ),
                )
                if durable:
                    identity = json.loads(row.owner_identity_json)
                    identity["lifetime"] = "workflow"
                    row.owner_identity_json = json.dumps(identity)
                row.resources_json = payload
                row.expires_at = utcnow() + timedelta(seconds=ttl_seconds)
                session.add(row)
                session.commit()
        except OperationError as exc:
            if exc.kind is ErrorKind.CAPACITY and exc.capacity is not None:
                self._record_admission_event(operation_id, exc.detail, exc.capacity)
                from app.modules.storage.capacity_observability import record_decision

                record_decision(operation_id, "deny", exc.detail)
            raise
        from app.modules.storage.capacity_observability import (
            change_reservations,
            record_decision,
        )

        record_decision(
            operation_id,
            "warn" if warnings else "allow",
            warnings[0].partition(":")[0] if warnings else "capacity_available",
        )
        if existing is None:
            change_reservations(operation_id, 1)
        return CapacityReservationHandle(
            self, operation_id, tuple(resources), tuple(warnings)
        )

    def _record_admission_event(
        self, operation_id: str, reason: str, capacity: dict[str, int | None]
    ) -> None:
        """Persist bounded diagnostics after the denied admission rolls back."""
        operation_kind = operation_id.partition(":")[0]
        if not operation_kind.replace("-", "").isalnum() or len(operation_kind) > 64:
            operation_kind = "unknown"
        with self.session_factory.scoped_session() as session:
            session.add(
                CapacityAdmissionEvent(
                    operation_kind=operation_kind,
                    decision="deny",
                    reason=reason[:64],
                    required_bytes=int(capacity.get("required_bytes") or 0),
                    available_bytes=capacity.get("available_bytes"),
                    reserved_bytes=int(capacity.get("reserved_bytes") or 0),
                    headroom_bytes=int(capacity.get("headroom_bytes") or 0),
                )
            )
            cutoff = utcnow() - timedelta(days=7)
            for row in session.exec(
                select(CapacityAdmissionEvent).where(
                    CapacityAdmissionEvent.occurred_at < cutoff
                )
            ):
                session.delete(row)
            overflow = list(
                session.exec(
                    select(CapacityAdmissionEvent)
                    .order_by(col(CapacityAdmissionEvent.occurred_at).desc())
                    .offset(100)
                )
            )
            for row in overflow:
                session.delete(row)
            session.commit()

    @contextmanager
    def hold(
        self,
        operation_id: str,
        resources: Sequence[CapacityResource],
        *,
        ttl_seconds: int = 900,
        transient: bool = False,
    ) -> Iterator[CapacityReservationHandle]:
        handle = self.reserve(
            operation_id, resources, ttl_seconds=ttl_seconds, transient=transient
        )
        try:
            yield handle
        finally:
            handle.release()

    def release(self, operation_id: str) -> None:
        released = False
        with self.session_factory.scoped_session() as session:
            self.serialize_admission(session)
            row = session.get(CapacityReservation, operation_id)
            if row is not None:
                session.delete(row)
                released = True
            session.commit()
        if released:
            from app.modules.storage.capacity_observability import change_reservations

            change_reservations(operation_id, -1)

    def reserved_bytes(self) -> dict[str, int]:
        with self.session_factory.scoped_session() as session:
            totals: dict[str, int] = {}
            for row in session.exec(select(CapacityReservation)):
                for item in _decode(row.resources_json):
                    totals[item.domain_id] = (
                        totals.get(item.domain_id, 0) + item.required_bytes
                    )
            return totals

    @staticmethod
    def _reconcile_stopped(session: Session) -> int:
        released = 0
        for row in session.exec(
            select(CapacityReservation).where(
                col(CapacityReservation.expires_at) <= utcnow()
            )
        ):
            # Older persisted workflow claims predate the explicit lifetime flag.
            # Their surviving staging/shadow/candidate objects still spend space.
            if row.operation_id.startswith(
                ("artifact-upload:", "vault-migration:", "postgres-restore:")
            ):
                continue
            if ensure_utc(row.expires_at) <= utcnow() and _owner_stopped(
                row.owner_identity_json
            ):
                session.delete(row)
                released += 1
        session.flush()
        return released

    def reconcile_stopped_processes(self) -> int:
        with self.session_factory.scoped_session() as session:
            self.serialize_admission(session)
            released = self._reconcile_stopped(session)
            session.commit()
            return released

    def reconcile(self, is_operation_active: Callable[[str], bool]) -> int:
        """Release only expired claims whose owner proves no further writes."""
        released = 0
        with self.session_factory.scoped_session() as session:
            self.serialize_admission(session)
            for row in session.exec(select(CapacityReservation)):
                if ensure_utc(row.expires_at) <= utcnow() and not is_operation_active(
                    row.operation_id
                ):
                    session.delete(row)
                    released += 1
            session.commit()
        return released
