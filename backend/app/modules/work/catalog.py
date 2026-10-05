"""The catalog of lanes and job definitions, and the process-wide binding.

Definitions live with the module that owns their work (``<module>/jobs.py``)
and are collected here by composition at startup. Nothing registers itself at
import time: the catalog a process runs is exactly the one bootstrap built.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import JobKind, LaneName, WorkLaneOverride

from .contracts import JobDefinition, JobEngine, Lane, LaneOrder

# How many reconcile passes run at once, deployment-wide.
RECONCILE_CONCURRENCY = 4


def _native_default() -> int:
    """One native process per render slot unless configured on its own."""
    if settings.jobs_derive_native_concurrency is not None:
        return settings.jobs_derive_native_concurrency
    return max(1, settings.max_render_jobs)


def default_lanes() -> dict[LaneName, Lane]:
    """Every lane with its configured (environment) concurrency."""
    lanes = (
        Lane(LaneName.INGEST, settings.jobs_ingest_concurrency),
        Lane(LaneName.DERIVE_NATIVE, _native_default(), queue_order=LaneOrder.FIFO),
        Lane(
            LaneName.DERIVE_LIGHT,
            settings.jobs_derive_light_concurrency,
            queue_order=LaneOrder.FIFO,
        ),
        Lane(LaneName.SIMILARITY, settings.jobs_similarity_concurrency, scope="global"),
        Lane(LaneName.NETWORK, settings.jobs_network_concurrency),
        Lane(
            LaneName.NOTIFY,
            settings.jobs_notify_concurrency,
            partitioned=True,
            rate_limit=(settings.jobs_notify_rate_per_minute, 60.0),
        ),
        # Fleet routing is one fleet-wide decision: one dispatcher, anywhere.
        Lane(LaneName.PRINTING, settings.jobs_printing_concurrency, scope="global"),
        Lane(
            LaneName.MAINTENANCE, settings.jobs_maintenance_concurrency, scope="global"
        ),
        Lane(LaneName.SEARCH, settings.jobs_search_concurrency, scope="global"),
        Lane(LaneName.CAPTIONS, settings.jobs_captions_concurrency, scope="global"),
        Lane(LaneName.EXPANSION, settings.jobs_expansion_concurrency, scope="global"),
        Lane(LaneName.RECONCILE, RECONCILE_CONCURRENCY, scope="global"),
    )
    return {lane.name: lane for lane in lanes}


class WorkCatalog:
    """Lanes and definitions one process knows how to run."""

    def __init__(
        self,
        definitions: Iterable[JobDefinition] = (),
        *,
        lanes: Mapping[LaneName, Lane] | None = None,
    ) -> None:
        self.lanes: dict[LaneName, Lane] = dict(
            default_lanes() if lanes is None else lanes
        )
        missing = set(LaneName) - set(self.lanes)
        if missing:
            raise ValueError(f"lanes_missing:{','.join(sorted(missing))}")
        self.definitions: dict[JobKind, JobDefinition] = {}
        for definition in definitions:
            self.add(definition)

    def add(self, definition: JobDefinition) -> None:
        if definition.lane is LaneName.RECONCILE:
            raise ValueError(f"reserved_lane:{definition.name.value}")
        if definition.name in self.definitions:
            raise ValueError(f"duplicate_job_definition:{definition.name.value}")
        if self.lanes[definition.lane].partitioned != (
            definition.partition is not None
        ):
            # A partitioned lane routes by partition and nothing else can.
            raise ValueError(f"partition_mismatch:{definition.name.value}")
        self.definitions[definition.name] = definition

    def definition(self, name: JobKind) -> JobDefinition:
        """The definition behind ``name``; a process that lacks it cannot run it."""
        try:
            return self.definitions[name]
        except KeyError:
            raise LookupError(f"unknown_job_definition:{name.value}") from None

    def lane_for(self, definition: JobKind) -> Lane:
        return self.lanes[self.definition(definition).lane]

    def apply_overrides(self, session: Session) -> None:
        """Replace configured concurrency with administrators' runtime values."""
        defaults = default_lanes()
        overrides = {
            row.lane: row.concurrency
            for row in session.exec(select(WorkLaneOverride)).all()
        }
        for name, lane in list(self.lanes.items()):
            self.lanes[name] = replace(
                lane, concurrency=overrides.get(name, defaults[name].concurrency)
            )


_catalog: WorkCatalog | None = None
_engine: JobEngine | None = None


def bind(engine: JobEngine | None, catalog: WorkCatalog | None) -> None:
    """Bind this process's engine and catalog; ``None`` unbinds."""
    global _catalog, _engine
    _engine = engine
    _catalog = catalog


def get_catalog() -> WorkCatalog:
    if _catalog is None:
        raise RuntimeError("work_catalog_unbound")
    return _catalog


def get_engine() -> JobEngine:
    if _engine is None:
        raise RuntimeError("job_engine_unbound")
    return _engine


def bound() -> bool:
    return _engine is not None and _catalog is not None
