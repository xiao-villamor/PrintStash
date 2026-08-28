"""The one fixture every file in this folder needs.

`mesh_processing` has two independent ceilings: a static triangle/byte cap from
configuration, and a RAM-aware cap derived from the host's memory. Both are live
by default, which is right in production and wrong in a test — a static-cap
assertion would pass on a big CI runner and fail on a small one, or the reverse,
for reasons unrelated to what it asserts.

So the RAM-aware cap is off by default here and the tests that are *about* it
turn it back on explicitly with a pinned memory limit.
"""

from __future__ import annotations

import pytest

from app.core.config import _overlay


@pytest.fixture(autouse=True)
def static_cap_only():
    """Disable the RAM-aware cap so static-cap outcomes are host-independent."""

    previous = _overlay.get("mesh_memory_budget_fraction", "__unset__")
    _overlay["mesh_memory_budget_fraction"] = 0
    yield
    if previous == "__unset__":
        _overlay.pop("mesh_memory_budget_fraction", None)
    else:
        _overlay["mesh_memory_budget_fraction"] = previous
