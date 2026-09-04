"""The limiter in front of login, and the memory it must not grow.

Rate limiting is what stops a password from being guessed at machine speed, so
the window arithmetic gets its own rows: up to the limit is allowed, past it is
blocked, and old hits expire so a legitimate user is not locked out for the rest
of the day by one bad afternoon. Keys are independent, because a global limit
means one attacker locks out every user.

The cardinality row is the one that is easy to miss. The limiter keys on client
IP, which an attacker controls and can churn. Without a bound, every new IP adds
an entry that is never collected — a slow memory leak reachable from
unauthenticated requests, which is a denial of service against the whole process
rather than against the endpoint it was protecting.

A non-positive configuration is refused rather than silently treated as
unlimited: a typo in a setting must not disable the protection.
"""

from __future__ import annotations

import time

import pytest

import app.core.ratelimit as ratelimit
from app.core.ratelimit import RateLimiter


class TestRateLimiter:
    @pytest.mark.parametrize(
        ("limit", "window_s", "max_keys"),
        [(0, 60.0, 100), (1, 0.0, 100), (1, 60.0, 0)],
    )
    def test_rejects_non_positive_configuration(self, limit, window_s, max_keys):
        with pytest.raises(ValueError, match="must be positive"):
            RateLimiter(limit=limit, window_s=window_s, max_keys=max_keys)

    def test_allows_up_to_limit(self):
        limiter = RateLimiter(limit=3, window_s=60.0)
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is True

    def test_blocks_after_limit(self):
        limiter = RateLimiter(limit=2, window_s=60.0)
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is False

    def test_keys_are_independent(self):
        limiter = RateLimiter(limit=1, window_s=60.0)
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("5.6.7.8") is True
        assert limiter.check("1.2.3.4") is False
        assert limiter.check("5.6.7.8") is False

    def test_window_expires_old_hits(self):
        limiter = RateLimiter(limit=1, window_s=0.05)
        assert limiter.check("1.2.3.4") is True
        assert limiter.check("1.2.3.4") is False
        time.sleep(0.06)
        assert limiter.check("1.2.3.4") is True

    def test_key_cardinality_is_bounded_under_ip_churn(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        now = 0.0
        monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now)
        limiter = RateLimiter(limit=2, window_s=60.0, max_keys=100)

        for index in range(10_000):
            assert limiter.check(f"198.51.{index // 256}.{index % 256}") is True

        assert limiter.key_count <= 100

    def test_expired_keys_are_removed_before_capacity_eviction(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        now = 0.0
        monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now)
        limiter = RateLimiter(limit=1, window_s=10.0, max_keys=2)
        limiter.check("old-a")
        limiter.check("old-b")

        now = 11.0
        limiter.check("new")

        assert limiter.key_count == 1

    def test_reset_clears_all_keys(self):
        limiter = RateLimiter(limit=1, window_s=60.0)
        limiter.check("1.2.3.4")
        assert limiter.check("1.2.3.4") is False
        limiter.reset()
        assert limiter.check("1.2.3.4") is True
