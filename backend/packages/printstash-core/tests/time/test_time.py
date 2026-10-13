"""Two helpers that keep every stored timestamp in one timezone.

PrintStash runs against SQLite by default, and SQLite has no timezone type: a
`datetime` written aware comes back *naive*. Compare one of those to
`datetime.now(timezone.utc)` and Python raises a `TypeError` about comparing
offset-naive and offset-aware datetimes — a 500, not a wrong answer. That is not
hypothetical: two live 500s in the staging-lease and external-library scan paths
were exactly this, and both were fixed by routing the comparison through
`ensure_utc`.

So these two functions are the reason the rest of the codebase can treat time as
one thing. `utcnow()` is the only clock the application reads, and `ensure_utc`
is what every value coming back out of the database goes through before it is
compared. The tests below pin the two properties that matter: the result is
always aware, and it is always *UTC* rather than merely aware — a value that kept
its original offset would compare correctly and then render in the wrong
timezone.

A naive value is assumed to already be UTC rather than local. That is the right
assumption here because the only naive datetimes in the system come from SQLite,
where they were written as UTC; interpreting them as local time would shift every
stored timestamp by the host's offset.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from printstash_core.time import ensure_utc, parse_hh_mm_time, utcnow


class TestUtcnow:
    def test_reports_the_current_time(self) -> None:
        before = datetime.now(timezone.utc)

        current = utcnow()

        after = datetime.now(timezone.utc)
        assert before <= current <= after

    def test_reports_it_as_timezone_aware_utc(self) -> None:
        # The whole point: an aware value can be compared with anything else
        # that went through `ensure_utc`, and a naive one cannot.
        assert utcnow().tzinfo is timezone.utc


class TestEnsureUtc:
    def test_reads_a_naive_value_as_utc(self) -> None:
        naive = datetime(2026, 8, 20, 12, 30, 45)

        # Naive values reach the application from SQLite, which drops the
        # timezone on write. They were UTC when stored, so reading them as local
        # time would shift every stored timestamp by the host offset.
        assert ensure_utc(naive) == datetime(
            2026, 8, 20, 12, 30, 45, tzinfo=timezone.utc
        )

    def test_converts_a_value_from_another_offset(self) -> None:
        source = datetime(2026, 8, 20, 12, 30, tzinfo=timezone(timedelta(hours=2)))

        assert ensure_utc(source) == datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)

    def test_returns_utc_rather_than_merely_something_aware(self) -> None:
        source = datetime(2026, 8, 20, 12, 30, tzinfo=timezone(timedelta(hours=2)))

        # A value that kept its original offset would compare correctly and then
        # render in the wrong timezone.
        assert ensure_utc(source).tzinfo is timezone.utc

    def test_leaves_a_value_that_is_already_utc_unchanged(self) -> None:
        source = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)

        assert ensure_utc(source) == source


class TestParseHhMmTime:
    @pytest.mark.parametrize(
        "value", ["00:00", "23:59"], ids=["first-minute", "last-minute"]
    )
    def test_accepts_a_minute_of_the_day(self, value: str) -> None:
        parsed = parse_hh_mm_time(value)

        assert parsed.isoformat(timespec="minutes") == value
        assert parsed.tzinfo is None

    @pytest.mark.parametrize(
        "value",
        ["24:00", "99:00", "23:60", "00:99"],
        ids=["next-day", "hour-overflow", "minute-overflow", "minute-maximum"],
    )
    def test_rejects_a_minute_outside_the_day(self, value: str) -> None:
        with pytest.raises(ValueError, match="time_hh_mm_invalid"):
            parse_hh_mm_time(value)

    @pytest.mark.parametrize(
        "value",
        ["", "invalid", "2:00", "00:0", "02:00:00", "02:00Z", "００:００", "00000"],
        ids=[
            "empty",
            "text",
            "short-hour",
            "short-minute",
            "seconds",
            "timezone",
            "non-ascii",
            "no-colon",
        ],
    )
    def test_rejects_non_hh_mm_text(self, value: str) -> None:
        with pytest.raises(ValueError, match="time_hh_mm_invalid"):
            parse_hh_mm_time(value)
