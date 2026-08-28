"""Validation on the fleet payloads, before anything is queued.

Each of these rejects a request that is individually well-formed and jointly
meaningless — the kind a service check would catch far too late, after a job row
exists.

`printer_id_required` covers manual routing with no target. "Manual" *is* the
choice of printer, so a manual request without one is not a job with a missing
field; it is a job that cannot be routed at all, and it would sit in the queue
looking stuck.

`automatic_batch_spool_not_allowed` is the subtler one. Naming a spool pins the
job to one physical printer, and an automatic strategy exists to choose the
printer — so the two together are contradictory instructions, and honouring
either silently gives the operator something they did not ask for.

A maintenance window that starts and ends at the same instant is never active, so
a printer the operator meant to take out of service keeps receiving work.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import RoutingStrategy
from app.schemas.fleet import BatchCreate, MaintenanceWindowCreate, QueueJobCreate

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class TestQueueJobCreate:
    def test_refuses_manual_routing_with_no_printer(self) -> None:
        # "Manual" is the choice of printer; without one the job cannot route and
        # sits in the queue looking stuck.
        with pytest.raises(ValueError, match="printer_id_required"):
            QueueJobCreate(file_id=1)


class TestBatchCreate:
    def test_refuses_manual_routing_with_no_printer(self) -> None:
        with pytest.raises(ValueError, match="printer_id_required"):
            BatchCreate(file_id=1, quantity=1, strategy=RoutingStrategy.MANUAL)

    def test_refuses_a_named_spool_on_an_automatic_strategy(self) -> None:
        # A spool pins the job to one machine; an automatic strategy exists to
        # pick the machine. Honouring either gives the operator something else.
        with pytest.raises(ValueError, match="automatic_batch_spool_not_allowed"):
            BatchCreate(file_id=1, quantity=1, spool_id=1)


class TestMaintenanceWindowCreate:
    def test_refuses_a_window_that_ends_when_it_starts(self) -> None:
        # Never active, so the printer the operator meant to take out of service
        # keeps taking work — and the window looks correct in the list.
        with pytest.raises(ValueError, match="maintenance_window_invalid"):
            MaintenanceWindowCreate(starts_at=NOW, ends_at=NOW)

    def test_accepts_a_window_with_a_duration(self) -> None:
        window = MaintenanceWindowCreate(
            starts_at=NOW, ends_at=NOW + timedelta(hours=1)
        )

        assert window.ends_at > window.starts_at
