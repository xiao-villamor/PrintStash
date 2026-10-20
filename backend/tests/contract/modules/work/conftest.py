"""Work contract environments: engine choice and matched database backends."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.runtime.engine.dbos_engine import system_database_url
from tests.containers import fresh_postgres_database
from tests.contract.modules.work._harness import Harness, harness_for, shared_app_db


@pytest.fixture(params=["inline", "dbos"])
def harness(request, tmp_path) -> Iterator[Harness]:
    with shared_app_db(), harness_for(request.param, tmp_path) as built:
        yield built


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def dbos_databases(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[str, tuple[str, str | None]]:
    """Application and DBOS persistence both use the selected real dialect."""
    if request.param == "sqlite":
        return (
            f"sqlite:///{tmp_path / 'application.sqlite'}",
            (f"sqlite:///{tmp_path / 'engine.sqlite'}", None),
        )
    url = fresh_postgres_database("work_lookahead")
    return url, system_database_url(url)
