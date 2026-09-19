"""IANA identifiers stay within the public preference budget."""

import pytest

from app.core.timezones import timezone_name


class TestTimezoneName:
    @pytest.mark.parametrize("value", ["", "x" * 129])
    def test_rejects_invalid_lengths(self, value):
        with pytest.raises(ValueError, match="timezone_invalid"):
            timezone_name(value)
