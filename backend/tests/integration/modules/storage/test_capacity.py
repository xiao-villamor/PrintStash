"""Capacity admission never lets concurrent owners spend the same free bytes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import SQLModel, create_engine, select

from app.core.errors import OperationError
from app.db.models import CapacityAdmissionEvent
from app.db.session import SQLiteSessionFactory, get_session_factory
from app.modules.storage.capacity import CapacityManager, CapacityResource


class TestCapacityManager:
    def test_admits_an_explicit_logical_budget(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=100)

        reservation = manager.reserve(
            "index", [CapacityResource.for_budget("index", 50, 50, role="index limit")]
        )

        assert manager.reserved_bytes() == {"budget:index": 50}
        reservation.release()

    def test_preserves_physical_headroom_with_logical_limits(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=10)

        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            manager.reserve(
                "index",
                [
                    CapacityResource.for_budget("index", 50, 50, role="index limit"),
                    CapacityResource.for_quota("disk", 95, 100, role="remote disk"),
                ],
            )

        assert manager.reserved_bytes() == {}

    def test_denies_combined_allocations_on_one_volume(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=10)
        resources = [
            CapacityResource.for_quota("one", 50, 100, role=role)
            for role in ("input", "output")
        ]
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            manager.reserve("upload", resources)
        event = db_session.exec(select(CapacityAdmissionEvent)).one()
        assert event.operation_kind == "upload"
        assert event.decision == "deny"
        assert event.required_bytes == 100
        assert event.available_bytes == 100

    def test_redacts_invalid_operation_kind_from_denial_history(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)

        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            manager.reserve(
                "private name/with path",
                [CapacityResource.for_quota("one", 101, 100, role="input")],
            )

        event = db_session.exec(select(CapacityAdmissionEvent)).one()
        assert event.operation_kind == "unknown"

    def test_preserves_exact_headroom(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=10)
        reservation = manager.reserve(
            "upload", [CapacityResource.for_quota("one", 90, 100, role="input")]
        )
        assert manager.reserved_bytes() == {"quota:one": 90}
        reservation.release()
        assert manager.reserved_bytes() == {}

    def test_retains_unknown_quota_warning(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=10)
        reservation = manager.reserve(
            "upload", [CapacityResource.for_quota("remote", 90, None, role="vault")]
        )
        assert reservation.warnings == ("capacity_unknown:quota:remote",)

    def test_rejects_changed_operation_identity(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        manager.reserve(
            "upload", [CapacityResource.for_quota("one", 20, 100, role="input")]
        )
        with pytest.raises(OperationError, match="capacity_operation_conflict"):
            manager.reserve(
                "upload", [CapacityResource.for_quota("one", 30, 100, role="input")]
            )

    def test_releases_after_operation_failure(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        with pytest.raises(ValueError):
            with manager.hold(
                "upload", [CapacityResource.for_quota("one", 20, 100, role="input")]
            ):
                raise ValueError("failed")
        assert manager.reserved_bytes() == {}

    def test_rechecks_before_larger_allocation(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        handle = manager.reserve(
            "upload", [CapacityResource.for_quota("one", 20, 100, role="input")]
        )
        with pytest.raises(OperationError, match="storage_capacity_exceeded"):
            handle.renew([CapacityResource.for_quota("one", 101, 100, role="input")])
        assert manager.reserved_bytes() == {"quota:one": 20}

    def test_serializes_competing_reservations(self, tmp_path):
        engine = create_engine(
            f"sqlite:///{tmp_path / 'capacity.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(engine)
        manager = CapacityManager(SQLiteSessionFactory(engine), headroom_bytes=0)
        barrier = Barrier(2)

        def reserve(owner):
            barrier.wait()
            try:
                manager.reserve(
                    owner, [CapacityResource.for_quota("one", 60, 100, role="input")]
                )
                return True
            except OperationError:
                return False

        with ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(reserve, ["first", "second"]))
        assert sorted(outcomes) == [False, True]
        assert manager.reserved_bytes() == {"quota:one": 60}
        engine.dispose()

    def test_resolves_shared_filesystem_identity(self, tmp_path):
        first = CapacityResource.for_path(tmp_path / "input", 10, role="input")
        second = CapacityResource.for_path(tmp_path / "output", 20, role="output")
        assert first.domain_id == second.domain_id

    def test_reconciles_expired_terminal_owner(
        self, db_session, make_capacity_reservation
    ):
        make_capacity_reservation(expired=True)
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        assert manager.reconcile(lambda operation_id: False) == 1
        assert manager.reserved_bytes() == {}

    def test_retains_expired_active_owner(self, db_session, make_capacity_reservation):
        make_capacity_reservation(expired=True)
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        assert manager.reconcile(lambda operation_id: True) == 0
        assert manager.reserved_bytes() == {"quota:test": 10}

    def test_retains_unexpired_owner(self, db_session, make_capacity_reservation):
        make_capacity_reservation()
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        assert manager.reconcile(lambda operation_id: False) == 0
        assert manager.reserved_bytes() == {"quota:test": 10}

    def test_recovers_dead_process_reservation(
        self, db_session, make_capacity_reservation
    ):
        import json

        from app.modules.storage.capacity import _process_identity

        identity = _process_identity()
        identity["pid"] = 2147483647
        make_capacity_reservation(
            expired=True, owner_identity_json=json.dumps(identity)
        )
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        assert manager.reconcile_stopped_processes() == 1
        assert manager.reserved_bytes() == {}

    def test_retains_live_process_reservation(
        self, db_session, make_capacity_reservation
    ):
        import json

        from app.modules.storage.capacity import _process_identity

        make_capacity_reservation(
            expired=True, owner_identity_json=json.dumps(_process_identity())
        )
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        assert manager.reconcile_stopped_processes() == 0
        assert manager.reserved_bytes() == {"quota:test": 10}

    def test_retains_unknown_process_identity(
        self, db_session, make_capacity_reservation
    ):
        make_capacity_reservation(expired=True)
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        assert manager.reconcile_stopped_processes() == 0
        assert manager.reserved_bytes() == {"quota:test": 10}

    def test_refuses_renewal_after_release(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        handle = manager.reserve(
            "lost", [CapacityResource.for_quota("one", 10, 100, role="test")]
        )
        handle.release()
        with pytest.raises(OperationError, match="capacity_reservation_lost"):
            handle.renew()

    def test_rejects_invalid_operation_identity(self, db_session):
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        with pytest.raises(ValueError, match="invalid capacity reservation"):
            manager.reserve(
                "", [CapacityResource.for_quota("one", 10, 100, role="test")]
            )

    def test_fails_closed_when_volume_probe_is_unavailable(
        self, db_session, tmp_path, monkeypatch
    ):
        import os

        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        resource = CapacityResource.for_path(tmp_path, 10, role="staging")

        def unavailable(path):
            raise OSError("unavailable")

        monkeypatch.setattr(os, "statvfs", unavailable)
        with pytest.raises(OperationError, match="storage_capacity_unavailable"):
            manager.reserve("probe", [resource])


class TestPercentageHeadroom:
    def test_prevents_allocating_reserved_percentage(self, db_session):
        manager = CapacityManager(
            get_session_factory(), headroom_bytes=0, headroom_percent=10
        )
        resource = CapacityResource.for_quota(
            "one", 91, 100, role="vault", total_bytes=100
        )

        with pytest.raises(OperationError, match="storage_capacity_exceeded") as denied:
            manager.reserve("percent-upload", [resource])

        assert denied.value.capacity == {
            "required_bytes": 91,
            "available_bytes": 100,
            "reserved_bytes": 0,
            "headroom_bytes": 10,
        }
        assert manager.reserved_bytes() == {}

    def test_deduplicates_percentage_for_shared_domain(self, db_session):
        manager = CapacityManager(
            get_session_factory(), headroom_bytes=0, headroom_percent=10
        )
        resources = [
            CapacityResource.for_quota("one", 45, 100, role=role, total_bytes=100)
            for role in ("input", "output")
        ]

        handle = manager.reserve("shared-percent", resources)

        assert manager.reserved_bytes() == {"quota:one": 90}
        handle.release()


class TestDurableCapacityLifetime:
    @pytest.mark.parametrize(
        "operation_id",
        [
            "artifact-upload:old-session",
            "vault-migration:old-run",
            "postgres-restore:old-backup",
        ],
    )
    def test_preserves_legacy_workflow_budget_after_process_death(
        self, db_session, make_capacity_reservation, operation_id
    ):
        import json

        from app.modules.storage.capacity import _process_identity

        identity = {**_process_identity(), "boot": "previous-boot"}
        make_capacity_reservation(
            expired=True,
            operation_id=operation_id,
            owner_identity_json=json.dumps(identity),
        )
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)

        assert manager.reconcile_stopped_processes() == 0
        assert manager.reserved_bytes() == {"quota:test": 10}

    def test_preserves_explicit_durable_budget_after_process_death(
        self, db_session, make_capacity_reservation
    ):
        import json

        from app.modules.storage.capacity import _process_identity

        identity = {
            **_process_identity(),
            "boot": "previous-boot",
            "lifetime": "workflow",
        }
        make_capacity_reservation(
            expired=True,
            operation_id="durable-owner",
            owner_identity_json=json.dumps(identity),
        )
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)

        assert manager.reconcile_stopped_processes() == 0
        assert manager.reserved_bytes() == {"quota:test": 10}
        assert manager.reconcile(lambda operation_id: False) == 1

    def test_records_durable_lifetime_on_reservation(self, db_session):
        import json

        from app.db.models import CapacityReservation

        manager = CapacityManager(get_session_factory(), headroom_bytes=0)
        handle = manager.reserve(
            "durable",
            [CapacityResource.for_quota("one", 10, 100, role="input")],
            durable=True,
        )
        handle.renew()

        row = db_session.get(CapacityReservation, "durable")
        assert json.loads(row.owner_identity_json)["lifetime"] == "workflow"
        handle.release()


class TestLegacyCapacityReservation:
    def test_reuses_without_total_evidence(self, db_session, make_capacity_reservation):
        row = make_capacity_reservation(operation_id="legacy")
        manager = CapacityManager(get_session_factory(), headroom_bytes=0)

        handle = manager.reserve(
            "legacy", [CapacityResource.for_quota("test", 10, 100, role="test")]
        )

        assert manager.reserved_bytes() == {"quota:test": 10}
        assert handle.operation_id == row.operation_id
