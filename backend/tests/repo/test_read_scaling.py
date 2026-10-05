"""The library page's reads do the same work whether the library is small or large.

Issue #295: a 9k-collection library took over a minute to open, and every test
passed, because the suite's libraries hold a handful of rows and quadratic work
is fast at a handful. Wall-clock is no use in a PR run: it varies by machine and
a few rows never show it. The *shape* of a read does not vary: how many
statements it runs, and how many parameters the largest one binds. A read that
queries once per row, or binds the visible id list into a query, changes that
shape as the library grows ten times. These tests grow it ten times and assert
nothing changed.

The reads come from ``tests/_library_reads.py``; the ``reader`` fixture (an
administrator and a granted viewer) from this directory's ``conftest.py``.
Wall-clock budgets at the supported scale are in ``test_read_scale_budgets.py``,
in the ``scale`` lane.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import Collection
from tests._library_reads import LIBRARY_READS
from tests._statements import StatementLog
from tests.factories.library_scale import build_library_at_scale

SMALL = {"collections": 10, "models": 30}
TEN_TIMES_MORE = {"collections": 90, "models": 270}


def _read(
    client: TestClient,
    log: StatementLog,
    path: str,
    params: dict[str, Any],
    headers: dict[str, str],
) -> StatementLog:
    with log.recording():
        response = client.get(path, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return log


class TestLibraryReads:
    @pytest.mark.parametrize(("path", "params"), LIBRARY_READS)
    def test_runs_as_many_statements_at_ten_times_the_size(
        self,
        client: TestClient,
        db_session: Session,
        sql_statements: StatementLog,
        reader: tuple[dict[str, str], Collection],
        path: str,
        params: dict[str, Any],
    ) -> None:
        headers, root = reader
        seeded = build_library_at_scale(db_session, under=root, **SMALL)
        if path == "/api/v1/outliner/entries":
            params = params | {"collection_id": seeded.collection_ids[0]}
        client.get(path, params=params, headers=headers)  # warm per-process caches
        small = _read(client, sql_statements, path, params, headers).count
        build_library_at_scale(db_session, under=root, **TEN_TIMES_MORE)

        large = _read(client, sql_statements, path, params, headers).count

        assert large == small, "a read issues a statement per row it returns"

    @pytest.mark.parametrize(("path", "params"), LIBRARY_READS)
    def test_binds_as_many_parameters_at_ten_times_the_size(
        self,
        client: TestClient,
        db_session: Session,
        sql_statements: StatementLog,
        reader: tuple[dict[str, str], Collection],
        path: str,
        params: dict[str, Any],
    ) -> None:
        headers, root = reader
        seeded = build_library_at_scale(db_session, under=root, **SMALL)
        if path == "/api/v1/outliner/entries":
            params = params | {"collection_id": seeded.collection_ids[0]}
        client.get(path, params=params, headers=headers)
        small = _read(client, sql_statements, path, params, headers)
        small_bound = small.max_bound_parameters
        build_library_at_scale(db_session, under=root, **TEN_TIMES_MORE)

        large = _read(client, sql_statements, path, params, headers)

        assert large.max_bound_parameters == small_bound, (
            "a read binds a parameter per visible row; filter by a subquery"
        )
