"""Audit scheduling accepts only a literal minute within a wall-clock day.

Python ISO parsing may normalize24:00; an audit policy must reject that value
rather than silently schedule a different minute.
"""

import pytest
from pydantic import ValidationError

from app.schemas.audit_policy import AuditPolicyUpdate


class TestAuditPolicyUpdate:
    @pytest.mark.parametrize(
        "value", ["00:00", "23:59"], ids=["first-minute", "last-minute"]
    )
    def test_preserves_valid_start_time(self, value: str) -> None:
        policy = AuditPolicyUpdate(start_time=value)

        assert policy.start_time == value

    @pytest.mark.parametrize(
        "value",
        ["24:00", "23:60", "2:00"],
        ids=["next-day", "minute-overflow", "short-hour"],
    )
    def test_rejects_invalid_start_time(self, value: str) -> None:
        with pytest.raises(ValidationError, match="audit_time_invalid") as error:
            AuditPolicyUpdate(start_time=value)

        assert error.value.errors()[0]["loc"] == ("start_time",)
