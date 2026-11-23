"""Catalogs where some work adds to the library or runs for a person, and
other work holds back for it.

Shared by the catalog, reconciler, runner and submission tests of
``adds_to_library``, ``during_additions`` and ``yields_to``.
"""

from __future__ import annotations

import pytest

from app.db.models import JobKind, LaneName
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import Lane, WorkCatalog, default_lanes
from app.modules.work.contracts import DuringAdditions, JobDefinition, Step, WorkItem
from app.runtime.engine.inline import InlineJobEngine

ADDING = JobKind.INGESTION_UPLOAD
ALSO_ADDING = JobKind.SOURCES_SCAN
WAITING = JobKind.DERIVATIVES_MESH
PACED = JobKind.DERIVATIVES_GCODE
EAGER = JobKind.DERIVATIVES_VIEWER_STL


class _OnePending:
    def pending(self, session, *, now, limit):
        return [WorkItem("artifact/1")][:limit]

    def next_due(self, session, *, now):
        return None


def _noop(_ctx) -> None:
    return None


@pytest.fixture
def additions_engine() -> InlineJobEngine:
    """Two adding definitions; one that waits, one paced and one that runs."""
    lane = LaneName.MAINTENANCE

    def definition(name: JobKind, **kw) -> JobDefinition:
        return JobDefinition(
            name=name,
            lane=lane,
            steps=(Step(f"{name}.run", _noop),),
            label=name.value,
            **kw,
        )

    catalog = WorkCatalog(
        [
            definition(ADDING, adds_to_library=True),
            definition(ALSO_ADDING, adds_to_library=True),
            definition(
                WAITING, source=_OnePending(), during_additions=DuringAdditions.WAITS
            ),
            definition(
                PACED, source=_OnePending(), during_additions=DuringAdditions.PACED
            ),
            definition(EAGER, source=_OnePending()),
        ],
        lanes={**default_lanes(), lane: Lane(lane, 4)},
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built


INTERACTIVE_WORK = JobKind.DERIVATIVES_MESH
YIELDING = JobKind.SEARCH_PROJECT


@pytest.fixture
def yielding_engine() -> InlineJobEngine:
    """One definition whose discovery yields to interactive Jobs of another."""
    lane = LaneName.MAINTENANCE

    def definition(name: JobKind, **kw) -> JobDefinition:
        return JobDefinition(
            name=name,
            lane=lane,
            steps=(Step(f"{name}.run", _noop),),
            label=name.value,
            **kw,
        )

    catalog = WorkCatalog(
        [
            definition(INTERACTIVE_WORK, source=_OnePending()),
            definition(YIELDING, source=_OnePending(), yields_to=(INTERACTIVE_WORK,)),
        ],
        lanes={**default_lanes(), lane: Lane(lane, 4)},
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built
