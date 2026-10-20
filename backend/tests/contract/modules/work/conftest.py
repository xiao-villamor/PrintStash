"""The engine harness fixture: every contract test runs on both engines."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.contract.modules.work._harness import Harness, harness_for, shared_app_db


@pytest.fixture(params=["inline", "dbos"])
def harness(request, tmp_path) -> Iterator[Harness]:
    with shared_app_db(), harness_for(request.param, tmp_path) as built:
        yield built
