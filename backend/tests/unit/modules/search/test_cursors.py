"""Pagination can restart after cutover without treating forged input as valid."""

import pytest

from app.core.errors import ErrorKind, OperationError
from app.modules.search import cursors


class TestCursors:
    def test_distinguishes_generation_expiry_from_an_invalid_context(self):
        cursor = cursors.encode("context", 10, generations=(1,))
        assert cursors.decode(cursor, "context", generations=(1,)) == 10
        with pytest.raises(OperationError, match="search_cursor_expired") as failure:
            cursors.decode(cursor, "context", generations=(2,))
        assert failure.value.kind is ErrorKind.CONFLICT
        with pytest.raises(OperationError, match="search_cursor_invalid"):
            cursors.decode(cursor, "another-query", generations=(1,))

    def test_expires_a_valid_cursor_after_its_deadline(self, monkeypatch):
        monkeypatch.setattr(cursors.time, "time", lambda: 1000)
        cursor = cursors.encode("context", 10)
        monkeypatch.setattr(cursors.time, "time", lambda: 1901)
        with pytest.raises(OperationError, match="search_cursor_expired"):
            cursors.decode(cursor, "context")

    @pytest.mark.parametrize("cursor", ["", "a" * 513, "foo.bar", "foo.bar.baz"])
    def test_rejects_malformed_or_forged_cursors(self, cursor):
        with pytest.raises(OperationError, match="search_cursor_invalid"):
            cursors.decode(cursor, "context")
