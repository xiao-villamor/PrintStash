"""A catalog where some work adds to the library and some waits for it.

Shared by the catalog, reconciler and runner tests of ``adds_to_library`` and
``waits_for_additions``.
"""

from __future__ import annotations

import pytest

from app.db.models import JobKind, LaneName
from app.modules.work import catalog as catalog_module
from app.modules.work.catalog import Lane, WorkCatalog, default_lanes
from app.modules.work.contracts import JobDefinition, Step, WorkItem
from app.runtime.engine.inline import InlineJobEngine

ADDING = JobKind.INGESTION_UPLOAD
ALSO_ADDING = JobKind.SOURCES_SCAN
WAITING = JobKind.DERIVATIVES_MESH
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
    """Two adding definitions, one that waits for them, one that does not."""
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
            definition(WAITING, source=_OnePending(), waits_for_additions=True),
            definition(EAGER, source=_OnePending()),
        ],
        lanes={**default_lanes(), lane: Lane(lane, 4)},
    )
    built = InlineJobEngine(catalog)
    built.launch(listen_lanes=None)
    catalog_module.bind(built, catalog)
    return built
