"""Every rate-limited route must be discoverable, so the suite can reset it.

A limiter built by `rate_limit()` holds one process-wide window. If the test suite does
not reset it between tests, a test that exhausts one hands the next test a 429 out of
nowhere — and only when the two land on the same xdist worker in the same order, which is
the worst kind of flake to chase. `tests/conftest.py` resets them by walking the app's
route tree rather than from a hand-maintained list.

This file defends that walk. FastAPI's internal route shapes are what it reaches through,
so an upgrade that changes them would otherwise turn the reset into a silent no-op and the
flake would come back with no failing test to point at.
"""

from __future__ import annotations

from app.core.ratelimit import RateLimiter
from app.main import app
from tests.conftest import _rate_limiters_in


class TestRateLimiterIsolation:
    def test_the_apps_rate_limiters_are_reachable_from_its_route_tree(self) -> None:
        found = list(_rate_limiters_in(app))

        assert found, (
            "no rate limiters found — the reset in tests/conftest.py is a no-op and "
            "rate-limit state now leaks between tests"
        )
        assert all(isinstance(limiter, RateLimiter) for limiter in found)

    def test_every_rate_limited_route_is_found(self) -> None:
        from app.api.v1.auth import _login_rate_limit, _refresh_rate_limit
        from app.api.v1.provider_connections import _claim_limit

        found = {id(limiter) for limiter in _rate_limiters_in(app)}

        for dependency in (_login_rate_limit, _refresh_rate_limit, _claim_limit):
            assert id(dependency.limiter) in found, dependency  # type: ignore[attr-defined]
