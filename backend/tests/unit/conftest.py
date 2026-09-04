"""``tests/unit/`` — one function, nothing real behind it.

A unit test here exercises pure logic: parsers, hashing, URL safety, state machines, and
the reaction of our code to a dependency that misbehaves on cue. It gets no database, no
router, and no network. Both guards below are autouse, so a test that drifts out of the
tier fails here rather than passing for the wrong reason.
"""

from __future__ import annotations

import pytest

from tests._guards import block_real_network, forbid_db_fixtures  # noqa: F401 — autouse


@pytest.fixture(autouse=True)
def _no_db_fixtures(request: pytest.FixtureRequest) -> None:
    forbid_db_fixtures(request)
