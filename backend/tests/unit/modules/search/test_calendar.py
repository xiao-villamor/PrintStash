"""The server owns calendar boundaries, including clock transitions."""

from datetime import datetime, timezone

import pytest

from app.modules.search.calendar import bounds


class TestBounds:
    @pytest.mark.parametrize(
        ("period", "now", "zone", "expected"),
        [
            (
                "last_month",
                "2026-04-15T12:00:00+00:00",
                "Europe/Madrid",
                ("2026-02-28T23:00:00+00:00", "2026-03-31T22:00:00+00:00"),
            ),
            (
                "last_month",
                "2026-01-02T12:00:00+00:00",
                "UTC",
                ("2025-12-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            ),
            (
                "today",
                "2026-03-08T12:00:00+00:00",
                "America/New_York",
                ("2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
            ),
            (
                "yesterday",
                "2026-11-02T12:00:00+00:00",
                "America/New_York",
                ("2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
            ),
            (
                "this_month",
                "2026-09-12T12:00:00+00:00",
                "UTC",
                ("2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"),
            ),
            (
                "last_week",
                "2026-09-12T12:00:00+00:00",
                "UTC",
                ("2026-08-31T00:00:00+00:00", "2026-09-07T00:00:00+00:00"),
            ),
            (
                "this_week",
                "2026-09-12T12:00:00+00:00",
                "UTC",
                ("2026-09-07T00:00:00+00:00", "2026-09-14T00:00:00+00:00"),
            ),
            (
                "past_30_days",
                "2026-09-12T12:00:00+00:00",
                "UTC",
                ("2026-08-13T12:00:00+00:00", "2026-09-12T12:00:00+00:00"),
            ),
        ],
    )
    def test_resolves_calendar_boundaries(self, period, now, zone, expected):
        assert (
            tuple(
                value.isoformat()
                for value in bounds(period, datetime.fromisoformat(now), zone)
            )
            == expected
        )

    def test_rejects_unknown_period(self):
        with pytest.raises(ValueError, match="search_period_invalid"):
            bounds("made_up", datetime(2026, 1, 1, tzinfo=timezone.utc), "UTC")
