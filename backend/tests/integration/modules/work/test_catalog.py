"""The catalog of lanes and definitions one process runs, and its binding.

A catalog has every lane, and refuses definitions that could not run: a
duplicate name, one on the reconcile lane (a pass is not a Job), or a partition
rule that does not match its lane. Runtime lane overrides replace the
configured concurrency and are reapplied on every launch, because the engine's
copy is disposable.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.core.config import _overlay
from app.db.models import JobKind, LaneName, WorkLaneOverride
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import WorkCatalog, default_lanes
from app.modules.work.contracts import JobDefinition, Step

PROBE = JobKind.SOURCES_SCAN


def _definition(
    name: JobKind = PROBE, lane: LaneName = LaneName.INGEST, **kw
) -> JobDefinition:
    return JobDefinition(
        name=name,
        lane=lane,
        steps=(Step(f"{name}.run", lambda _: None),),
        label="Probe",
        **kw,
    )


class TestDefaultLanes:
    def test_defines_every_lane(self) -> None:
        assert set(default_lanes()) == set(LaneName)

    def test_reconciles_on_its_own_global_lane(self) -> None:
        assert default_lanes()[LaneName.RECONCILE].scope == "global"

    def test_notifications_are_throttled_per_channel(self) -> None:
        lane = default_lanes()[LaneName.NOTIFY]

        assert lane.partitioned is True
        assert lane.rate_limit is not None

    def test_native_derivation_follows_the_render_budget(self) -> None:
        _overlay["jobs_derive_native_concurrency"] = None
        _overlay["max_render_jobs"] = 3

        assert default_lanes()[LaneName.DERIVE_NATIVE].concurrency == 3

    def test_an_explicit_native_concurrency_wins(self) -> None:
        _overlay["jobs_derive_native_concurrency"] = 5

        assert default_lanes()[LaneName.DERIVE_NATIVE].concurrency == 5


class TestWorkCatalog:
    def test_looks_a_definition_up_by_name(self) -> None:
        catalog = WorkCatalog([_definition()])

        assert catalog.definition(PROBE).lane is LaneName.INGEST
        assert catalog.lane_for(PROBE).name is LaneName.INGEST

    def test_a_definition_this_process_lacks_is_a_lookup_error(self) -> None:
        with pytest.raises(LookupError, match="unknown_job_definition:sources.scan"):
            WorkCatalog().definition(PROBE)

    def test_refuses_a_duplicate_definition(self) -> None:
        with pytest.raises(ValueError, match="duplicate_job_definition"):
            WorkCatalog([_definition(), _definition()])

    def test_refuses_a_catalog_missing_a_lane(self) -> None:
        lanes = default_lanes()
        del lanes[LaneName.SEARCH]

        with pytest.raises(ValueError, match="lanes_missing:search"):
            WorkCatalog(lanes=lanes)

    def test_refuses_a_job_on_the_reconcile_lane(self) -> None:
        with pytest.raises(ValueError, match="reserved_lane"):
            WorkCatalog([_definition(lane=LaneName.RECONCILE)])

    def test_a_partitioned_lane_requires_a_partition(self) -> None:
        with pytest.raises(ValueError, match="partition_mismatch"):
            WorkCatalog([_definition(lane=LaneName.NOTIFY)])

    def test_an_ordinary_lane_refuses_a_partition(self) -> None:
        with pytest.raises(ValueError, match="partition_mismatch"):
            WorkCatalog([_definition(partition=lambda subject: subject)])

    def test_applies_runtime_overrides_over_configured_concurrency(
        self, db_session: Session
    ) -> None:
        db_session.add(WorkLaneOverride(lane=LaneName.INGEST, concurrency=9))
        db_session.commit()
        catalog = WorkCatalog()

        catalog.apply_overrides(db_session)

        assert catalog.lanes[LaneName.INGEST].concurrency == 9

    def test_a_removed_override_restores_the_configured_value(
        self, db_session: Session
    ) -> None:
        catalog = WorkCatalog()
        db_session.add(WorkLaneOverride(lane=LaneName.INGEST, concurrency=9))
        db_session.commit()
        catalog.apply_overrides(db_session)
        db_session.delete(db_session.get(WorkLaneOverride, LaneName.INGEST))
        db_session.commit()

        catalog.apply_overrides(db_session)

        assert (
            catalog.lanes[LaneName.INGEST].concurrency
            == default_lanes()[LaneName.INGEST].concurrency
        )


class TestBinding:
    def test_an_unbound_process_has_no_catalog(self) -> None:
        catalog_module.bind(None, None)

        assert catalog_module.bound() is False
        with pytest.raises(RuntimeError, match="work_catalog_unbound"):
            catalog_module.get_catalog()
        with pytest.raises(RuntimeError, match="job_engine_unbound"):
            catalog_module.get_engine()

    def test_binding_makes_both_available(self, work_engine, work_catalog) -> None:
        catalog_module.bind(work_engine, work_catalog)

        assert catalog_module.bound() is True
        assert catalog_module.get_engine() is work_engine


class TestQueueOrder:
    def test_only_derivation_defaults_to_fifo(self):
        from app.modules.work.contracts import LaneOrder

        lanes = default_lanes()
        assert {
            name for name, lane in lanes.items() if lane.queue_order is LaneOrder.FIFO
        } == {
            LaneName.DERIVE_NATIVE,
            LaneName.DERIVE_LIGHT,
        }
        assert all(
            lane.queue_order is LaneOrder.PRIORITY
            for name, lane in lanes.items()
            if name not in {LaneName.DERIVE_NATIVE, LaneName.DERIVE_LIGHT}
        )

    def test_runtime_concurrency_override_preserves_lane_policy(self, db_session):
        from dataclasses import replace

        from app.modules.work.contracts import LaneOrder

        catalog = WorkCatalog()
        original = catalog.lanes[LaneName.DERIVE_NATIVE]
        assert original.queue_order is LaneOrder.FIFO
        db_session.add(WorkLaneOverride(lane=LaneName.DERIVE_NATIVE, concurrency=7))
        db_session.commit()

        catalog.apply_overrides(db_session)

        assert catalog.lanes[LaneName.DERIVE_NATIVE] == replace(original, concurrency=7)
