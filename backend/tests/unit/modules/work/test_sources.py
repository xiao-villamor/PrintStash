"""Schedules as cron expressions: which occurrence is due, and when the next is."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.work.sources import (
    fixed,
    interval_cron,
    latest_occurrence,
    next_occurrence,
)


class TestLatestOccurrence:
    def test_an_instant_on_the_schedule_is_its_own_occurrence(self) -> None:
        now = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)

        assert latest_occurrence("0 3 * * *", now=now) == now

    def test_between_firings_the_previous_one_is_due(self) -> None:
        now = datetime(2026, 9, 24, 14, 30, tzinfo=UTC)

        assert latest_occurrence("0 3 * * *", now=now) == datetime(
            2026, 9, 24, 3, 0, tzinfo=UTC
        )

    def test_a_naive_instant_is_read_as_utc(self) -> None:
        naive = datetime(2026, 9, 24, 14, 30)

        assert latest_occurrence("0 * * * *", now=naive).tzinfo is not None


class TestNextOccurrence:
    def test_is_strictly_after_now(self) -> None:
        now = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)

        assert next_occurrence("0 3 * * *", now=now) == datetime(
            2026, 9, 25, 3, 0, tzinfo=UTC
        )


class TestIntervalCron:
    @pytest.mark.parametrize(
        ("seconds", "expression"),
        [
            (30, "*/1 * * * *"),
            (300, "*/5 * * * *"),
            (3600, "0 */1 * * *"),
            (6 * 3600, "0 */6 * * *"),
            (86400, "0 0 * * *"),
            (7 * 86400, "0 0 * * *"),
        ],
    )
    def test_maps_an_interval_onto_the_nearest_cron(
        self, seconds: int, expression: str
    ) -> None:
        assert interval_cron(seconds) == expression

    @pytest.mark.parametrize(
        ("seconds", "expression"),
        [
            pytest.param(7 * 60, "*/6 * * * *", id="7min-to-6min"),
            pytest.param(50 * 60, "*/30 * * * *", id="50min-to-30min"),
            pytest.param(5 * 3600, "0 */4 * * *", id="5h-to-4h"),
        ],
    )
    def test_an_uneven_interval_rounds_down_to_an_even_cadence(
        self, seconds: int, expression: str
    ) -> None:
        # */7 minutes fires at :56 and again at :00, a four-minute gap.
        assert interval_cron(seconds) == expression


class TestFixed:
    def test_is_always_on(self) -> None:
        assert fixed("25 * * * *")(None) == "25 * * * *"  # type: ignore[arg-type]
